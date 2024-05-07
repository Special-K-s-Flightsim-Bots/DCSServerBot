from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

import argparse
import platform
import psycopg
import shlex
import os
import subprocess
import sys

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


def _recover_db_user(url: str) -> tuple[str, str]:
    _url = urlparse(url)
    username = _url.username
    password = _url.password
    database = _url.path.strip('/')
    print(f"Recovering user {username} on database {database} ...")
    with psycopg.connect(url, autocommit=True) as conn:
        with suppress(psycopg.Error):
            conn.execute(f"CREATE USER {username} WITH ENCRYPTED PASSWORD '{password}'")
    print("User recovered.")
    return username, database


def recover_database(config: dict, filename: str):
    job_config = config.get('backups', {}).get('database')
    cmd = os.path.join(os.path.expandvars(job_config['path']), "pg_restore.exe")
    if not os.path.exists(cmd):
        raise FileNotFoundError(cmd)
    user, database = _recover_db_user(config['database']['url'])
    args = shlex.split(f'--no-owner --role {user} -c -C -U postgres -F t -d "{database}" "{filename}"')
    os.environ['PGPASSWORD'] = job_config['password']
    print("Recovering database...")
    process = subprocess.run([os.path.basename(cmd), *args], executable=cmd)
    #                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rc = process.returncode
    if rc == 0:
        print("Restore of database complete.")
        return True
    else:
        print(f"Restore of database failed. Code: {rc}")
        return False


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='recover.py', description="DCSServerBot Recovery Tool",
                                     epilog='If unsure about the parameters, please check the documentation.')
    parser.add_argument('-n', '--node', help='Node name', default=platform.node())
    parser.add_argument('-f', '--file', help='Path to backup', default=None)
    args = parser.parse_args()
    try:
        config = yaml.load(Path('config/services/backup.yaml').read_text(encoding='utf-8'))
        main = yaml.load(Path('config/main.yaml').read_text(encoding='utf-8'))
        nodes = yaml.load(Path('config/nodes.yaml').read_text(encoding='utf-8'))
        config['database'] = {
            "url": main.get("database", nodes[args.node].get('database'))['url']
        }
        recover_database(config, args.file)
    except FileNotFoundError as ex:
        print(f"{ex.filename} not found. Please install the bot first or recover a valid configuration.")
    subprocess.Popen([sys.executable, 'run.py', '-n', args.node])
