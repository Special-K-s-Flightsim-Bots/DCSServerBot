---
parent: Extensions
nav_order: 0
---

# SRS

[SimpleRadioStandalone] (DCS-SRS) is an awesome tool built by our friend CiriBob, who dedicates a lot of work and
this simulated real life radio experience to DCS. Many if not every server runs an SRS server too, to let their
players have a proper radio experience.

DCSServerBot integrates nicely with SRS. If you place your server.cfg in your `Saved Games\DCS(..)\Config` folder (and I
usually rename it to `SRS.cfg`, just to avoid confusions in there), the bot can auto-start and -stop your SRS server
alongside with your DCS server. It even monitors if SRS has crashed (that's a waste of code... I literally never saw
that crash) and start it again in such a case.

To enable SRS, support, you need to add the following parts to your [scheduler.json]:

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
      "installation": "DCS.release_server",
      [...]
      "extensions": {
        "SRS": {
          "config": "%USERPROFILE%\\Saved Games\\DCS.release_server\\Config\\SRS.cfg",
          "host": "myfancyhost.com",    -- Optional, default is your external IP
          "port": 5004,                 -- Optional, default is what's written in server.cfg already,
          "awacs": true,                -- enable AWACS mode
          "blue_password": "blue",      -- AWACS mode, blue password
          "red_password": "red",        -- AWACS mode, red password
          "autostart": true     -- can be false to disable autostart of SRS (default = true)
        }
      }
    }
  ]
}
```

You need one entry in the default section, pointing to your DCS-SRS installation and one in every server section, 
where you want to use SRS with. The next time the scheduler starts your server, it will auto-launch SRS and take 
care of it.

__Optional__ parameters (will change `server.cfg` if necessary):

| Parameter          | Description                                                                                               |
|------------  ------|-----------------------------------------------------------------------------------------------------------|
| host               | The hostname or IP to be used in your `DCS-SRS-AutoConnectGameGUI.lua`. The bot will replace it in there. |
| port               | SRS port                                                                                                  |
| awacs              | AWACS mode                                                                                                |
| blue_password      | AWACS mode, password blue.                                                                                |
| red_password       | AWACS mode, password red.                                                                                 |

[SimpleRadioStandalone]: http://dcssimpleradio.com/
[scheduler.json]: ../plugins/scheduler.md
