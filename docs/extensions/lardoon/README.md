---
layout: default
title: README
nav_section: extensions/lardoon
---

# Extension "Lardoon"
[Lardoon](https://github.com/b1naryth1ef/lardoon) is another web-server-based application that provides a nice search 
interface for Tacview files. It is based on [Jambon](https://github.com/b1naryth1ef/jambon) an ACMI parser.</br>
You can use it more or less like Sneaker. It contains of a single server instance, that runs on a specific port, and
it'll import all Tacview files from all your servers. You can access the gui with your browser.

## Configuration
```yaml
MyNode:
  # [...]
  extensions:
    Lardoon:
      cmd: '%USERPROFILE%\Documents\GitHub\lardoon\lardoon.exe'
      bind: 0.0.0.0:3113            # IP and port the Lardoon server is listening to
      url: https://myfancyhost.com  # Alternate hostname to be displayed in your status embed 
      minutes: 5                    # Number of minutes the Lardoon database is updated
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Lardoon:
          enabled: true
          debug: true               # Show the Lardoon console output in the DCSSB console. Default = false
          tacviewExportPath: 'G:\My Drive\Tacview Files'  # Alternative drive for tacview files (default: auto-detect from Tacview)
```
Don't forget to add some kind of security before exposing services like that to the outside world, with for instance
a nginx reverse proxy.</br>
If you plan to build Lardoon on your own, I'd recommend the fork of [Team LimaKilo](https://github.com/team-limakilo/lardoon).
