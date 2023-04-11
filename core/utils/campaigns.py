from __future__ import annotations
from contextlib import closing
from typing import TYPE_CHECKING, Tuple, Any

if TYPE_CHECKING:
    from core import Server


def get_running_campaign(server: Server) -> Tuple[Any, Any]:
    with server.pool.connection() as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('SELECT id, name FROM campaigns c, campaigns_servers s WHERE c.id = s.campaign_id AND '
                           's.server_name = %s AND NOW() BETWEEN c.start AND COALESCE(c.stop, NOW())', (server.name,))
            if cursor.rowcount == 1:
                row = cursor.fetchone()
                return row[0], row[1]
            else:
                return None, None


def get_all_campaigns(self) -> list[str]:
    with self.pool.connection() as conn:
        return [x[0] for x in conn.execute('SELECT name FROM campaigns').fetchall()]
