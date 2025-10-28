# Plugin MessageOfTheDay (MOTD)
This plugin adds a message of the day to the server, that is displayed either on join or when you hop into a plane.

## Configuration
As MOTD is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - motd
```

The plugin itself is configured via yaml, as many others. If you don't generate your custom yaml file (sample available 
in the samples directory), the plugin will not generate any messages.

To be able to create a message on "birth", MISSION_STATISTICS = true has to be enabled on your server.

```yaml
DEFAULT:
  on_birth:                   # message will fire when someone enters a plane
    report: greeting.json     # the respective report will be used (see Reporting Framework)
    display_type: popup       # the message will generate a popup ..
    display_time: 20          # .. which lasts for 20 seconds
  nudge:
    - delay: 3600             # the following message will be displayed every 3600 seconds (1h)
      message: "All members, be aware of our weekly mission, every Sunday at 1700 UTC!"
      recipients: 'Members'   # the message only goes to specific recipients (see below)
      display_type: chat      # the message will be displayed in the in-game chat
    - delay: 120              # this message will be displayed every 2 mins
      message: "To see your stats, you can link your user by using /linkme in your discord!"
      recipients: '!@everyone' # and will be sent to anybody that is not linked yet (has not the discord role @everyone)
      display_type: popup     # Message will be a popup
DCS.dcs_serverrelease:
  on_join:                    # The message will be displayed in the in-game chat on join of the server.
    message: Welcome to our public server! Teamkills will be punished.
```
> recipients can be a list of Discord groups that the player either is part of or not (prepend with !).<br>
> !@everyone means, this message is for people that are not a member of your Discord.

If you want to play sounds, make sure that you loaded them into the mission first (see [MizEdit](../../extensions/mizedit/README.md)).

### Optional: Layout for multiple Recipient Groups
```yaml
      nudge:
        delay: 60     # this time, only one delay is set. You can even use a list in here.
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

### Optional: Layout for nudge-messages at different times
```yaml
  nudge:
    - delay: 3600             # the following message will be displayed every 3600 seconds (1h)
      message: "All members, be aware of our weekly mission, every Sunday at 1700 UTC!"
      recipients: 'Members'   # the message only goes to specific recipients (see below)
      display_type: chat      # the message will be displayed in the in-game chat
    - delay: 120              # this message will be displayed every 2 mins
      message: "To see your stats, you can link your user by using /linkme in your discord!"
      recipients: '!@everyone' # and will be sent to anybody that is not linked yet (has not the discord role @everyone)
      display_type: popup     # Message will be a popup
```

### Optional: Random pick messages
```yaml
      nudge:
        delay: 3600     
        random: true  # If set to true (default: false), a random pick will be made out of the messages list.
        messages:     # Please keep in mind that, if you use recipients, only those recipients of that specific message will receive.
        - message: This is the first test message!
          display_type: popup
          display_time: 20
        - message": This is the second test message!
          display_type: popup
          display_time: 20
```
