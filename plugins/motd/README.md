# Plugin MessageOfTheDay (MOTD)
This plugin adds a message of the day to the server, that is displayed either on join or when you hop in a plane.

## Configuration
The plugin is configured via JSON, as many others. If you don't generate your custom json file (sample available in the 
config directory), the plugin will not generate any messages.

To be able to create a message on "birth", MISSION_STATISTICS = true has to be enabled on your server.

```json
{
  "configs": [
    {
      "message": "{player[name]}, welcome to {server[server_name]}!\n",
      "on_event": "birth",             -- one of "birth" or "join"
      "display_type": "popup",         -- "chat" or "popup" (popup only possible on "birth")
      "display_time": 20               -- display time for popups only
    }
  ]
}
```
