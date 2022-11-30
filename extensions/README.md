# Extensions
Extensions are external programs or mods that you have added to your DCS installation like SRS, Tacview, etc. 
(supported ones, see below). DCSServerBot adds some support for them, reaching from simply displaying details about
them in your server embed (LotAtc) to completely starting and stopping external programs (SRS).

## Supported Extensions
If you have looked around a bit, you might have seen already that I try to create APIs that you guys can use to extend
what is there. That said - there is a list of Extensions that I added already, but you can write our own. I'll give an
example later.

### SRS
[SimpleRadioStandalone](http://dcssimpleradio.com/) (DCS-SRS) is an awesome tool built by our friend CiriBob, who 
dedicates a lot of work and this simulated real life radio experience to DCS. Many if not every server runs an SRS
server too, to let their players have a proper radio experience.<br/>
DCSServerBot integrates nicely with SRS. If you place your server.cfg in your Saved Games\DCS(..)\Config folder (and I
usually rename it to SRS.cfg, just to avoid confusions in there), the bot can auto-start and -stop your SRS server 
alongside with your DCS server. It even monitors if SRS has crashed (that's a waste of code.. I literally never saw
that crash) and start it again in such a case.<br/>
To enable SRS, support, you need to add the following parts to your [scheduler.json](..\plugins\scheduler\README.md):
```json
{
  "configs": [
    {
      [...]
      "extensions": {
        "SRS": {
          "installation": "%ProgramFiles%\\DCS-SimpleRadio-Standalone"
        }
      }
      [...]
    },
    {
      "installation": "DCS.openbeta_server",
      [...]
      "extensions": {
        "SRS": {
          "config": "%USERPROFILE%\\Saved Games\\DCS.openbeta_server\\Config\\SRS.cfg"
        }
      }
    }
  ]
}
```
Where [...] are parts that you have in your scheduler.json anyway. So you need one entry in the default section, 
pointing to your DCS-SRS installation and one in every server section, where you want to use SRS with. That's all,
the next time the scheduler starts your server, it will auto-launch SRS and take care of it.

### Tacview
Many servers run [Tacview](https://www.tacview.net/) to help people analyse their flight path, weapons employment and 
whatnot. It is an awesome tool for teaching and after action reports as well.<br/>
One of the downsides (besides a performance hit on servers) is, that you gather a lot of data and fill up your disk.
DCSServerBot takes care of both, it will a) warn you, if you configured Tacview in a way that is bad for your overall
server performance and it can delete old Tacview files after a specific time.<br/>
To enable Tacview support, again, a change in scheduler.json is needed:
```json
{
  "configs": [
    {
      [...]
      "extensions": {
        "Tacview": {
          "path": "%USERPROFILE%\\Documents\\Tacview",
          "delete_after": 30
        }
      }
      [...]
    },
    {
      "installation": "DCS.openbeta_server",
      [...]
      "extensions": {
        "Tacview": {
          "show_passwords": false
        }
      }
    }
  ]
}
```
**delete_after** specifies the number of days after which old Tacview files will get deleted by the bot.</br>
**show_passwords** specifies whether to show the Tacview passwords in the server embed in your status channel or not.

### LotAtc
Another famous extension for DCS is [LotAtc](https://www.lotatc.com/) by D'Art. If you think about any kind of proper
GCI or ATC work, there is no way around it. It perfectly integrates with DCS and DCS-SRS.<br/>
DCSServerBot can detect if it is there and enabled, but that's about it. You'll get a notification in your servers
status embed about ports and - if you like - passwords and the version of LotAtc printed in the footer.
```json
{
  "configs": [
    {
      "installation": "DCS.openbeta_server",
      [...]
      "extensions": {
        "LotAtc": {
          "show_passwords": false
        }
      }
    }
  ]
}
```
There is no default section for LotAtc, so if added to a server like described above, it is enabled, if not, then not.

### DSMC
If you want to enable persistence for your missions, [DSMC](https://dsmcfordcs.wordpress.com/) is one way to go.
DSMC does not need any change in your missions (but you can, see their documentation!). It will write out a new
miz-file with the state of the mission at the time of saving. This is perfect for instance for campaigns, where you
want to follow up on the next campaign day with the exact state of the mission it had at the end of the current day.</br>
To use DSMC, you need to install it, according to the documentation linked above. In DCSServerBot, you activate the 
extension like with all others:
```json
    {
      "installation": "DCS.openbeta_server",
      [...]
      "extensions": {
        "DSMC": {
          "enabled": true
        }
      }
    }
```
DCSServerBot will detect if DSCM is enabled and - if yes - change the settings in your DSMC_Dedicated_Server_options.lua
to fit to its needs. DSMC will write out a new miz-file with a new extension (001, 002, ...) after each run. The bot
will take care, that this generated mission will be the next to launch. Other extensions like RealWeather work together
with these generated missions, so you can use a DSMC generated mission but apply a preset or any real time weather to
it.

### Sneaker
Well, this "sneaked" in here somehow. Many people were asking for a moving map and we looked at several solutions. 
Nearly all took a lot of effort to get them running, if ever. Then we stumbled across 
[Sneaker](https://github.com/b1naryth1ef/sneaker) and in all fairness - that was more or less all that we needed. It 
looks good, it is easy to setup. We tried to contact the developer, but unfortunately they are quite unresponsive. So
we created a [fork](https://github.com/Special-K-s-Flightsim-Bots/sneaker), added all the maps and maybe will remove
some of the main bugs in the upcoming future.<br/>
Sneaker itself provides a webserver that then connect via the Tacview Realtime protocol to your server. You need to 
have Tacview running on your server though, to use sneaker. As there are still some issues, please don't configure a
realtime password for now.<br/>
Adding sneaker is quite straightforward, if you looked at the above examples already:
```json
{
  "configs": [
    {
      [...]
      "extensions": {
        "Sneaker": {
          "cmd": "%USERPROFILE%\\Documents\\GitHub\\sneaker\\sneaker.exe",
          "bind": "0.0.0.0:8080"
        }
      }
      [...]
    },
    {
      "installation": "DCS.openbeta_server",
      [...]
      "extensions": {
        "Sneaker": {
          "enabled": true
        }
      }
    }
  ]
}
```
You need to let the sneaker cmd point to wherever you've installed the sneaker.exe binary (name might vary, usually 
there is a version number attached to it). DCSServerBot will auto-create the config json for sneaker 
(config/sneaker.json) and start / stop / monitor the sneaker process.

### DCS-real-weather
If you want to use real-time weather in your missions, you can do that by using [DCS-real-weather](https://github.com/evogelsa/DCS-real-weather).
Download the release zip and unzip it to a directory of your choice on your system running your DCS servers and the 
DCSServerBot. You can then add another extension into your scheduler.json:
```json
{
  "configs": [
    {
      [...]
      "extensions": {
        "RealWeather": {
          "installation": "%USERPROFILE%\\Documents\\realweather_v1.5.0"
        }
      }
      [...]
    },
    {
      "installation": "DCS.openbeta_server",
      [...]
      "extensions": {
        "RealWeather": {
          "enabled": true,
          "icao": "SFAL",
          "update-time": true,
          "update-weather": true
        }
      }
    }
  ]
}
```
You can find a list of supported parameters in the config.json provided by DCS-real-weather.

### Write your own Extension!
Do you use something alongside with DCS that isn't supported yet? Are you someone that does not fear some lines of
Python code? Well then - write your own extension!</br>
<p>
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
      "installation": "DCS.openbeta_server",
      [...]
      "extensions": {
        "mymodule.MyExtension": {}
      }
    }
  ]
}
```