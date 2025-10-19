# Plugin "Admin"
This plugin supports administrative commands that are needed to operate a DCS server remotely.

## Configuration
You can specify, which folders / patterns you want to offer your admins to download from your server via the `/download` 
command. There is a default list being loaded, if no list is provided.

```yaml
DEFAULT:  # The DEFAULT section is valid for all your servers
  downloads:
  # that's for your DCS logs. It should work for all your servers.
  - label: DCS Logs
    directory: '{server.instance.home}\logs'
    pattern: 'dcs*.log'
  # That is for the DCSSB logs. Should work for all your servers.
  - label: DCSServerBot Logs
    directory: logs
    pattern: 'dcssb-*.log*'
  # This is for your missions. The bot knows where all your missions are, so nothing more to do here.
  - label: Missions
  # This is for DCS Trackfiles. Please keep in mind that these files can get really huge
  - label: Trackfiles
    directory: '{server.instance.home}\Tracks'
    pattern: '*.trk'
    target: 'G:\My Drive\Tracks' # download the files to a specific directory
  # This is for Tacview. If you use an instance-specific tacview directory, this needs to be changed.
  # Player-specific files aren't supported yet for download. See auto-upload to channels in the Tacview-extension.
  - label: Tacview
    directory: '%USERPROFILE%\Documents\Tacview'
    pattern: 'Tacview-*.acmi'
    target: '<id:1122334455667788>' # download the files to a specific channel
  # If you decided to use dedicated chat logs per server (default), this is where you can find them.
  - label: Chat Logs
    directory: logs
    pattern: '{server.instance.name}-chat.*log*'
  # The main configuration files of DCSSB. You can upload changed configurations again to your admin channels.
  - label: Main Config Files
    directory: .\config
    pattern: '*.yaml'
    discord:      # only Admin users can download these config files
      - Admin
    audit: true   # each download is audited
  # All configuration files of your plugins. You can upload changed configurations again to your admin channels.
  - label: Plugin Config Files
    directory: .\config\plugins
    pattern: '*.yaml'
    discord:      # only Admin users can download these config files
      - Admin
    audit: true   # each download is audited
  # The service configuration files of DCSSB. You can upload changed configurations again to your admin channels.
  # Be aware, the bot.yaml contains your Discord TOKEN in a readable format.
  - label: Service Config Files
    directory: .\config\services
    pattern: '*.yaml'
    discord:      # only Admin users can download these config files
      - Admin
    audit: true   # each download is audited
  uploads:        # The uploads section defines who is allowed to upload config files
    enabled: true # If false, uploads are disabled in general
    discord:
      - Admin     # Only Admin users are allowed to upload
```
When using the /download command, you can select which "label" you want to download.<br/>
If "target" is not provided, the file will be sent as a DM. If sending as a DM exceeds the limits of 25 MB, it tries to 
download to the current channel. Discord limits may apply.</br>
"target" can be anything from a file path to a channel (see example above).

## Discord Commands

| Command            | Parameter                              | Channel       | Role      | Description                                                                                                                                      |
|--------------------|----------------------------------------|---------------|-----------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| /dcs ban           | user                                   | all           | DCS Admin | Bans a specific player from your DCS servers. You get a selection of all users and need to provide a reason and the time for the ban afterwards. |
| /dcs unban         | user                                   | all           | DCS Admin | Unbans a specific player that is banned atm.                                                                                                     |
| /dcs bans          | user                                   | all           | DCS Admin | Gets detailed information about a specific ban.                                                                                                  |
| /dcs update        | node [warn time] [branch] [version]    | all           | DCS Admin | Updates DCS World to the latest available version (or switch to another branch / version).                                                       |
| /dcs repair        | [slow] [check_extra_files] [warn_time] | all           | DCS Admin | Repairs DCS World.                                                                                                                               |
| /dcs install       | node module                            | all           | Admin     | Installs a missing module into your DCS server (usually maps).                                                                                   |
| /dcs uninstall     | node module                            | all           | Admin     | Uninstalls a module from your DCS server (usually maps).                                                                                         |
| /node list         |                                        | all           | DCS Admin | Shows an information about all configured nodes (multi-node installations only).                                                                 |
| /node shutdown     | [node]                                 | all           | Admin     | Terminates the specified node (or all nodes).                                                                                                    |
| /node restart      | [node]                                 | all           | Admin     | Restarts the specified node (or all nodes).                                                                                                      |
| /node upgrade      | [node]                                 | all           | Admin     | Upgrades and restarts the specified node (or all nodes).                                                                                         |
| /node offline      | node                                   | all           | Admin     | Shuts down all servers on a specific node and puts them in maintenance mode.                                                                     |
| /node online       | node                                   | all           | Admin     | Clears the maintenance mode on a specific node and starts all servers.                                                                           |
| /node add_instance | <node> [template]                      | admin-channel | Admin     | Adds another instance to your node. You can either add an existing one or you create a new one by specifying a an existing one as a template.    |
| /node cpuinfo      | <node>                                 | admin-channel | Admin     | Shows the CPU topology of your node. More to come.                                                                                               |
| /download          |                                        | admin-channel | DCS Admin | Download a dcs.log, dcssb-*.log, bot config file, missions and more (see above) into a DM, path or configured channel.                           |
| /prune             |                                        | all           | Admin     | Runs a cleanup on the DCSServerBot database. You can specify which data should be deleted.                                                       |
| /plugin install    | plugin                                 | all           | Admin     | Install a plugin into your DCSServerBot installation.                                                                                            |
| /plugin uninstall  | plugin                                 | all           | Admin     | Uninstalls a plugin from your DCSServerBot installation.                                                                                         |
| /plugin reload     | plugin                                 | all           | Admin     | Reloads a DCSServerBot plugin.                                                                                                                   |
| /extension enable  | extension                              | all           | Admin     | Enables a (configured) extension.                                                                                                                |
| /extension disable | extension                              | all           | Admin     | Disables an extension.                                                                                                                           |

## Config File Uploads
All configuration files used by the bot itself (such as `main.yaml`) or by various plugins (like `<plugin>.yaml`) 
can be uploaded via the admin channels, provided this feature is enabled. When these files are updated, they will 
overwrite the existing ones, and depending on the file type, either the specific plugin will be reloaded or the entire 
bot will be restarted (subject to a security confirmation). You can also set specific roles that are permitted to upload 
files.

> [!NOTE]
> **The modifications will take place on the node which oversees the server associated with the particular admin channel!**<br>
> If you're using a central cloud folder for your configurations, the existing configuration in that folder will be replaced.

## Default files for /node add_instance
If you want your new instances to have specific settings, you can provide default files in your config directory.

__These files are supported:__
* autoexec.cfg
* options.lua
* serverSettings.lua

The files act as templates, which means that several data will be replaced automatically (e.g., ports).

> [!NOTE]
> An existing mission list will be cleared from your serverSettings.lua.

## Tables
### Bans
| Column       | Type                                                       | Description                                          |
|--------------|------------------------------------------------------------|------------------------------------------------------|
| #ucid        | TEXT NOT NULL                                              | Unique ID of this player. FK to the players table.   |
| banned_by    | TEXT NOT NULL                                              | User name that banned or DCSServerBot for auto bans. |
| reason       | TEXT                                                       | Reason for the ban.                                  |
| banned_at    | TIMESTAMP NOT NULL DEFAULT NOW()                           | When was that user banned.                           |
| banned_until | TIMESTAMP NOT NULL DEFAULT TO_DATE('99991231','YYYYMMDD')  | Until when the user should be banned.                |
