import asyncio
import subprocess

from core import ServiceRegistry, Service, Server, proxy


@ServiceRegistry.register(plugin='firewall')
class FirewallService(Service):

    def __init__(self, node):
        super().__init__(node=node, name="Firewall")

    @proxy
    async def change_firewall_rule(self, server: Server, enable: bool) -> None:
        dcs_port = int(server.settings.get('port', 10308))

        def run_subprocess():
            subprocess.run('netsh advfirewall firewall set rule name="{}" new enable={}'.format(
                f"DCS_{dcs_port}", 'yes' if enable else 'no'), shell=True)
        await asyncio.to_thread(run_subprocess)
