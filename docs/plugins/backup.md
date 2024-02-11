---
title: Backup
parent: Plugin System
nav_order: 0
---

# Plugin "Backup"
This plugin can only be used together with the [Backup Service](./services/backup.md).
It provides the /backup command to manually start a backup.

## Configuration
See [Backup Service](./services/backup.md).

## Discord Commands

| Command    | Parameter | Channel       | Role   | Description                                                                         |
|------------|-----------|---------------|--------|-------------------------------------------------------------------------------------|
| /backup    | what      | all           | Admin  | Starts a backup of the selected item according to the Backup Service configuration. |
