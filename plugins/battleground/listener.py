import json

from core import EventListener, event, Server

class BattlegroundEventListener(EventListener): 
    @event(name="sendMission")
    async def sendMission(self, server: Server, data: dict) -> None:
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO bg_missions(node, data) 
                    VALUES (%s, %s)
                """, (server.name, json.dumps(data))) 