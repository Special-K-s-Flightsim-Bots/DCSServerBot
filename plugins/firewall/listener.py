from core import EventListener, event, Server, ServiceRegistry
from services.firewall.service import FirewallService


class FirewallListener(EventListener):
    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, _: dict) -> None:
        service = ServiceRegistry.get(FirewallService)
        await service.change_firewall_rule(server, False)

    @event(name="onMissionInitDone")
    async def onMissionInitDone(self, server: Server, _: dict) -> None:
        """
        Use this in your mission to enable the firewall again:

        local msg = {
            command = "onMissionInitDone"
        }
        dcsbot.sendBotTable(msg)
        """
        service = ServiceRegistry.get(FirewallService)
        await service.change_firewall_rule(server, True)
