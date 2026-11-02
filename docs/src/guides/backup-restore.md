# Backup and restore

The bot backs up its database, its configuration and the DCS instance directories. Two components do
it, and they are independent of each other:

- the **Backup service** ([backup service](../services/backup.md)) — scheduled backups per node
- the **Backup plugin** ([backup plugin](../plugins/backup.md)) — manual backups from Discord

Both write a single archive you can move to another machine.

## Auto-restore

1. Create a folder named `restore` in the bot's installation directory.
2. Copy the backup file into it.
3. Point `config/nodes.yaml` at the database the restored data should live in.
4. Start the bot and let the restore run to completion.

The bot reads the archive on startup and restores:

- the database
- the bot configuration
- the instance directories, including missions and their configuration (what exactly is included is
  described in the backup service's page)

!!! tip
    Auto-restore is the simplest way to move a database from one PC to another. Keep the PostgreSQL
    master password at hand — the restore asks for it.
