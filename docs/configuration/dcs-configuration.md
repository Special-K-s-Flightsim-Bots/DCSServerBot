---
title: DCS configuration
parent: Configuration
nav_order: 2
---

# DCS configuration

## DCS Hook Configuration

The DCS World integration is done via Hooks. They are being installed automatically into your configured DCS servers by the bot.

## Auto-Banning

The bot supports automatically bans / unbans of players from the configured DCS servers, as soon as they leave / join your Discord guild.
If you like that feature, set _AUTOBAN = true_ in dcsserverbot.ini (default = false).

However, players that are being banned from your Discord or that are being detected as hackers are auto-banned from all your configured DCS servers without prior notice.

## Additional Security Features

Players that have no pilot ID (empty) or that share an account with others, will not be able to join your DCS server. 
This is not configurable, it's a general rule (and a good one in my eyes).

## Custom MissionScripting.lua

If you want to use a **custom MissionScripting.lua** that has more sanitization (for instance for LotAtc, Moose, 
OverlordBot or the like) or additional lines to be loaded (for instance for LotAtc, or DCS-gRPC), just place the 
MissionScripting.lua of your choice in the config directory of the bot. It will be replaced on every bot startup then.

## Desanitization

DCSServerBot desanitizes your MissionScripting environment. That means, it changes entries in {DCS_INSTALLATION}\Scripts\MissionScripting.lua.
If you use any other method of desanitization, DCSServerBot checks, if additional desanitizations are needed and conducts them.
**To be able to do so, you must change the permissions on the DCS-installation directory. Give the User group write permissions for instance.**
Your MissionScripting.lua will look like this afterwards:

```lua
do
    --sanitizeModule('os')
    --sanitizeModule('io')
    --sanitizeModule('lfs')
    --_G['require'] = nil
    _G['loadlib'] = nil
    --_G['package'] = nil
end
```
