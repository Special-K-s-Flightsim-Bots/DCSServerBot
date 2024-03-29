---
parent: Extensions
nav_order: 999
---

# Write your own Extension!

Do you use something alongside with DCS that isn't supported yet? Are you someone that does not fear some lines of
Python code? Well then - write your own extension!

Just implement a python class, extend core.Extension and load it in your scheduler.json:

```python
from __future__ import annotations
from typing import Optional
from core import Extension, report

class MyExtension(Extension):

    async def startup(self) -> bool:
        self.log.debug("Hello World!")
        return True

    async def shutdown(self) -> bool:
        self.log.debug("Cya World!")
        return True

    async def is_running(self) -> bool:
        return True

    @property
    def version(self) -> str:
        return "1.0.0"

    def verify(self) -> bool:
        return True

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        embed.add_field(name='MyExtension', value='enabled' if self.verify() else 'disabled')
```

You can then use this extension in your scheduler.json like so:

```json
{
  "configs": [
    {
      "installation": "DCS.release_server",
      [...]
      "extensions": {
        "mymodule.MyExtension": {}
      }
    }
  ]
}
```
