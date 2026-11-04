# Bans and auto-ban

## Banning from Discord and from the servers

Players banned from your Discord can be banned from your DCS servers automatically, and unbanned
again when they leave or rejoin your guild. That link is off by default and is switched on in
`config/services/bot.yaml`:

```yaml
autoban: true                 # ban/unban on the servers when someone leaves/joins your Discord
no_dcs_autoban: false         # true = do NOT mirror Discord bans onto the servers
```

Independently of that setting, players who are banned from your Discord **or detected as cheaters
by DGSA** are auto-banned on all of your servers. `no_dcs_autoban: true` stops that too.

`message_ban` in the same file is the reason a banned player sees when they try to join.

## What the bot refuses without asking you

Two rules always apply and are not configurable:

- a player with **no pilot ID** (empty or whitespace) cannot join;
- a player joining from an **IP a banned user has used before** is rejected (ban avoidance) — you get
  a message in the Discord admin channel about it.

## Smart bans

The bot correlates bans with IP addresses, which is what makes ban avoidance work. In networks where
IPv4 addresses are shared between many users — mobile carriers in some countries, for instance —
that produces false positives. `smart_bans: false` in the server's block in `config/servers.yaml`
turns the correlation off for that server.

## Manual bans

Bans live in the bot's database, not in DCS. `/dcs ban`, `/dcs unban` and `/dcs bans` manage them
(role: DCS Admin). A ban reason is shown to the player on the join attempt.
