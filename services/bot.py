import discord
from core import DCSServerBot, ServiceRegistry, Service
from discord.ext import commands


@ServiceRegistry.register("Bot")
class BotService(Service):

    def __init__(self, main):
        super().__init__(main=main)
        self.bot = None

    def init_bot(self):
        def get_prefix(client, message):
            prefixes = [self.config['BOT']['COMMAND_PREFIX']]
            # Allow users to @mention the bot instead of using a prefix
            return commands.when_mentioned_or(*prefixes)(client, message)

        # Create the Bot
        return DCSServerBot(version=self.config['BOT']['VERSION'],
                            sub_version=self.config['BOT']['SUB_VERSION'],
                            command_prefix=get_prefix,
                            description='Interact with DCS World servers',
                            owner_id=int(self.config['BOT']['OWNER']),
                            case_insensitive=True,
                            intents=discord.Intents.all(),
                            log=self.log,
                            config=self.config,
                            pool=self.pool,
                            help_command=None,
                            heartbeat_timeout=120,
                            assume_unsync_clock=True)

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.bot = self.init_bot()
        await super().start()
        # cleanup the database of pending requests to an old master
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM intercom WHERE agent = 'Master'")
        # ask any active agent to register its servers with us
        for agent in self.main.get_active_agents():
            self.bot.sendtoBot({
                "command": "rpc",
                "object": "Agent",
                "method": "register_servers"
            }, agent=agent)
        async with self.bot:
            await self.bot.start(token, reconnect=reconnect)

    async def stop(self):
        if self.bot:
            await self.bot.close()
        await super().stop()
