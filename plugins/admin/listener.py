# listener.py
import platform
import psycopg2
from core import utils, DCSServerBot, EventListener
from contextlib import closing


class AdminEventListener(EventListener):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)

    async def registerDCSServer(self, data):
        self.log.debug('Registering DCS-Server ' + data['server_name'])
        # check for protocol incompatibilities
        if data['hook_version'] != self.bot.version:
            self.log.error(
                'Server {} has wrong Hook version installed. Please update lua files and restart server. Registration '
                'ignored.'.format(
                    data['server_name']))
            return
        if data['status_channel'].isnumeric() is True:
            SQL_INSERT = 'INSERT INTO servers (server_name, agent_host, host, port, chat_channel, status_channel, ' \
                         'admin_channel) VALUES(%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (server_name) DO UPDATE SET ' \
                         'agent_host=%s, host=%s, port=%s, chat_channel=%s, status_channel=%s, admin_channel=%s '
            SQL_SELECT = 'SELECT server_name, host, port, chat_channel, status_channel, admin_channel, \'Unknown\' as ' \
                         'status FROM servers WHERE server_name = %s '
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                    cursor.execute(SQL_INSERT, (data['server_name'], platform.node(), data['host'], data['port'],
                                                data['chat_channel'], data['status_channel'], data['admin_channel'],
                                                platform.node(), data['host'], data['port'],
                                                data['chat_channel'], data['status_channel'], data['admin_channel']))
                    cursor.execute(SQL_SELECT, (data['server_name'],))
                    server = self.bot.DCSServers[data['server_name']] = dict(cursor.fetchone())
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)
            # Store server configuration
            server['installation'] = utils.findDCSInstallations(data['server_name'])[0]
            server['dcs_version'] = data['dcs_version']
            server['serverSettings'] = data['serverSettings']
            server['options'] = data['options']
            if 'SRSSettings' in data:
                server['SRSSettings'] = data['SRSSettings']
            if 'lotAtcSettings' in data:
                server['lotAtcSettings'] = data['lotAtcSettings']
            self.updateBans(data)
        else:
            self.log.error(
                'Configuration mismatch. Please check channel settings in dcsserverbot.ini for server {}!'.format(
                    data['server_name']))

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
            servers = [self.bot.DCSServers[data['server_name']]]
        else:
            servers = self.bot.DCSServers.values()
        for server in servers:
            for ban in banlist:
                self.bot.sendtoDCS(server, {"command": "ban", "ucid": ban['ucid'], "channel": server['status_channel']})
