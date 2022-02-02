import psycopg2
import psycopg2.extras
from contextlib import closing
from core import report


class HighscorePlaytime(report.GraphElement):

    def render(self, server_name, period, limit):
        sql = 'SELECT p.discord_id, ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS ' \
              'playtime FROM statistics s, players p, missions m WHERE p.ucid = s.player_ucid AND ' \
              's.hop_off IS NOT NULL AND p.discord_id <> -1 AND s.mission_id = m.id '
        if server_name:
            sql += f' AND m.server_name = \'{server_name}\' '
        if period:
            sql += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'
        sql += f' GROUP BY p.discord_id ORDER BY 2 DESC LIMIT {limit}'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                labels = []
                values = []
                self.log.debug(sql)
                cursor.execute(sql)
                for row in cursor.fetchall():
                    member = self.bot.guilds[0].get_member(row[0])
                    name = member.display_name if member else 'Unknown'
                    labels.insert(0, name)
                    values.insert(0, row[1] / 3600)
                self.axes.barh(labels, values, color=['#CD7F32', 'silver', 'gold'], height=0.75)
                self.axes.set_xlabel('hours')
                self.axes.set_title('Longest Playtimes', color='white', fontsize=25)
                if len(values) == 0:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class HighscoreElement(report.GraphElement):

    def render(self, server_name, period, limit, kill_type):
        sql_parts = {
            'Air Targets': 'SUM(s.kills_planes+s.kills_helicopters)',
            'Ships': 'SUM(s.kills_ships)',
            'Air Defence': 'SUM(s.kills_sams)',
            'Ground Targets': 'SUM(s.kills_ground)',
            'Most Efficient Killers': 'SUM(s.kills) / (SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600)',
            'Most Wasteful Pilots': 'SUM(s.crashes) / (SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600)',
            'KD-Ratio': 'CASE when sum(s.deaths) = 0 then sum(s.kills) else sum(s.kills)/sum(s.deaths) end',
            'PvP-KD-Ratio': 'CASE when sum(s.deaths_pvp) = 0 then sum(s.pvp) else sum(s.pvp)/sum(s.deaths_pvp) end'
        }
        xlabels = {
            'Air Targets': 'kills',
            'Ships': 'kills',
            'Air Defence': 'kills',
            'Ground Targets': 'kills',
            'Most Efficient Killers': 'kills / h',
            'Most Wasteful Pilots': 'airframes wasted / h',
            'KD-Ratio': 'K/D-ratio',
            'PvP-KD-Ratio': 'K/D-ratio'
        }
        colors = ['#CD7F32', 'silver', 'gold']
        sql = f'SELECT p.discord_id, {sql_parts[kill_type]} FROM players p, statistics s, missions m WHERE ' \
              's.player_ucid = p.ucid AND p.discord_id <> -1 AND s.mission_id = m.id'
        if server_name:
            sql += f' AND m.server_name = \'{server_name}\' '
        if period:
            sql += f' AND DATE(s.hop_on) > (DATE(NOW()) - interval \'1 {period}\')'
        sql += f' AND s.hop_off IS NOT NULL GROUP BY p.discord_id HAVING {sql_parts[kill_type]} > 0 ORDER BY 2 DESC LIMIT {limit}'

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                self.log.debug(sql)
                cursor.execute(sql)
                result = cursor.fetchall()
                labels = []
                values = []
                for i in range(0, len(result)):
                    if len(result) > i:
                        member = self.bot.guilds[0].get_member(result[i][0])
                        name = member.display_name if member else 'Unkown'
                        labels.insert(0, name)
                        values.insert(0, result[i][1])
                self.axes.barh(labels, values, color=colors, label=kill_type, height=0.75)
                self.axes.set_title(kill_type, color='white', fontsize=25)
                self.axes.set_xlabel(xlabels[kill_type])
                if len(values) == 0:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
