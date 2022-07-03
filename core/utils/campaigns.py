from __future__ import annotations
import psycopg2
from contextlib import closing
from typing import Optional, TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from core import Server


def get_running_campaign(server: Server) -> Optional[str]:
    conn = server.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute('SELECT name FROM campaigns WHERE server_name = %s AND NOW() BETWEEN start AND COALESCE('
                           'stop, NOW())', (server.name,))
            if cursor.rowcount == 1:
                return cursor.fetchone()[0]
            else:
                return None
    except (Exception, psycopg2.DatabaseError) as error:
        server.log.exception(error)
    finally:
        server.pool.putconn(conn)


def get_running_campaigns(self) -> list[Tuple[str, str]]:
    conn = self.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute('SELECT name, server_name FROM campaigns WHERE NOW() BETWEEN start AND COALESCE(stop, NOW())')
            return [(x[0], x[1]) for x in cursor.fetchall()]
    except (Exception, psycopg2.DatabaseError) as error:
        self.log.exception(error)
    finally:
        self.pool.putconn(conn)


def get_all_campaigns(self) -> list[Tuple[str, str]]:
    conn = self.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute('SELECT DISTINCT name, server_name FROM campaigns')
            return [(x[0], x[1]) for x in cursor.fetchall()]
    except (Exception, psycopg2.DatabaseError) as error:
        self.log.exception(error)
    finally:
        self.pool.putconn(conn)


def get_servers_for_campaign(self, campaign: str) -> list[str]:
    conn = self.pool.getconn()
    try:
        with closing(conn.cursor()) as cursor:
            cursor.execute('SELECT DISTINCT server_name FROM campaigns WHERE name ILIKE %s', (campaign,))
            return [x[0] for x in cursor.fetchall()]
    except (Exception, psycopg2.DatabaseError) as error:
        self.log.exception(error)
    finally:
        self.pool.putconn(conn)
