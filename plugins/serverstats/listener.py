import math

from core import EventListener, Plugin, event, Server


class ServerStatsListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.fps = {}

    @event(name="perfmon")
    async def perfmon(self, server: Server, data: dict):
        self.fps[server.name] = data['fps']

    @event(name="serverLoad")
    async def serverLoad(self, server: Server, data: dict):
        if server.name not in self.fps:
            return
        ping = (self.bot.latency * 1000) if not math.isinf(self.bot.latency) else -1
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                INSERT INTO serverstats (server_name, node, mission_id, users, status, cpu, mem_total, 
                                         mem_ram, read_bytes, write_bytes, bytes_sent, bytes_recv, fps, ping) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (server.name, server.node.name, server.mission_id, len(server.get_active_players()),
                  server.status.name, data['cpu'], data['mem_total'], data['mem_ram'], data['read_bytes'],
                  data['write_bytes'], data['bytes_sent'], data['bytes_recv'], self.fps[server.name], ping))
