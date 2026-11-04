# Profanity filter

The bot can filter player nicknames and the in-game chat against a word list.

```yaml
# config/servers.yaml
profanity_filter: true        # enabled per server, default false
no_join_with_cursename: true  # also block nicknames, default true (needs the filter on)
```

Both keys live in the server's block, so you can filter one server and not another. Putting them
under `DEFAULT:` applies them to every server.

## How it works

DCS has its own censor file — `Data/censor.lua` in the DCS installation. With the filter enabled the
bot writes your word list into that file and keeps the original as `censor.lua.bak`. This needs write
permission on the DCS installation directory, and it applies to the whole installation, not to one
server.

Player names are checked by the bot itself, because the censor file only covers the in-game chat. A
refused nickname sees the message in `messages.message_player_inappropriate_username`.

## The word list

On the first start with `profanity_filter: true` the bot copies `samples/wordlists/<language>.txt` to
`config/profanity.txt`, where `<language>` is the `language` set in `config/main.yaml`. Available
lists: `de`, `en`, `es`, `fr`, `ru`.

Edit `config/profanity.txt` freely: one word per line, lines starting with `#` are ignored. The file
is **not** overwritten once it exists — which also means an updated upstream list will not reach you
automatically.
