# Extension "LotAtc"
Another famous extension for DCS is [LotAtc](https://www.lotatc.com/) by D'Art. If you think about any kind of proper
GCI or ATC work, there is no way around it. It perfectly integrates with DCS and DCS-SRS.<br/>
You'll get a notification in your servers status embed about ports and - if you like - passwords and the version of 
LotAtc printed in the footer. If a GCI gets active on your server, players of the respective coalition will be informed
via the in-game chat and a popup. Same if the GCI leaves their slot again.

## Configuration
```yaml
MyNode:
  # [...]
  extensions:
    LotAtc:
      installation: '%ProgramFiles%\LotAtc' # the installation path to your LotAtc installation
      autoupdate: true                      # auto update LotAtc, if a new version is available online (default: false)
      announce:             # Optional: post a message to Discord after every update
        title: LotAtc has been updated to version {}!
        description: 'The following servers will be updated on the next restart:'
        footer: Please make sure you update your LotAtc client also!
        mention:            # Optional mentioning
          - DCS
  instances:
    DCS.release_server:
      # [...]
      extensions:
        LotAtc:
          autoupdate: true          # auto update LotAtc in this instance, if a new version is available (default: false)
          show_passwords: false     # show passwords in the server status embed (default = true)
          host: "myfancyhost.com"   # Show a different hostname instead of your servers external IP
          port: 10310               # you can specify any parameter from LotAtc's config.lua in here to overwrite it
```
