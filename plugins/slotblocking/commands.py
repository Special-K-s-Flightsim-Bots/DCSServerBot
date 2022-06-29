from copy import deepcopy
from core import DCSServerBot, Plugin, PluginRequiredError, Server
from typing import Optional
from .listener import SlotBlockingListener


class SlotBlocking(Plugin):

    def get_config(self, server: Server) -> Optional[dict]:
        if server.name not in self._config:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server.installation == element['installation']) or \
                                ('server_name' in element and server.name == element['server_name']):
                            specific = deepcopy(element)
                    else:
                        default = deepcopy(element)
                if default and not specific:
                    self._config[server.name] = default
                elif specific and not default:
                    self._config[server.name] = specific
                elif default and specific:
                    merged = {}
                    if 'use_reservations' in specific:
                        merged['use_reservations'] = specific['use_reservations']
                    elif 'use_reservations' in default:
                        merged['use_reservations'] = default['use_reservations']
                    if 'restricted' in default and 'restricted' not in specific:
                        merged['restricted'] = default['restricted']
                    elif 'restricted' not in default and 'restricted' in specific:
                        merged['restricted'] = specific['restricted']
                    elif 'restricted' in default and 'restricted' in specific:
                        merged['restricted'] = default['restricted'] + specific['restricted']
                    self._config[server.name] = merged
                    server.sendtoDCS({
                        'command': 'loadParams',
                        'plugin': self.plugin_name,
                        'params': self._config[server.name]
                    })
            else:
                return None
        return self._config[server.name] if server.name in self._config else None


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if 'creditsystem' not in bot.plugins:
        raise PluginRequiredError('creditsystem')
    bot.add_cog(SlotBlocking(bot, SlotBlockingListener))
