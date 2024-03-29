---
parent: Extensions
nav_order: 0
---

# Lardoon

[Lardoon](https://github.com/b1naryth1ef/lardoon) is another web-server-based application that provides a nice search 
interface for Tacview files. It is based on [Jambon](https://github.com/b1naryth1ef/jambon) an ACMI parser.</br>
You can use it more or less like Sneaker. It contains of a single server instance, that runs on a specific port, and
it'll import all Tacview files from all your servers. You can access the gui with your browser.

```json
{
  "configs": [
    {
      [...]
      "extensions": {
        "Lardoon": {
          "cmd": "%USERPROFILE%\\Documents\\GitHub\\lardoon\\lardoon.exe",
          "minutes": 5,
          "bind": "0.0.0.0:3113",
          "url": "http://mydnsname:3113"
        }
      }
      [...]
    },
    {
      "installation": "DCS.release_server",
      [...]
      "extensions": {
        "Lardoon": {
          "enabled": true
        }
      }
    }
  ]
}
```

Don't forget to add some kind of security before exposing services like that to the outside world, with for instance
a nginx reverse proxy.</br>
If you plan to build Lardoon on your own, I'd recommend the fork of [Team LimaKilo](https://github.com/team-limakilo/lardoon).
