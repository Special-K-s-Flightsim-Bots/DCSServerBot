import io
import os
import re
import requests
import shutil
import subprocess
import sys
import tempfile
import zipfile

from contextlib import closing
from core import utils, COMMAND_LINE_ARGS
from packaging import version
from pathlib import Path
from typing import Iterable
from version import __version__


def install_requirements() -> subprocess.CompletedProcess:
    """
    Use pip-sync to synchronize the virtual environment with requirements.txt.
    This ensures exact package versions are installed and removes packages not in requirements.txt.
    """

    # First, ensure pip-tools are installed
    subprocess.run([
        sys.executable,
        '-m', 'pip', 'install', 'pip-tools'
    ])

    # Then run pip-sync to synchronize the environment with requirements.txt
    cmd = [
        sys.executable,
#        '-m', 'piptools', 'sync', 'requirements.txt' # temporary disabled due to pip / pip-sync mismatch
        '-m', 'pip', 'install', '-r', 'requirements.txt'
    ]
    if os.path.exists('requirements.local'):
        cmd.append('requirements.local')
    return subprocess.run(cmd)


def do_update_git() -> int:
    import git

    try:
        with closing(git.Repo('.')) as repo:
            current_hash = repo.head.commit.hexsha
            origin = repo.remotes.origin
            origin.fetch()
            new_hash = origin.refs[repo.active_branch.name].object.hexsha
            if new_hash != current_hash:
                modules = False
                print('- Updating myself...')
                diff = repo.head.commit.diff(new_hash)
                for d in diff:
                    if d.b_path == 'requirements.txt':
                        modules = True
                try:
                    repo.remote().pull(repo.active_branch)
                    print('  => DCSServerBot updated to the latest version.')
                    if modules:
                        print('  => requirements.txt has changed. Installing missing modules...')
                        rc = install_requirements()
                        if rc.returncode:
                            print('  => Autoupdate failed!')
                            print('     Please run update.cmd manually.')
                            return -1
                except git.exc.InvalidGitRepositoryError:
                    return do_update_github()
                except git.exc.GitCommandError as ex:
                    print('  => Autoupdate failed!')
                    changed_files = set()
                    # Add staged changes
                    for item in repo.index.diff(None):
                        changed_files.add(item.a_path)
                    # Add unstaged changes
                    for item in repo.head.commit.diff(None):
                        changed_files.add(item.a_path)
                    if changed_files:
                        print('     Please revert back the changes in these files:')
                        for path in changed_files:
                            print(f'     ./{path}')
                    else:
                        print(ex)
                    return -1
            else:
                print('- No update found for DCSServerBot.')
                return 0
    except git.exc.InvalidGitRepositoryError:
        return do_update_github()


def cleanup_local_files(to_delete_set: Iterable, extracted_folder: str):
    # Exclude directories from deletion
    root_exclude_dirs = {'__pycache__', '.git', 'config', 'reports', 'logs'}
    special_dirs = {
        Path('plugins'): 'plugins directory',
        Path('services'): 'services directory'
    }

    to_delete_set = {f for f in to_delete_set if not any(excluded_dir in f for excluded_dir in root_exclude_dirs)}

    # Delete each old file not in the updated directory.
    for relative_path in to_delete_set:
        full_path = os.path.join(os.getcwd(), relative_path)
        path_obj = Path(relative_path)

        # Handle special directories (plugins, services)
        handled = False
        for special_dir, dir_description in special_dirs.items():
            if special_dir in path_obj.parents:
                # Delete only subdirectories inside special dirs that exist in the zip
                sub_path = relative_path.replace(f"{special_dir}/", "")
                if not os.path.isdir(os.path.join(extracted_folder, special_dir, sub_path)):
                    print(f"  => Deleting {full_path} (from {dir_description})")
                    delete_path(full_path)
                handled = True
                break

        if handled:
            continue

        # Delete the rest
        print(f"  => {'Removing directory' if os.path.isdir(full_path) else 'Deleting'} {full_path}")
        delete_path(full_path)


def delete_path(path: str):
    """Helper function to delete a file or directory"""
    if os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def do_update_github() -> int:
    response = requests.get(f"https://api.github.com/repos/Special-K-s-Flightsim-Bots/DCSServerBot/releases")
    if response.status_code != 200:
        print(f'  => Autoupdate failed: {response.reason} ({response.status_code})')
        return -2
    current_version = re.sub('^v', '', __version__)
    latest_version = re.sub('^v', '', response.json()[0]["tag_name"])

    if version.parse(latest_version) > version.parse(current_version):
        print('- Updating myself...')

        zip_url = response.json()[0]['zipball_url']
        zip_res = requests.get(zip_url)

        with io.BytesIO() as bytes_io:
            bytes_io.write(zip_res.content)
            bytes_io.seek(0)  # reset file pointer to beginning

            with zipfile.ZipFile(bytes_io) as zip_ref:
                with tempfile.TemporaryDirectory() as temp_dir:
                    zip_ref.extractall(temp_dir)  # extract to temporary directory

                    extracted_folder = os.path.join(temp_dir, os.listdir(temp_dir)[0])  # there is one folder

                    # check for necessary file deletions
                    old_files_set = set(utils.list_all_files(os.getcwd()))
                    new_files_set = set(utils.list_all_files(extracted_folder))
                    to_delete_set = old_files_set - new_files_set
                    cleanup_local_files(to_delete_set, extracted_folder)

                    for root, dirs, files in os.walk(extracted_folder):
                        for file in files:
                            old_file_path = os.path.join(root, file)
                            relative_path = os.path.relpath(old_file_path, extracted_folder)
                            new_file_path = os.path.join(os.getcwd(), relative_path)

                            # make the necessary directories
                            new_file_dir = os.path.dirname(new_file_path)
                            os.makedirs(new_file_dir, exist_ok=True)

                            # move file
                            shutil.copy2(old_file_path, new_file_path)
            rc = install_requirements()
            if rc.returncode:
                print('  => Autoupdate failed!')
                return -1
        print(f'  => DCSServerBot updated to the latest version {latest_version}.')
    else:
        print('- No update found for DCSServerBot.')
    return 0


if __name__ == '__main__':
    # get the command line args from core
    args = COMMAND_LINE_ARGS
    try:
        if args.install:
            rc = install_requirements()
            if rc.returncode:
                print("Unable to install or update 'requirements.txt'. Please try installing it manually.")
                print("To do so, open 'cmd.exe' in the DCSServerBot installation directory, and type:")
                print('"%USERPROFILE%\\.dcssb\\Script\\pip" install --prefer-binary -r requirements.txt')
                exit(-2)
        else:
            rc = do_update_git()
    except ImportError:
        rc = do_update_github()
    if args.no_restart:
        exit(-2)
    else:
        exit(-1)
