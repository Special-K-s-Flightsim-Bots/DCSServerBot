# listener.py
import platform
import psycopg2
from contextlib import closing
from core import utils, EventListener, Status


class AdminEventListener(EventListener):

    def updateBans(self, data=None):
        banlist = []
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT ucid FROM bans')
                banlist = [dict(row) for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        if data is not None:
            servers = [self.globals[data['server_name']]]
        else:
            servers = self.globals.values()
        for server in servers:
            for ban in banlist:
                self.bot.sendtoDCS(server, {"command": "ban", "ucid": ban['ucid'], "channel": server['status_channel']})

    async def registerDCSServer(self, data):
        installations = utils.findDCSInstallations(data['server_name'])
        if not installations:
            self.log.error(f"Server {data['server_name']} not found in dcsserverbot.ini. Please add a "
                           f"configuration for it!")
            return
        self.log.debug('  => Registering DCS-Server ' + data['server_name'])
        # check for protocol incompatibilities
        if data['hook_version'] != self.bot.version:
            self.log.error(
                'Server {} has wrong Hook version installed. Please update lua files and restart server. Registration '
                'ignored.'.format(
                    data['server_name']))
            return
        if data['status_channel'].isnumeric() is True:
            sql = 'INSERT INTO servers (server_name, agent_host, host, port, chat_channel, status_channel, ' \
                  'admin_channel) VALUES(%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (server_name) DO UPDATE SET ' \
                  'agent_host=%s, host=%s, port=%s, chat_channel=%s, status_channel=%s, admin_channel=%s '
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                    cursor.execute(sql, (data['server_name'], platform.node(), data['host'], data['port'],
                                         data['chat_channel'], data['status_channel'], data['admin_channel'],
                                         platform.node(), data['host'], data['port'], data['chat_channel'],
                                         data['status_channel'], data['admin_channel']))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)
            if data['server_name'] in self.globals:
                self.globals[data['server_name']] = data | self.globals[data['server_name']]
            else:
                self.globals[data['server_name']] = data
            server = self.globals[data['server_name']]
            if data['channel'].startswith('sync-'):
                server['status'] = Status.PAUSED if 'pause' in data and data['pause'] is True else Status.RUNNING
            else:
                server['status'] = Status.LOADING
            # Store server configuration
            server['installation'] = installations[0]
            self.updateBans(data)
        else:
            self.log.error(
                'Configuration mismatch. Please check channel settings in dcsserverbot.ini for server {}!'.format(
                    data['server_name']))

    async def ban(self, data):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                               (data['ucid'], 'DCSServerBot', data['reason']))
                for server in self.globals.values():
                    self.bot.sendtoDCS(server, {
                        "command": "ban",
                        "ucid": data['ucid'],
                        "reason": data['reason']
                    })
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
