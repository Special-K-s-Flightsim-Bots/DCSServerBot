import sched
from datetime import datetime, timedelta
from core import utils, EventListener


class SchedulerEventListener(EventListener):

    def do_scheduled_restart(self, server, method, restart_in_seconds=0):
        self.log.debug('Scheduling restart for server {} in {} seconds.'.format(
            server['server_name'], restart_in_seconds))
        s = sched.scheduler()
        if 'warn' in server[self.plugin]:
            warn_times = server[self.plugin]['warn']['times'] if 'times' in server[self.plugin]['warn'] else []
            warn_text = server[self.plugin]['warn']['text'] if 'text' in server[self.plugin]['warn'] \
                else '!!! Server will restart in {} seconds !!!'
            if len(warn_times) > 0:
                if restart_in_seconds < max(warn_times):
                    restart_in_seconds = max(warn_times)
            for warn_time in warn_times:
                s.enter(restart_in_seconds - warn_time, 1, self.bot.sendtoDCS, kwargs={
                    'server': server,
                    'message': {
                        'command': 'sendPopupMessage',
                        'message': warn_text.format(warn_time),
                        'to': 'all'
                    }
                })
        if method == 'restart':
            if 'shutdown' in server[self.plugin]['restart'] and server[self.plugin]['restart']['shutdown'] is True:
                s.enter(restart_in_seconds, 1, self.bot.sendtoDCS, kwargs={
                    'server': server,
                    'message': {
                        "command": "shutdown"
                    }
                })
                s.enter(restart_in_seconds + 10, 1, utils.start_dcs, (self, server['installation']))
            else:
                s.enter(restart_in_seconds, 1, self.bot.sendtoDCS, kwargs={
                    'server': server,
                    'message': {
                        "command": "restartMission"
                    }
                })
        elif method == 'rotate':
            s.enter(restart_in_seconds, 1, self.bot.sendtoDCS, kwargs={
                'server': server,
                'message': {"command": "startNextMission"}
            })
        server['restartScheduler'] = s
        self.loop.run_in_executor(self.bot.executor, s.run)

    async def registerDCSServer(self, data):
        server = self.globals[data['server_name']]
        if self.plugin not in server:
            server[self.plugin] = self._plugin.read_config(server)
        server['restart_pending'] = False

    async def getMissionUpdate(self, data):
        server_name = data['server_name']
        server = self.globals[server_name]
        installation = server['installation']
        # check if we have to restart the mission
        if 'restartScheduler' not in server and not server['restart_pending']:
            # check if a restart should be executed relative to mission start
            if 'restart' in server[self.plugin]:
                if 'mission_time' in server[self.plugin]['restart'] and \
                        data['mission_time'] > int(server[self.plugin]['restart']['mission_time']) * 60:
                    players = self.bot.player_data[server_name]
                    if 'populated' in server[self.plugin]['restart'] and \
                            server[self.plugin]['restart']['populated'] is False and \
                            len(players[players['active'] == True]) > 0:
                        self.log.debug(f"Scheduled restart of server \"{server_name}\" postponed due to server population.")
                        server['restart_pending'] = True
                    else:
                        self.do_scheduled_restart(server, server[self.plugin]['restart']['method'])
            elif 'local_times' in server[self.plugin]['restart']:
                now = datetime.now()
                times = []
                for t in server[self.plugin]['restart']['local_times']:
                    d = datetime.strptime(t, '%H:%M')
                    check = now.replace(hour=d.hour, minute=d.minute)
                    if check.time() > now.time():
                        times.insert(0, check)
                        break
                    else:
                        times.append(check + timedelta(days=1))
                if len(times):
                    self.do_scheduled_restart(
                        server, server[self.plugin]['restart']['method'], int((times[0] - now).total_seconds()))
                else:
                    self.log.warning(
                        f'Configuration mismatch! local_times not set correctly for server {server_name}.')

    async def onSimulationStop(self, data):
        server = self.globals[data['server_name']]
        # stop all restart events
        if 'restartScheduler' in server:
            s = server['restartScheduler']
            # Don't pull the one and only start event from the queue, as we want it to be executed.
            if len(s.queue) > 0 and s.queue[0].action != utils.start_dcs:
                for event in s.queue:
                    s.cancel(event)
            # delete everything else
            del server['restartScheduler']

    async def onGameEvent(self, data):
        if data['eventName'] == 'disconnect':
            if data['arg1'] != 1:
                # if no player is in the server anymore and we have a pending restart, restart the server
                server_name = data['server_name']
                players = self.bot.player_data[server_name]
                if len(players[players['active'] == True]) == 0 and self.globals[server_name]['restart_pending']:
                    server = self.globals[server_name]
                    self.bot.sendtoDCS(server, {"command": "restartMission", "channel": "-1"})
