# Several servers on one PC

One node can run any number of DCS instances. Two things have to be unique per instance: the
communication port between bot and DCS, and the DCS port itself.

## Ports

In `config/nodes.yaml`, each instance gets its own `bot_port` (default 6666):

```yaml
instances:
  DCS.dcs_serverrelease:
    bot_port: 6666        # bot → DCS communication
    dcs_port: 10308       # the port players connect to
  DCS.dcs_serveropenbeta:
    bot_port: 6667
    dcs_port: 10309
```

!!! warning
    `bot_port` is **not** your DCS port. Never expose it: it is the channel the bot uses to control
    the DCS process.

## Channels

Give each server its own Discord channels in `config/servers.yaml` — `status` and `chat`, optionally
`admin` — or let them share the central channels from `config/services/bot.yaml` if you want
everything in one place.

## Adding another instance

1. Copy the instance's Saved Games directory, or start a fresh DCS server installation.
2. Add the instance under `instances:` in `config/nodes.yaml` with unique ports.
3. Add the server to `config/servers.yaml`.
4. Restart the bot.

`/node add_instance` does the same from Discord, and the bot autodetects DCS installations it finds
on the machine.

## Another PC?

If the servers run on different machines, that is a multi-node installation — see
[Multi-node](../multinode.md).
