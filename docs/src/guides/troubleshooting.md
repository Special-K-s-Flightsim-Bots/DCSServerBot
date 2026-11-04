# Troubleshooting and repair

## The bot does not start

Work through these in order:

1. **Is the token set?** Without it the bot stops with *"No Discord token found!"*. See
   [Secrets and token](../secrets.md).
2. **Is the database reachable?** `config/main.yaml` holds the connection URL; the password lives in
   `config/.secret/database.pkl`. A wrong port, a stopped service or a missing `postgresql` user all
   look the same in the log: connection refused.
3. **Read the log.** `logs/` in the installation directory, newest file first. The stack trace names
   the plugin or service that failed, and the bot continues without a plugin that fails to load.
4. **Run the repair script** (see below).

## Repair

If the bot fails to start after an update, or an update cannot be applied at all because a file it
expects was changed, run the repair script from the installation directory:

```
repair.cmd          # Windows
./repair.sh         # Linux
```

It re-creates the Python environment and restores the files the updater manages. Your
configuration, database and missions are untouched.

## A plugin or service refuses to start

A plugin or service starts **only** when its configuration file exists — a missing
`config/plugins/<name>.yaml` is the usual cause, not a code fault. The log line names the file it
looked for.

## Show the stored secrets

`run.cmd -s` prints the stored token, database and DCS passwords before starting. Useful when a
credential was entered once and then hidden by the secret store.

## Starting the bot at boot

Create a shortcut to `run.cmd` in the Windows autostart folder: press `Win+R`, type
`shell:startup`, press Enter, put the shortcut there.
