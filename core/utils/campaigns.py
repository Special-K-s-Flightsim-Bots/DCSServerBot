from __future__ import annotations
import psycopg2
from contextlib import closing
from typing import TYPE_CHECKING, Tuple, Any

if TYPE_CHECKING:
    from core import Server


def get_running_campaign(server: Server) -> Tuple[Any, Any]:
    conn = server.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute('SELECT id, name FROM campaigns c, campaigns_servers s WHERE c.id = s.campaign_id AND '
                           's.server_name = %s AND NOW() BETWEEN c.start AND COALESCE(c.stop, NOW())', (server.name,))
            if cursor.rowcount == 1:
                row = cursor.fetchone()
                return row[0], row[1]
            else:
                return None, None
    except (Exception, psycopg2.DatabaseError) as error:
        server.log.exception(error)
    finally:
        server.pool.putconn(conn)


def get_all_campaigns(self) -> list[str]:
    conn = self.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute('SELECT name FROM campaigns')
            return [x[0] for x in cursor.fetchall()]
    except (Exception, psycopg2.DatabaseError) as error:
        self.log.exception(error)
    finally:
        self.pool.putconn(conn)
