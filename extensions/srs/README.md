# Extension "SRS"
[SimpleRadioStandalone](http://dcssimpleradio.com/) (DCS-SRS) is an awesome tool built by CiriBob, who dedicates a lot of work and this 
simulated real life radio experience to DCS. Many if not every server runs an SRS server too, to let their players have 
a proper radio experience.<br/>
DCSServerBot integrates nicely with SRS. If you place your server.cfg in your Saved Games\DCS(...)\Config folder (and I
usually rename it to SRS.cfg, just to avoid confusions in there), the bot can auto-start and -stop your SRS server 
alongside with your DCS server. It even monitors if SRS has crashed (that's a waste of code... I literally never saw
that crash) and start it again in such a case.<br/>

## Configuration
To enable SRS, support, you need to add the following parts to your nodes.yaml:
```yaml
MyNode:
  # [...]
  extensions:
    SRS:
      installation: '%ProgramFiles%\DCS-SimpleRadio-Standalone'
      beta: true  # allow beta versions
      autoupdate: true      # auto update your DCS-SRS installation, if a new version is available online (default: false)
      announce:             # Optional: post a message to Discord after every update
        title: DCS-SRS has been updated to version {}!
        description: 'The following servers have been updated:'
        footer: Please make sure you update your DCS-SRS client also!
        mention:            # Optional mentioning
          - DCS
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        SRS:
          config: '{instance.home}\Config\SRS.cfg'
          host: 127.0.0.1
          port: 5002
          gui_server: true    # Optional: use the SRS-Server.exe (GUI server) instead of the command line one
          minimized: true     # Old SR-Server.exe: start SRS minimized (default: true)
          autoconnect: true   # install the appropriate DCS-SRS-AutoConnectGameGUI.lua, default: true
          awacs: true         # if you use LotAtc
          lotatc_export_port: 10712
          blue_password: blue
          red_password: red
          show_passwords: false   # Optional: do not show red/blue passwords in the status embed (default: true)
          radio_effect_override: false                # optional: disable radio effects (LOS, etc)
          global_lobby_frequencies: 248.22,30.0,127.0 # optional: set your music channels in here
          autostart: true     # optional: if you manage your SRS servers outside of DCSSB, set that to false
          always_on: true     # start SRS as soon as possible  (includes no_shutdown: true)
          no_shutdown: true   # optional: don't shut down SRS on mission end (default: false)
          srs_nudge_message: 'Optional nudge message' # optional: overwrite the existing nudge message
          
```
You need one entry in the node section, pointing to your DCS-SRS installation and one in every instance section, 
where you want to use SRS with. The next time the bot starts your server, it will auto-launch SRS and take care of it.

__Optional__ parameters (will change server.cfg if necessary):</br>
* **autoupdate** If true, SRS will check for updates and update itself.
* **host** The hostname or IP to be used in your DCS-SRS-AutoConnectGameGUI.lua. The bot will replace it in there.
* **port** SRS port (default: 5002)
* **awacs** AWACS mode
* **blue_password** AWACS mode, password blue.
* **red_password** AWACS mode, password red.
* **autostart** If true, the SRS server will be auto-started (default).

> ⚠️ **Attention!**<br>
> You need to disable User-Access-Control (UAC) to use SRS-autoupdate.

