import logging
import os

from core import EventListener, Server, DEFAULT_TAG, Player, utils
from pathlib import Path
from pykwalify.core import Core
from pykwalify.errors import SchemaError
from typing import Optional

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError, YAMLError
yaml = YAML()

logger = logging.getLogger(__name__)


def read_menu_config(listener: EventListener, server: Server) -> Optional[dict]:
    menu_file = os.path.join(listener.node.config_dir, 'menus.yaml')
    if os.path.exists(menu_file):
        try:
            c = Core(source_file=menu_file, schema_files=['schemas/menus_schema.yaml'], file_encoding='utf-8',
                     extensions=['core/utils/validators.py'])
            try:
                c.validate(raise_exception=True)
            except SchemaError as ex:
                listener.log.warning(f'Error while parsing {menu_file}:\n{ex}')

            menu = yaml.load(Path(menu_file).read_text(encoding='utf-8'))
            if server.instance.name in menu:
                return menu[server.instance.name]
            else:
                return menu.get(DEFAULT_TAG)
        except MarkedYAMLError as ex:
            raise YAMLError(menu_file, ex)


def filter_menu_items(menu: list, usable_commands: list[str], player: Player) -> list:
    def is_valid_menu_item(menu_value: dict) -> bool:
        if 'command' not in menu_value:
            logger.error(f"menus.yaml: illegal block {menu_value}")
            return False
        if 'subcommand' in menu_value and menu_value['subcommand'] not in usable_commands:
            return False
        if 'ucid' in menu_value and player.ucid not in menu_value['ucid']:
            return False
        if 'discord' in menu_value and not utils.check_roles(menu_value['discord'], player.member):
            return False
        return True

    filtered_menu = []

    for item in menu:
        for key, value in item.items():
            if isinstance(value, list):
                filtered_menu.append({key: filter_menu_items(value, usable_commands, player)})
            elif isinstance(value, dict) and is_valid_menu_item(value):
                filtered_menu.append({key: value})

    return filtered_menu


async def filter_menu(listener: EventListener, menu: list, server: Server, player: Player) -> Optional[list]:
    if not menu:
        return None
    usable_commands = []
    for li in listener.bot.eventListeners:
        for cmd in li.chat_commands:
            if await listener.can_run(cmd, server, player):
                usable_commands.append(cmd.name)

    return filter_menu_items(menu, usable_commands, player)
