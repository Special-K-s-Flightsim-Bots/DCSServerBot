# Plugin MessageOfTheDay (MOTD)
This plugin adds a message of the day to the server, that is displayed either on join or when you hop into a plane.

## Configuration
The plugin is configured via yaml, as many others. If you don't generate your custom yaml file (sample available in the 
config/samples directory), the plugin will not generate any messages.

To be able to create a message on "birth", MISSION_STATISTICS = true has to be enabled on your server.

```yaml
DEFAULT:
  on_birth:                   # message will fire when someone enters a plane
    report: greeting.json     # the respective report will be used (see Reporting Framework)
    display_type: popup       # the message will generate a popup ..
    display_time: 20          # .. which lasts for 20 seconds
  nudge:
    delay: 3600               # the following message will be displayed every 3600 seconds (1h)
    message: "This awesome server is presented to you by http://discord.gg/myfancylink.\n
      Come and join us!"
    recipients: '!@everyone'  # the message only goes to specific recipients (see below)
    display_type: chat        # the message will be displayed in the in-game chat
DCS.openbeta_server:
  on_join:                    # The message will be displayed in the in-game chat on join of the server.
    message: Welcome to our public server! Teamkills will be punished.
```
> recipients can be a list of Discord groups that the player either is part of or not (prepend with !).<br>
> !@everyone means, this message is for people that are not a member of your Discord.

If you want to play sounds, make sure that you loaded them into the mission first (see [MizEdit](../../extensions/MizEdit.md)).

### Optional Layout for multiple Recipient Groups
```yaml
      nudge:
        delay: 60
        messages:
        - message: This awesome server is presented to you by https://discord.gg/myfancylink.\nCome and join us!
          recipients": "!@everyone"
          display_type: popup
          display_time: 20
        - message": Glad to have you guys here!
          recipients: DCS Admin
          display_type: popup
          display_time: 20
```
