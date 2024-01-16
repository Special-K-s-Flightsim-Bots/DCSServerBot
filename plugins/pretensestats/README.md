# Plugin PretenseStats
With this plugin, you can display persistent status update embeds for your Pretense missions.

> ⚠️ **Attention!**<br>
> This plugin can **NOT** run on multi-node-systems yet.  


## Configuration
The configuration is quite easy. You can define a channel per instance that you want your embeds to be displayed in.
And you can define, how often they should be updated. Embeds will only be updated, if there is a pretense mission 
running and if there is new data to be displayed.

```yaml
DEFAULT:
  update_interval: 60         # interval in seconds when the embed should update (default = 60)
DCS.openbeta_server:
  json_file_path: '%USERPROFILE%\Saved Games\DCS.openbeta\Missions\Saves\player_stats.json' # this is the default
  channel: 1122334455667788   # channel, where to upload the stats into (default: Status channel)
```
Every parameter has a default value, which means, that you do not need to specify a configuration file at all.

## Credits
Credits to No15|KillerDog for implementing the base version of this plugin!
