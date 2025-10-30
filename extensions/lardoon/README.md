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
      bind: 0.0.0.0:3113            # IP and port the (single) Lardoon server is listening to
      url: https://myfancyhost.com  # Alternate hostname to be displayed in your status embed 
      minutes: 5                    # Number of minutes the Lardoon database is updated
      use_single_process: true      # Start one Lardoon process instead of one per node (default: true) 
  # [...]
  instances:
    DCS.dcs_serverrelease:
      # [...]
      extensions:
        Lardoon:
          enabled: true
          debug: true                   # Show the Lardoon console output in the DCSSB console. Default = false
          bind: 0.0.0.0:3113            # Optional: IP and port this Lardoon server is listening to (only needed if use_single_process is false)
          url: https://myfancyhost.com  # Optional: Alternate hostname to be displayed in your status embed (only needed if use_single_process is false)
          minutes: 5                    # Optional: Number of minutes the Lardoon database is updated (only needed if use_single_process is false)
          tacviewExportPath: 'G:\My Drive\Tacview Files'  # Alternative drive for tacview files (default: auto-detect from Tacview)
```
Remember to add some kind of security before exposing services like that to the outside world, with, for instance,
an nginx reverse proxy.</br>
If you plan to build Lardoon on your own, I'd recommend the fork of [Team LimaKilo](https://github.com/team-limakilo/lardoon).

> [!IMPORTANT]
> If you want to start multiple Lardoon processes, set use_single_process to false and make sure that you add a "bind"
> parameter to each instance configuration.

> [!TIP]
> You can rename the Lardoon extension in your server status embed by setting a "name" in the configuration like so:
> ```yaml
> extension:
>   Lardoon:
>     name: MyFancyName  # Optional: default is "Lardoon"
> ```
