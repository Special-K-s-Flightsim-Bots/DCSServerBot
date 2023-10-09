# Coalitions
If you want to support Blue and Red coalitions in your Discord and your DCS servers, you're good to go!
As there are major changes to how the bot behaves with COALITIONS enabled, I decided to have a separate documentation 
about it. It has redundant information in it, which is usually a bad idea, but I thought it might be easier for you
guys to have everything in one place.<br/>
Coalitions are implemented by slot blocking, but can use the feature of coalition passwords in DCS, too.

> ⚠️ **Attention!**</BR>
> With COALITIONS enabled, some persistent displays will not appear in your server status channels (or will be changed)
> like Player information or Mission Statistics, which would render all the work useless, if you could peek in there and 
> see what is going on. You can still use the commands `/player list` or `/missionstats` in your dedicated coalition 
> channels, but you can't see data from the opposite coalition anymore.

COALITION handling can be enabled in each server individually. So if you only want to enable strict red/blue 
handling in one server, you can do that. Every other server (and their persistent embeds) will not be affected.  

---
## Bot Configuration
There are some specific settings for coalitions that you can set in your configuration:

### bot.yml
a) Greeting Direct Message (DM)
```yaml
# [...]
message_ban: User has been banned on Discord.
message_autodelete: 300
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
  - Admin
  DCS Admin:
  - DCS Admin
  DCS:
  - DCS
  GameMaster:
  - GameMaster      # GameMaster can see both sides, red and blue and fire specific commands to change the mission situation
# [...]
```

### server.yaml
```yaml
My Fancy Server:
  # [...]
  channels:
    status: 1122334455667788
    chat: 8765432187654321      # general chat channel
    admin: 8877665544332211
    blue: 1188227733664455      # chat channel for coalition blue
    red: 8811772266335544       # chat channel for coalition red
  # [...]
  coalitions:
    lock_time: 1 day            # time in which you are not allowed to move to the opposite coalition after leaving one coalition
    allow_players_pool: false   # don't allow access to the players pool
    blue_role: Coalition Blue   # Discord role for the blue coalition
    red_role: Coalition Red     # Discord role for the red coalition
```
> ⚠️ **Attention!**<br/>
> Make sure, that all channels for red and blue coalitions have read access **only** for this coalition and not for 
> @everyone or the other coalition! The CHAT-channels for red and blue are similar to the general chat channel, 
> but they only replicate chat messages that are being sent to that specific coalition in game.
> Unfortunately, it is not possible to chat back yet, as the DCS API doesn't allow it yet.

## Discord Configuration
The bot uses the following **internal** roles to apply specific permissions to commands.
You can change the role names to the ones being used in your discord. That has to be done in the dcsserverbot.ini 
configuration file.

| Role           | Description                                                                                                                                         |
|----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| GameMaster     | People with this role can see both coalitions and run specific commands that are helpful in missions.                                               |
| Coalition Blue | People with this role are members of the blue coalition. See Coalitions below for details.                                                          |
| Coalition Red  | People with this role are members of the red coalition. See Coalitions below for details.                                                           |

## Discord Commands
These discord commands are either exclusively for coalition handling like .join and .leave or have been amended for 
coalition use, which means, that the data they display is filtered to data that belongs to your coalition only.

| Command           | Parameter                | Channel       | Role                   | Description                                                                                               |
|-------------------|--------------------------|---------------|------------------------|-----------------------------------------------------------------------------------------------------------|
| /server password  | \<password\> [coalition] | admin-channel | DCS Admin              | Changes the password of a specific coalition on this server.                                              |
| /player list      |                          | all           | DCS                    | Lists the players currently active on the server (for your coalition only!).                              |
| /mission briefing |                          | all           | DCS                    | Shows the description / briefing of the running mission (for your coalition only!).                       |
| /missionstats     |                          | all           | DCS                    | Display the current mission situation for either red or blue and the achievements in kills and captures.  |

## In-Game Chat Commands

| Command    | Parameter      | Role | Description                    |
|------------|----------------|------|--------------------------------|
| .join      | \<coalition\>  | all  | Join a coalition.              |
| .leave     |                | all  | Leave a coalition.             |
| .red       |                | all  | Join the red coalition.        |
| .blue      |                | all  | Join the blue coalition.       |
| .coalition |                | all  | Shows your current coalition.  |
| .password  |                | all  | Shows your coalition password. |
