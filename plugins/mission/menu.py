import os

from core import EventListener, Server, DEFAULT_TAG, Player
from pathlib import Path
from typing import Optional

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


def read_menu_config(listener: EventListener, server: Server) -> Optional[dict]:
    menu_file = os.path.join(listener.node.config_dir, 'menus.yaml')
    if os.path.exists(menu_file):
        menu = yaml.load(Path(menu_file).read_text(encoding='utf-8'))
        if server.instance.name in menu:
            return menu[server.instance.name]
        else:
            return menu.get(DEFAULT_TAG)


def filter_menu_items(menu: dict, usable_commands: list[str]) -> dict:
    filtered_menu = {}

    for key, value in menu.items():
        if isinstance(value, dict):
            # If this is a command block with a 'subcommand'
            if "subcommand" in value:
                if value["subcommand"] in usable_commands:
                    filtered_menu[key] = value
            else:
                # If it's a nested menu, recursively filter it
                filtered_submenu = filter_menu_items(value, usable_commands)
                if filtered_submenu:  # Only include non-empty submenus
                    filtered_menu[key] = filtered_submenu

    return filtered_menu


async def filter_menu(listener: EventListener, menu: dict, server: Server, player: Player) -> Optional[dict]:
    if not menu:
        return None
    usable_commands = []
    for li in listener.bot.eventListeners:
        for cmd in li.chat_commands:
            if await listener.can_run(cmd, server, player):
                usable_commands.append(cmd.name)

    return filter_menu_items(menu, usable_commands)
