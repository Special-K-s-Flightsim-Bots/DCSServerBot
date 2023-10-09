# Bot Service
This is the Discord bot implementation, the one that's providing all the commands and embeds that are displayed in your
Discord server.

## Configuration
The configuration is quite basic, please check out the installation guide in the main [README](../../README.md).
After you have installed DCSServerBot, a file named config/services/bot.yaml will be there for you to amend to your
requirements:

```yaml
token: SECRET DISCORD TOKEN COMES HERE          # Your TOKEN, do not change this
owner: 1122334455667788                         # Your bots Discord ID (Copy UserID)
automatch: false                                # don't use the automatching of Discord / DCS IDs (default: true)
autoban: false                                  # do not auto-ban people from your DCS servers that left your Discord guild (default: false)
message_ban: User has been banned on Discord.   # Message that will be added as a reason to the DCS ban, if autoban is true
message_autodelete: 300                         # Very few Discord messages that are not displayed privately, will vanish after this time.
reports:
  num_workers: 4                                # Number of worker threads for reports / graphs
  cjk_font: KR                                  # If you want to use a CJK font on the graphs, you need to specify it in here (that it gets loaded).
discord_status: Managing DCS servers ...        # Optional: message to be displayed on your bots status (WIP, static for now)
audit_channel: 88776655443322                   # a channel to send audit-events to
roles:                                          # Role mapping. All your internal bot roles need to be mapped to real roles in your Discord!
  Admin:
  - Admin                                       # Map the internal role "Admin" to a Discord role "Admin"
  DCS Admin:                                    # Map the internal role "DCS Admin" to a Discord role "Moderator" and "Staff"
  - Moderator
  - Staff
  DCS:                                          # Map the internal role "DCS" to everyone in your Discord (keep an eye on the @ here!)
  - @everyone
  GameMaster:                                   # Map the internal role "GameMaster" on the "Staff" role in your Discord.
  - Staff
```

> ⚠️ **Attention!**<br>
> Never ever share your Discord TOKEN with anyone. If you plan to check-in your configuration to GitHub, don't do that
> for the Discord TOKEN. GitHub will automatically revoke it from Discord for security reasons.
