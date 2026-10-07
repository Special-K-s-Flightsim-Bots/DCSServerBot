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

VERSION_TRAILER_FILE = "version.sha"


def install_requirements() -> subprocess.CompletedProcess:
    """
    Synchronize the virtual environment with requirements.txt using uv.

    ``uv pip sync`` installs exactly the pinned versions and removes everything else, the same
    contract the previous pip-sync call had. uv links packages out of a shared cache instead of
    copying them, so several bots on one machine do not each keep a full copy of the
    dependencies - provided the cache and the environment share a filesystem.
    """
    if not shutil.which('uv'):
        print("uv was not found in your PATH - DCSServerBot needs it to install its dependencies.")
        print("Install it from https://docs.astral.sh/uv/ and run the update again.")
        return subprocess.CompletedProcess(args=['uv'], returncode=-1)

    cmd = ['uv', 'pip', 'sync', '--python', sys.executable, 'requirements.txt']
    if os.path.exists('requirements.local'):
        cmd.append('requirements.local')
    return subprocess.run(cmd)


#: Exit code for the launcher that started us: "a fresh launcher is already running, so do not
#: restart anything - just stop". The launchers treat it as a clean exit.
ALREADY_RESTARTED = -5


def relaunch_launcher() -> bool:
    """
    Start the bot again, in a new process, after an update.

    The launcher that started this script is a batch or shell file that the update has just
    rewritten on disk, and a running script cannot reliably read its own remaining lines once that
    has happened - cmd.exe reads batch files by byte offset, and a shell keeps reading the file it
    already has open. This process is loaded and cannot be corrupted that way, so it performs the
    hand-over itself, and the launcher is told to stop instead of continuing.
    """
    launcher = 'run.cmd' if sys.platform == 'win32' else './run.sh'
    if not os.path.exists(launcher):
        print(f"  => {launcher} was not found - start DCSServerBot yourself.")
        return False

    args = [arg for arg in sys.argv[1:] if arg not in ('-r', '--no-restart', '-i', '--install')]
    if sys.platform == 'win32':
        # A new console, so the fresh launcher owns its window and does not die with this one.
        cmd = [os.environ.get('COMSPEC', 'cmd.exe'), '/c', launcher, *args]
        try:
            subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        except OSError as ex:
            print(f"  => Could not start {launcher} ({ex}) - start DCSServerBot yourself.")
            return False
    else:
        # Own session, so it survives this process and the terminal it was started from.
        try:
            subprocess.Popen([launcher, *args], start_new_session=True)
        except OSError as ex:
            print(f"  => Could not start {launcher} ({ex}) - start DCSServerBot yourself.")
            return False
    print("  => DCSServerBot is starting again.")
    return True


def do_update_git() -> int | None:
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
                # Get the name of the subdirectory (e.g., 'fh_report')
                sub_dir = path_obj.relative_to(special_dir).parts[0]

                # If this subdirectory doesn't exist in the ZIP, it's user-added content.
                # We skip the deletion of the directory and all elements below it.
                if not (Path(extracted_folder) / special_dir / sub_dir).is_dir():
                    handled = True
                    break

                # If it IS an official directory, we only delete the file if it's missing from the ZIP
                if not (Path(extracted_folder) / path_obj).exists():
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


def get_last_successful_sha() -> str | None:
    """Reads the SHA from the version trailer file."""
    if os.path.exists(VERSION_TRAILER_FILE):
        with open(VERSION_TRAILER_FILE, 'r') as f:
            return f.read().strip()
    return None


def set_last_successful_sha(new_sha: str):
    """Writes the current successful SHA to the version trailer file."""
    with open(VERSION_TRAILER_FILE, 'w') as f:
        f.write(new_sha)
    print(f"  [Version Tracker] Successfully updated state marker to {new_sha[:7]}.")


def do_update_github() -> int:
    # 1. Check for Releases
    response = requests.get(f"https://api.github.com/repos/Special-K-s-Flightsim-Bots/DCSServerBot/releases")
    latest_release = None
    if response.status_code == 200 and response.json():
        latest_release = response.json()[0]

    current_version = re.sub('^v', '', __version__)
    update_url = None
    new_tag = None
    new_sha = None

    if latest_release:
        latest_version = re.sub('^v', '', latest_release["tag_name"])
        if version.parse(latest_version) > version.parse(current_version):
            update_url = latest_release['zipball_url']
            new_tag = latest_version

    # 2. Check master branch if no release update was found
    if not update_url:
        branch_res = requests.get(f"https://api.github.com/repos/Special-K-s-Flightsim-Bots/DCSServerBot/branches/master")
        if branch_res.status_code == 200:
            master_sha = branch_res.json()['commit']['sha']
            last_sha = get_last_successful_sha()
            if master_sha != last_sha:
                print(f"- New changes detected on master branch ({master_sha[:7]}).")
                update_url = "https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/zipball/master"
                new_sha = master_sha
                new_tag = f"master ({master_sha[:7]})"

    if update_url:
        print(f'- Updating myself to {new_tag}...')
        zip_res = requests.get(update_url)

        with io.BytesIO() as bytes_io:
            bytes_io.write(zip_res.content)
            bytes_io.seek(0)

            with zipfile.ZipFile(bytes_io) as zip_ref:
                with tempfile.TemporaryDirectory() as temp_dir:
                    zip_ref.extractall(temp_dir)
                    extracted_folder = os.path.join(temp_dir, os.listdir(temp_dir)[0])

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

                            # Exchange files only if they differ (shutil.copy2 preserves metadata)
                            shutil.copy2(old_file_path, new_file_path)

            if new_sha:
                set_last_successful_sha(new_sha)

            rc = install_requirements()
            if rc.returncode:
                print('  => Autoupdate failed!')
                return -1
        print(f'  => DCSServerBot updated to {new_tag}.')
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
                print(f'uv pip sync --python "{sys.executable}" requirements.txt')
                exit(-2)
        else:
            rc = do_update_git()
    except ImportError:
        rc = do_update_github()
    if args.no_restart:
        exit(-2)
    else:
        # Hand over to a fresh launcher rather than returning to the one that started us: that
        # launcher has just been rewritten on disk by this update and can no longer read its own
        # remaining lines. relaunch_launcher does it here, where the code is already loaded.
        relaunch_launcher()
        exit(ALREADY_RESTARTED)
