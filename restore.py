import asyncio
import os
import psycopg
import re
import secrets
import shutil
import subprocess
import sys
import zipfile

from core import COMMAND_LINE_ARGS, translations, is_junction, get_password, set_password, SAVED_GAMES, utils
from datetime import datetime
from pathlib import Path
from psycopg import AsyncConnection, sql
from rich.console import Console
from rich.prompt import Prompt, Confirm
from urllib.parse import urlparse, ParseResult

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = translations.get_translation("restore")


class Restore:
    def __init__(self, node: str, config_dir: str, quiet: bool = False):
        self.node = node
        self.config_dir = config_dir
        self.quiet = quiet

    def unzip(self, file: Path, target: Path) -> None:
        with zipfile.ZipFile(file, 'r') as zip_ref:
            for member in zip_ref.infolist():
                dest_name = member.filename
                if not member.is_dir():
                    dest_path = os.path.join(target, dest_name)
                    dest_dir = os.path.dirname(dest_path)
                    os.makedirs(dest_dir, exist_ok=True)
                    with open(dest_path, 'wb') as out_file:
                        shutil.copyfileobj(zip_ref.open(member), out_file)
                else:
                    dest_path = os.path.join(target, dest_name)
                    os.makedirs(dest_path, exist_ok=True)

    async def restore_bot(self, console: Console, backup_file: Path) -> bool:
        if os.path.exists(self.config_dir) and len(os.listdir(self.config_dir)) > 0:
            backup_name = f"config.{datetime.now().strftime('%Y-%m-%d')}"
            console.print(_("[yellow]A configuration directory exists. Renaming to {} ...").format(backup_name))
            if not is_junction(self.config_dir):
                os.rename(self.config_dir, backup_name)
            else:
                # TODO
                console.print(_("[red]Junction for ./config found, aborting."))
                return False
        # unzip
        await asyncio.to_thread(self.unzip, backup_file, Path(os.getcwd()))
        return True

    async def prepare_restore_database(self, console: Console) -> tuple[ParseResult, str, str] | None:
        main_yaml = Path(self.config_dir) / "main.yaml"
        try:
            main = yaml.load(main_yaml.read_text(encoding='utf-8'))
        except FileNotFoundError:
            console.print(_("[red]No main.yaml found, aborting."))
            return None
        nodes_yaml = Path(self.config_dir) / "nodes.yaml"
        try:
            nodes = yaml.load(nodes_yaml.read_text(encoding='utf-8'))
        except FileNotFoundError:
            console.print(_("[red]No nodes.yaml found, aborting."))
            return None
        c_url = main.get('database', {}).get('url')
        l_url = nodes.get(self.node, {}).get('database', {}).get('url')

        if not l_url:
            l_url = c_url

        if not l_url:
            console.print(_("[red]No database configuration found, aborting."))
            return None

        db_url = urlparse(l_url)
        try:
            pg_pwd = get_password("postgres", config_dir=self.config_dir)
        except ValueError:
            if self.quiet:
                raise ValueError("Password of user 'postgres' not stored, run the restore process manually!")
            pg_pwd = Prompt.ask(_("Please enter the master password of your database (user=postgres):"))
            set_password("postgres", pg_pwd, config_dir=self.config_dir)
        try:
            db_pwd = get_password("database", config_dir=self.config_dir)
        except ValueError:
            if db_url.password == 'SECRET':
                db_pwd = secrets.token_urlsafe(8)
            else:
                db_pwd = db_url.password

        return db_url, pg_pwd, db_pwd

    async def restore_database(self, console: Console, backup_file: Path, db_url: ParseResult, pg_pwd: str,
                               db_pwd: str) -> bool:
        conninfo = f"postgresql://postgres:{pg_pwd}@{db_url.hostname}:{db_url.port}/postgres?sslmode=prefer"
        try:
            async with await AsyncConnection.connect(conninfo=conninfo, autocommit=True) as conn:
                # terminate any existing connection to the database
                await conn.execute("""
                        SELECT pg_terminate_backend(pid) 
                        FROM pg_stat_activity 
                        WHERE datname='{}' AND pid != pg_backend_pid();
                    """.format(db_url.path[1:]))
                await conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_url.path[1:])))
                await conn.execute(sql.SQL("DROP USER IF EXISTS {}").format(sql.Identifier(db_url.username)))
                await conn.execute(sql.SQL("CREATE USER {} WITH PASSWORD {}").format(
                    sql.Identifier(db_url.username), sql.Literal(db_pwd)))
                set_password("database", db_pwd, config_dir=self.config_dir)
                await conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(db_url.path[1:]), sql.Identifier(db_url.username)))
                # read the postgres installation directory
                cursor = await conn.execute("""
                    SELECT 
                        version(),
                        setting as data_directory
                    FROM pg_settings 
                    WHERE name = 'data_directory';
                """)
                version, data_directory = await cursor.fetchone()
                install_path = os.path.dirname(data_directory)
                if not os.path.exists(install_path):
                    # TODO: continue here
                    ...

        except psycopg.OperationalError:
            console.print_exception(show_locals=False)
            return False

        def do_restore() -> int:
            os.environ['PGPASSWORD'] = db_pwd
            cmd = os.path.join(install_path, 'bin', 'pg_restore.exe')
            args = [
                '--no-owner',
                '-U', db_url.username,
                '-d', db_url.path[1:],
                '-h', db_url.hostname,
                '-Ft', str(backup_file)
            ]
            try:
                process = subprocess.run(
                    [cmd, *args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                )
                if process.returncode == 1:
                    console.print(
                        _("[yellow]Warnings while restoring the database:\n{}[/]").format(
                            process.stderr.decode('utf-8')))
                return process.returncode
            except Exception:
                console.print_exception(show_locals=False)
                return 2

        console.print("Restoring database ...")
        rc = await asyncio.to_thread(do_restore)
        if rc > 1:
            console.print(_("[red]Failed to restore database.[/]"))
            return False
        return True

    async def restore_instance(self, console: Console, backup_file: Path) -> bool:
        instance_name = re.match(r'^(.+?)_(?=\d{8}_\d{6}\.zip$)', os.path.basename(backup_file)).group(1)
        instance_path = Path(SAVED_GAMES) / instance_name
        # create backup
        if (not self.quiet and os.path.exists(instance_path) and
                not Confirm.ask(_("Instance {} exists. Do you want to overwrite it?").format(instance_name),
                                default=False)):
            return False
        else:
            utils.safe_rmtree(instance_path)
        # unzip
        await asyncio.to_thread(self.unzip, backup_file, instance_path)
        return True

    async def run(self, restore_dir: Path | None = None, *, delete: bool = False) -> int:
        console = Console()

        if not restore_dir:
            try:
                backup_yaml = Path(self.config_dir) / "services" / "backup.yaml"
                backup_config = yaml.load(backup_yaml.read_text(encoding='utf-8'))
                backup_dir = backup_config['target']
            except FileNotFoundError:
                backup_dir = Prompt.ask(_("Please enter your backup directory:"))
            if not os.path.exists(backup_dir):
                console.print(_("[red]No backup directory found![/]"))
                return -1

            if not self.quiet and not Confirm.ask(_("Are you sure you want to restore from backup?"), default=False):
                return -1

            console.print(_("[green]Restoring from backup...[/]"))
            console.print(_("[green]Backup directory: {}/[/]".format(backup_dir)))

            backup_dirs = []
            for file in Path(backup_dir).glob('**/*'):
                if os.path.isdir(file) and file.name.startswith(self.node.lower()):
                    backup_dirs.append(file.name)

            if not backup_dirs:
                console.print(_("[green]No backup found for node {}[/]".format(self.node)))
                return -1

            backup_dirs = sorted(backup_dirs, reverse=True)
            if len(backup_dirs) > 1:
                restore_point = Prompt.ask(_("Please enter the version you want to restore:"), choices=backup_dirs,
                                            default=backup_dirs[0])
            else:
                restore_point = backup_dirs[0]

            restore_dir = Path(backup_dir) / restore_point

        rc = 0
        for file in Path(restore_dir).glob('**/*'):
            if file.name.startswith('bot_'):
                if not self.quiet and not Confirm.ask(_("Do you want to restore the DCSServerBot configuration?"),
                                                      default=False):
                    continue
                try:
                    if await self.restore_bot(console, file):
                        console.print(_("[green]DCSServerBot configuration restored.[/]"))
                        rc = -1
                    else:
                        console.print(_("[yellow]Could not restore DCSServerBot configuration.[/]"))
                except Exception:
                    console.print_exception(show_locals=True)
            elif file.name.startswith('db_'):
                if not self.quiet and not Confirm.ask(_("Do you want to restore the Database?"), default=False):
                    continue
                data = await self.prepare_restore_database(console)
                if data:
                    db_url, pg_pwd, db_pwd = data
                    await self.restore_database(console, file, db_url, pg_pwd, db_pwd)
                    console.print(_("[green]Database configuration restored.[/]"))
                    rc = -1
            else:
                instance_name = re.match(r'^(.+?)_(?=\d{8}_\d{6}\.zip$)', file.name).group(1)
                if not self.quiet and not Confirm.ask(_("Do you want to restore instance {}?").format(instance_name),
                                                      default=False):
                    continue
                if await self.restore_instance(console, file):
                    console.print(_("[green]Instance {} restored.[/]").format(os.path.basename(file.name)))
                    rc = -1
            if delete:
                file.unlink()
        if rc:
            console.print(_("[green]All data restored.[/]"))
        else:
            console.print(_("[yellow]No data restored.[/]"))
        return rc


if __name__ == '__main__':
    args = COMMAND_LINE_ARGS
    if sys.platform == "win32" and sys.version_info < (3, 14):
        # set the asyncio event loop policy
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if sys.version_info >= (3, 14):
        import selectors

        rc = asyncio.run(
            Restore(args.node, args.config).run(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(Restore(args.node, args.config).run())
