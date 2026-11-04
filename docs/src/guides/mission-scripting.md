# Talking to the bot from inside a mission

A mission can send chat messages and embeds to Discord through the bot. All it needs is the bot's
script, loaded once:

```lua
dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
```

!!! warning
    Do not put that line in a *Mission Start* trigger — it clashes with other plugins that load into
    the mission. Use a trigger that fires after the mission has started.

## Chat messages

```lua
dcsbot.sendBotMessage('Hello World', '12345678')
```

- the second argument is the target channel ID, as a **string** — LUA integers overflow the Discord
  snowflake, so never pass a number
- omit it and the message goes to the server's configured chat channel
- a third argument `true` sends the text raw, without the bot's formatting:

```lua
dcsbot.sendBotMessage('**Hello** World', '12345678', true)
```

## Embeds

```lua
title = 'Special K successfully landed at Kutaisi!'
description = 'His 110th attempt, belly down.'
img = 'https://example.com/screenshot.png'
fields = {
  ['Pilot'] = 'sexy as hell',
  ['Speed'] = '130 kn',
  ['Wind'] = 'calm'
}
footer = 'They forgot to put their gear down!'
dcsbot.sendEmbed(title, description, img, fields, footer)
```

Embeds go to the chat channel unless you append a channel ID as the last argument.

## One embed, updated in place

Give the embed a name and update it instead of posting a new one — the name has to be unique per
server:

```lua
dcsbot.updateEmbed('myEmbed', 'RED Coalition captured Kutaisi!', 'After a successful last bombing run …')
```

```lua
dcsbot.updateEmbed('myEmbed', 'Mission Over!', 'RED has won after capturing the last BLUE base Batumi!', img)
```

The first call creates the embed if it does not exist yet; every later call replaces its content. Use
it for a live scoreboard, a mission briefing or a status line.
