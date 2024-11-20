# Extension "DCS Olympus"
[DCS Olympus](https://github.com/Pax1601/DCSOlympus) is a free and open-source mod for DCS that enables dynamic 
real-time control through a map interface. It is a mod that needs to be installed into your servers. Best you can do
is to download the latest ZIP file from [here](https://github.com/Pax1601/DCSOlympus/releases/latest) and provide it to the [OvGME](../../services/ovgme/README.md) service like so:
```yaml
DEFAULT:
  SavedGames: '%USERPROFILE%\Documents\OvGME\SavedGames'
  RootFolder: '%USERPROFILE%\Documents\OvGME\RootFolder'
DCS_MERCS:
  packages:
  - name: DCSOlympus
    version: latest
    source: SavedGames
```
To use the DCS Olympus client, you need [Node.js](https://nodejs.org/download/release/latest-v20.x/) installed.
Click on the link, download and install it. Remember the installation location, as you need to provide it in the 
configuration.

## Configuration
Then you can add the DCS Olympus extension like so to your nodes.yaml:

### Version 1.0.4
```yaml
MyNode:
  # [...]
  extensions:
    Olympus:
      nodejs: '%ProgramFiles%\nodejs'
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Olympus:
          debug: true                     # Show the Olympus console in the DCSSB console, default = false
          show_passwords: true            # show passwords in your server status embed (default: false)
          url: http://myfancyurl:3000/    # optional: your own URL, if available
          backend:
            port: 3001                    # server port for DCS Olympus internal communication (needs to be unique)                   
          authentication:
            gameMasterPassword: secret    # Game Master password
            blueCommanderPassword: blue   # Blue Tactical Commander password
            redCommanderPassword: red     # Red Tactical Commander password
          frontend:
            path: '%USERPROFILE%\Saved Games\Olympus\frontend' # Optional: path to the Olympus frontend. This is only needed if you are using the official installer. OVGME users don't need this.
            port: 3000                    # Port where DCS Olympus listens for client access (needs to be unique)
    instance2:
      # [...]
      extensions:
        Olympus:
          enabled: false                  # Don't enable DCS Olympus on your instance2
```
> ⚠️ **Attention!**<br>
> You need to forward the frontend port from your router to the PC running DCS and DCS Olympus.

### Version 1.0.3
```yaml
MyNode:
  # [...]
  extensions:
    Olympus:
      nodejs: '%ProgramFiles%\nodejs'
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Olympus:
          debug: true                     # Show the Olympus console in the DCSSB console, default = false
          show_passwords: true            # show passwords in your server status embed (default: false)
          url: http://myfancyurl:3000/    # optional: your own URL, if available
          server:
            address: '*'                  # your bind address. * = 0.0.0.0, use localhost for local only setups
            port: 3001                    # server port for DCS Olympus internal communication (needs to be unique)                   
          authentication:
            gameMasterPassword: secret    # Game Master password
            blueCommanderPassword: blue   # Blue Tactical Commander password
            redCommanderPassword: red     # Red Tactical Commander password
          client:
            port: 3000                    # Port where DCS Olympus listens for client access (needs to be unique)
    instance2:
      # [...]
      extensions:
        Olympus:
          enabled: false                  # Don't enable DCS Olympus on your instance2
```
> ⚠️ **Attention!**<br>
> You need to forward the server port and the client port from your router to the PC running DCS and DCS Olympus.<br>
> To create an exclusion in your UAC run this: `netsh http add urlacl url="http://*:3001/olympus/" user=user-running-dcs`
