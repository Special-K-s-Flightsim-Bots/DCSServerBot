import asyncio

from core import ServiceRegistry, Service, DEFAULT_TAG, utils, Server
from datetime import datetime
from discord.ext import tasks
from typing import Optional

from . import actions
from ..bot import BotService


@ServiceRegistry.register(master_only=True, plugin="scheduler")
class SchedulerService(Service):

    def __init__(self, node):
        super().__init__(node=node, name="Scheduler")
        self.bot = None

    async def start(self, *args, **kwargs):
        if self.locals:
            await super().start()
            self.bot = ServiceRegistry.get(BotService).bot
            self.schedule.start()

    async def stop(self, *args, **kwargs):
        if self.locals:
            self.schedule.cancel()
            await super().stop()

    def get_config(self, server: Optional[Server] = None) -> dict:
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        else:
            return self.locals.get(server.instance.name, {})

    async def do_actions(self, config: dict, server: Optional[Server] = None):
        action = config['action']
        try:
            func = getattr(actions, action['type'])
            kwargs = action.get("params", {})
            kwargs['node'] = self.node
            if server:
                kwargs['server'] = server
            if asyncio.iscoroutinefunction(func):
                await func(**kwargs)
            else:
                func(**kwargs)
        except Exception as ex:
            self.log.error(f"Scheduler: error while processing action {action}", exc_info=ex)

    @tasks.loop(minutes=1)
    async def schedule(self):
        async def check_run(server: Optional[Server] = None):
            now = datetime.now().replace(second=0, microsecond=0)
            for cfg in config['actions']:
                if 'cron' in cfg and not utils.matches_cron(now, cfg['cron']):
                    continue
                elif (server and 'mission_time' in cfg and
                      server.current_mission.mission_time < cfg['mission_time'] * 60):
                    continue
                # noinspection PyAsyncCall
                asyncio.create_task(self.do_actions(cfg, server))

        try:
            config = self.get_config()
            # run all default tasks
            await check_run(None)
            # do the servers
            for server in self.bot.servers.copy().values():
                config = self.get_config(server)
                if config:
                    await check_run(server)
        except Exception as ex:
            self.log.exception(ex)

    @schedule.before_loop
    async def before_loop(self):
        if self.bot:
            await self.bot.wait_until_ready()
