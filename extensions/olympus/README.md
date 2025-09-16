# Extension "DCS Olympus"
[DCS Olympus](https://github.com/Pax1601/DCSOlympus) is a free and open-source mod for DCS that enables dynamic 
real-time control through a map interface. It is a mod that needs to be installed into your servers. Best you can do
is to download the latest ZIP file from [here](https://github.com/Pax1601/DCSOlympus/releases/latest) and provide it to the [ModManager](../../services/modmanager/README.md) service like so:
```yaml
DEFAULT:
  SavedGames: '%USERPROFILE%\Documents\ModManager\SavedGames'
  RootFolder: '%USERPROFILE%\Documents\ModManager\RootFolder'
DCS.release_server:
  packages:
  - name: DCSOlympus
    version: latest
    source: SavedGames
    # uncomment for auto-update:
    # repo: https://github.com/Pax1601/DCSOlympus
```
To use the DCS Olympus client, you need [Node.js](https://nodejs.org/download/release/latest-v20.x/) installed.
Click on the link, download and install it. Remember the installation location, as you need to provide it in the 
configuration.

> [!WARNING]
> Do NOT install Chocolatey, it is unnecessary for Olympus, and it seems to create issues with Python installations.

## Configuration
Then you can add the DCS Olympus extension like so to your nodes.yaml:

### Version 1.0.4 or higher
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
            adminPassword: admin          # Admin Password (Olympus 2.0)
          frontend:
            path: '%USERPROFILE%\Saved Games\Olympus\frontend' # Optional: path to the Olympus frontend. This is only needed if you are using the official installer. ModManager users don't need this.
            port: 3000                    # Port where DCS Olympus listens for client access (needs to be unique)
            customAuthHeaders:            # SSO configuration (Olympus 2.0), see Olympus documentation
              ...
            elevationProvider:            # Elevation data provider (Olympus 2.0), see Olympus documentation
              ...
            mapLayers:                    # Providers for map displays (Olympus 2.0), see Olympus documemtation
              ...
            mapMirrors:                   # Map tiles sources (Olympus 2.0), see Olympus documentation
              ...
          audio:                          # SRS audio settings (Olympus 2.0)
            WSPort: 4000                  # The WSPort is the port used by the web interface to connect to the audio backend WebSocket. It should be available and not used by other processes.
            WSEndpoint: audio             # The WSEndpoint is the endpoint used by the web interface to connect to the audio backend WebSocket when using a reverse proxy. A websocket proxy should be set up to forward requests from this endpoint to WSPort.
    instance2:
      # [...]
      extensions:
        Olympus:
          enabled: false                  # Don't enable DCS Olympus on your instance2
```
> ⚠️ **Attention!**<br>
> You need to forward the frontend port from your router to the PC running DCS and DCS Olympus.

### Version 1.0.3 (deprecated)
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
