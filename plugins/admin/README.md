# Plugin "Admin"
This plugin supports administrative commands that are needed to operate a DCS server remotely.

## Configuration
You can specify, which folders / patterns you want to offer your admins to download from your server via the `/download` 
command. There is a default list being loaded, if no list is provided.

```yaml
DEFAULT:  # The DEFAULT section is valid for all your servers
  # we only have a download section atm
  downloads:
  # that's for your DCS logs. It should work for all your servers.
  - label: DCS Logs
    directory: '%USERPROFILE%\Saved Games\{server.instance.name}\logs'
    pattern: 'dcs*.log'
  # That is for the DCSSB logs. Should work for all your servers.
  - label: DCSServerBot Logs
    directory: logs
    pattern: 'dcssb-*.log*'
  # This is for your missions. If you use a central mission directory, you might want to amend that.
  - label: Missions
    directory: '%USERPROFILE%\Saved Games\{server.instance.name}\Missions'
    pattern: '*.miz'
  # This is for Tacview. If you use an instance-specific tacview directory, this needs to be changed.
  # Player-specific files aren't supported yet for download. See auto-upload to channels in the Tacview-extension.
  - label: Tacview
    directory: '%USERPROFILE%\Documents\Tacview'
    pattern: 'Tacview-*.acmi'
    target: <id:1122334455667788> # tacview files will be uploaded in this channel instead
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
  # The service configuration files of DCSSB. You can upload changed configurations again to your admin channels.
  # Be aware, the bot.yaml contains your Discord TOKEN in a readable format.
  - label: Service Config Files
    directory: .\config\services
    pattern: '*.yaml'
    discord:      # only Admin users can download these config files
      - Admin
    audit: true   # each download is audited
```
When using the /download command, you can select which "label" you want to download.<br/>
If "target" is not provided, the file will be sent as a DM. If sending as a DM exceeds the limits of 25 MB, it tries to 
download to the current channel. Discord limits may apply.</br>
"target" can be anything from a file path to a channel (see example above).

## Discord Commands

| Command        | Parameter        | Channel       | Role      | Description                                                                                                                                      |
|----------------|------------------|---------------|-----------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| /dcs ban       | user             | all           | DCS Admin | Bans a specific player from your DCS servers. You get a selection of all users and need to provide a reason and the time for the ban afterwards. |
| /dcs unban     | user             | all           | DCS Admin | Unbans a specific player that is banned atm.                                                                                                     |
| /dcs bans      | user             | all           | DCS Admin | Gets detailed information about a specific ban.                                                                                                  |
| /dcs update    | node [warn time] | all           | DCS Admin | Updates DCS World to the latest available version.                                                                                               |
| /dcs install   | node module      | all           | Admin     | Installs a missing module into your DCS server (usually maps).                                                                                   |
| /dcs uninstall | node module      | all           | Admin     | Uninstalls a module from your DCS server (usually maps).                                                                                         |
| /node list     |                  | all           | DCS Admin | Shows an information about all configured nodes (multi-node installations only).                                                                 |
| /node exit     |                  | all           | Admin     | Terminates the bot. It will auto-restart, when configured (recommended).                                                                         |
| /node upgrade  |                  | all           | Admin     | Upgrades the bot. It will auto-restart when configured (recommended).                                                                            |
| /download      |                  | admin-channel | DCS Admin | Download a dcs.log, dcssb-*.log, bot config file, missions and more (see above) into a DM, path or configured channel.                           |
| /prune         |                  | all           | Admin     | Runs a cleanup on the DCSServerBot database. You can specify which data should be deleted.                                                       |
| /reload        | plugin           | all           | Admin     | Reloads a DCSServerBot plugin.                                                                                                                   |

## Config File Uploads
Every config file that either the bot uses for itself (`main.yaml`, etc.) or the different plugins use (`<plugin>.yaml`)
can be uploaded in the admin channels by a user belonging to the Admin group. The files will be replaced, the dedicated 
plugin will be reloaded or the bot will be restarted (security question applies), if you update the main config files. 

**All changes will happen on the node that is controlling the server of that specific admin channel!**<br>
If you use a central cloud folder for your configuration, it will be replaced on this one.

## Tables
### Bans
| Column       | Type                                                       | Description                                          |
|--------------|------------------------------------------------------------|------------------------------------------------------|
| #ucid        | TEXT NOT NULL                                              | Unique ID of this player. FK to the players table.   |
| banned_by    | TEXT NOT NULL                                              | User name that banned or DCSServerBot for auto bans. |
| reason       | TEXT                                                       | Reason for the ban.                                  |
| banned_at    | TIMESTAMP NOT NULL DEFAULT NOW()                           | When was that user banned.                           |
| banned_until | TIMESTAMP NOT NULL DEFAULT TO_DATE('99991231','YYYYMMDD')  | Until when the user should be banned.                |
