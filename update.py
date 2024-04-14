import argparse
import io
import os
import platform
import re
import requests
import shutil
import subprocess
import sys
import tempfile
import zipfile

from contextlib import closing
from typing import Iterable, Optional

from version import __version__


def do_update_git(delete: Optional[bool]) -> int:
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
                        rc = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
                        if rc.returncode:
                            print('  => Autoupdate failed!')
                            print('     Please run update.cmd manually.')
                            return -1
                except git.exc.InvalidGitRepositoryError:
                    return do_update_github(delete)
                except git.exc.GitCommandError:
                    print('  => Autoupdate failed!')
                    print('     Please revert back the changes in these files:')
                    for item in repo.index.diff(None):
                        print(f'     ./{item.a_path}')
                    return -1
            else:
                print('- No update found for DCSServerBot.')
                return 0
    except git.exc.InvalidGitRepositoryError:
        return do_update_github(delete)


def list_all_files(path):
    # Returns a list of all file paths in the given directory and its subdirectories.
    # The paths are in the form of relative paths from the given root directory.
    file_paths = []
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            relative_path = os.path.relpath(full_path, path)
            file_paths.append(relative_path)
    return file_paths


def cleanup_local_files(to_delete_set: Iterable):
    # Exclude directories from deletion
    exclude_dirs = {'__pycache__', '.git', 'config', 'reports', 'sounds', 'services', 'extensions', 'plugins', 'logs'}
    to_delete_set = {f for f in to_delete_set if not any(excluded_dir in f for excluded_dir in exclude_dirs)}

    # Delete each old file that is not in the updated directory.
    for relative_path in to_delete_set:
        full_path = os.path.join(os.getcwd(), relative_path)
        if os.path.isfile(full_path):
            print(f"  => Deleting {full_path}")
            os.remove(full_path)


def do_update_github(delete: Optional[bool] = False) -> int:
    response = requests.get(f"https://api.github.com/repos/Special-K-s-Flightsim-Bots/DCSServerBot/releases")
    current_version = __version__
    latest_version = response.json()[0]["tag_name"]

    # Comparing SemVer taking in account that there could be a "v" prefix
    if re.sub('^v', '', latest_version) > re.sub('^v', '', current_version):
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

                    if delete:
                        # check for necessary file deletions
                        old_files_set = set(list_all_files(os.getcwd()))
                        new_files_set = set(list_all_files(extracted_folder))
                        to_delete_set = old_files_set - new_files_set
                        cleanup_local_files(to_delete_set)

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
            rc = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
            if rc.returncode:
                print('  => Autoupdate failed!')
                return -1
        print('  => DCSServerBot updated to the latest version.')
    else:
        print('- No update found for DCSServerBot.')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='update.py', description="Welcome to DCSServerBot!",
                                     epilog='If unsure about the parameters, please check the documentation.')
    parser.add_argument('-n', '--node', help='Node name', default=platform.node())
    parser.add_argument('-d', '--delete', action='store_true', help='remove obsolete local files')
    parser.add_argument('-r', '--no-restart', action='store_true', default=False,
                        help="don't start DCSServerBot after the update")
    args = parser.parse_args()
    try:
        rc = do_update_git(args.delete)
    except ImportError:
        rc = do_update_github(args.delete)
    if not args.no_restart:
        os.execv(sys.executable, [os.path.basename(sys.executable), 'run.py', '--noupdate'] + sys.argv[1:])
