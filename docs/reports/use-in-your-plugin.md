---
title: Using Reports in your Plugins
parent: Report Framework
nav_order: 999
---

# Using Reports in your Plugins

It is very simple to generate a report in your plugins. You just need to instantiate one of the available Report classes with a json file that is stored in the ./reports subdirectory of your plugin.

```python
from core import DCSServerBot, Plugin, Report
from discord.ext import commands


class Test(Plugin):
    @commands.command(description='Test')
    async def test(self, ctx):
        report = Report(self.bot, self.plugin_name, 'test.json')
        env = await report.render(params={"name": "Special K"})
        await ctx.send(embed=env.embed)


def setup(bot: DCSServerBot):
    bot.add_cog(Test(bot))
```

{: .note }
> If your reports contain graphs, the created image will be returned in `env.filename`.
> You need to take care of wrapping the in a `discord.File` and deleting the file after it has been displayed.
