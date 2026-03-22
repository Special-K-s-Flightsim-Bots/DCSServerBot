import json

from core import EventListener, event, Server


class BattlegroundEventListener(EventListener):

    @event(name="sendMission")
    async def sendMission(self, server: Server, data: dict) -> None:
        async with self.apool.connection() as conn:
            await conn.execute("INSERT INTO bg_missions(server_name, data) VALUES (%s, %s)",
                               (server.name, json.dumps(data)))
