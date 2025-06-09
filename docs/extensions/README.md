---
layout: default
title: README
nav_section: extensions/.
---

# Extensions
Extensions are external programs or mods that you have added to your DCS installation like SRS, Tacview, etc. 
DCSServerBot adds some support for them, reaching from simply displaying details about
them in your server embed (LotAtc) to completely starting and stopping external programs (SRS).

## How to write your own Extension!
Do you use something alongside with DCS that isn't supported yet? Are you someone that does not fear some lines of
Python code? Well then - write your own extension!</br>
<p>
Just implement a python class, extend core.Extension and configure it in your nodes.yaml:

```python
from core import Extension
from discord.ext import tasks
from typing import Optional


class MyExtension(Extension):

    async def prepare(self) -> bool:
        await super().prepare()
        # do something that has to happen, before the DCS server starts up
        return True

    async def startup(self) -> bool:
        await super().startup()
        self.log.debug("Hello World!")
        return True

    def shutdown(self) -> bool:
        self.log.debug("Cya World!")
        return super().shutdown()

    def is_running(self) -> bool:
        return True

    @property
    def version(self) -> str:
        return "1.0.0"

    async def render(self, param: Optional[dict] = None) -> dict:
        return {
            "name": "MyExtension", 
            "version": self.version,
            "value": "enabled" if self.is_running() else "disabled"
        }

    @tasks.loop(hours=24.0)
    async def schedule(self):
        # if you need to run something on a scheduled basis, you can do that in here (optional)
        pass
```

You can then use this extension in your nodes.yaml like so:
```yaml
MyNode:
  # [...]
  extensions:
    mymodule.MyExtension:
      param1: aa
      param2: bb
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        mymodule.MyExtension:
          enabled: true
          param3: cc
          param4: dd
```
