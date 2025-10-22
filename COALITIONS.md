# Coalitions
If you want to support Blue and Red coalitions in your Discord and your DCS servers, you're good to go!
As there are major changes to how the bot behaves with COALITIONS enabled, I decided to have separate documentation 
about it.

> [!IMPORTANT]
> As of version 2.9.21, DCS World has its own coalition locking feature.
> DCSServerBot uses this system from now on, with some personal add-ons.
> This has a slight impact on how the locking mechanism works.
> 
> In the past, DCSServerBot had a cooldown lock, meaning you could not join the opposite coalition for a specific time
> after you **left** your coalition.<br>
> DCS now has a time-based lock.
> You cannot switch coalitions at all in-between of a specific lock time.
> If you are able to switch coalitions, though, you can directly switch to the opposite coalition.
> Keep this in mind whenever you plan the lock times.

> [!NOTE]
> With COALITIONS enabled, some persistent displays will not appear in your server status channels (or will be changed)
> like Player information or Mission Statistics, which would render all the work useless, if you could peek in there and 
> see what's going on. You can still use commands like `/player list` or `/missionstats` in your dedicated coalition 
> channels, but you can't see data from the opposite coalition anymore.

COALITION handling can be enabled in each server individually. 
If you only want to enable strict red/blue handling in one server, you can do that. 
Every other server (and their persistent embeds) will not be affected.  

---
## Bot Configuration
There are some specific settings for coalitions that you can set in your configuration.
Please do **not** set the coalition-specific settings in serverSettings.lua. 
The bot takes care of those.

### bot.yaml
a) Greeting Direct Message (DM)
```yaml
# [...]
greeting_dm: This server has a coalition system enabled. Please use .red or .blue in the in-game chat to join a coalition.  
# [...]
```
`greeting_dm` is not mandatory and not linked to coalitions, but it is recommended to tell your new joiners to join a
coalition.

b) Roles for Coalitions
```yaml
# [...]
roles:
  Admin:
  - 1234567890123456789 # role "Admin"
  DCS Admin:
  - 9876543219876543210 # role "DCS Admin"
  DCS:
  - 1122334455667788990 # role "DCS"
  GameMaster:
  - 9988776655443322110 # role "GameMaster"      # GameMaster can see both sides, red and blue and fire specific commands to change the mission situation
# [...]
```

### server.yaml
```yaml
My Fancy Server:
  # [...]
  channels:
    status: 1122334455667788
    chat: 8765432187654321          # general chat channel
    admin: 8877665544332211
    blue: 1188227733664455          # chat channel for coalition blue
    blue_events: 987651234987651234 # Optional: to separate in-game event from chat messages (default: take blue instead).
    red: 8811772266335544           # chat channel for coalition red
    red_events: 123459876123459876  # Optional: same as blue_events for red.
  # [...]
  coalitions:
    lock_time: 1 day            # time in which you are not allowed to change coalitions.
    allow_players_pool: false   # don't allow access to the players pool
    blue_role: 1234123412341234 # Discord role for the blue coalition
    red_role: 43214321432143210 # Discord role for the red coalition
```
> [!IMPORTANT]
> Make sure that all channels for red and blue coalitions have read access **only** for this coalition and not for 
> @everyone or the other coalition! The CHAT-channels for red and blue are similar to the general chat channel, 
> but they only replicate chat messages that are being sent to that specific coalition in game.
> Unfortunately, it is not possible to chat back yet, as the DCS API doesn't allow it yet.

## Discord Configuration
The bot uses the following **internal** roles to apply specific permissions to commands.
You can change the role names to the ones being used in your discord. 
That has to be done in the bot.yaml for general roles like GameMaster and for specific ones in the servers.yaml 
configuration file.

| Role       | Description                                                                                                                                         |
|------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| GameMaster | People with this role can see both coalitions and run specific commands that are helpful in missions.                                               |
| blue_role  | People with this role are members of the blue coalition. See Coalitions below for details.                                                          |
| red_role   | People with this role are members of the red coalition. See Coalitions below for details.                                                           |

## Discord Commands
These discord commands are either exclusively for coalition handling like /reset_coalitions or have been amended for 
coalition use, which means that the data they display is filtered to data that belongs to your coalition only.

| Command           | Parameter            | Channel       | Role                  | Description                                                                                              |
|-------------------|----------------------|---------------|-----------------------|----------------------------------------------------------------------------------------------------------|
| /server password  | password [coalition] | admin-channel | DCS Admin             | Changes the password of a specific coalition on this server.                                             |
| /player list      |                      | all           | DCS                   | Lists the players currently active on the server (for your coalition only!).                             |
| /mission briefing |                      | all           | DCS                   | Shows the description / briefing of the running mission (for your coalition only!).                      |
| /missionstats     |                      | all           | DCS                   | Display the current mission situation for either red or blue and the achievements in kills and captures. |
| /reset_coalition  | server player        | admin-channel | DCS Admin, GameMaster | Resets the coalition cooldown for a specific user on a specific server.                                  |
| /reset_coalitions | [server]             | all           | DCS Admin, GameMaster | Resets all coalition cooldowns (optional: on a specific server).                                         |

## In-Game Chat Commands

| Command    | Parameter      | Role      | Description                                        |
|------------|----------------|-----------|----------------------------------------------------|
| -coalition |                | all       | Shows your current coalition (if you have joined). |
| -password  |                | red/ blue | Shows your coalition password (if set).            |
