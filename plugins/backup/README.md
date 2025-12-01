# Plugin "Backup"
This plugin can only be used together with the [Backup Service](../../services/backup/README.md).
It provides the Discord commands to manually start a backup or restore.

## Configuration
See [Backup Service](../../services/backup/README.md).

## Discord Commands

| Command  | Parameter | Channel       | Role  | Description                                                                         |
|----------|-----------|---------------|-------|-------------------------------------------------------------------------------------|
| /backup  | what      | admin-channel | Admin | Starts a backup of the selected item according to the Backup Service configuration. |
| /restore | what date | admin-channel | Admin | Restores data from a backup.                                                        |
