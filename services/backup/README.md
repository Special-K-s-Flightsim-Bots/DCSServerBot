# Backup Service
The backup service can be used to backup your bot configuration, the database and whatever you want to backup from your 
servers. It runs on a configurable schedule or can be launched by commands from the [Backup plugin](../../plugins/backup/README.md).

## Configuration
The backup service can be configured with a yaml file /config/services/backup.yaml, that you might need to create.
A sample is in the /config/samples directory.

```yaml
target: G:\My Drive\Backup    # A directory of your choice, best case on a cloud drive
delete_after: never           # Delete the files after x days (never = never) 
backups:
  database:                                   # Backup your database
    path: C:\Program Files\PostgreSQL\15\bin  # path to your postgres installation / bin directory
    password: secret                          # put your postgres database-user password in here
    schedule: 
      times:
      - 03:00                                 # do it every day at 03:00 LT
      days: YYYYYYY
  servers:                                    # Backup your DCS servers
    directories:                              # List of directories to be backed up
    - Config
    - Missions
    - Scripts
    schedule:
      times:
      - 04:00                                 # do it every Sunday at 04:00 LT                       
      days: NNNNNNY
  bot:                                        # Backup of your DCSServerBots configuration
    directories:                              # directories to chose from
    - config
    - reports
    schedule:
      times:                                  # Every night at 03:10 LT
      - 03:10
      days: YYYYYYY
```

> ⚠️ **Attention!**<br>
> The backup service can't do incremental backups yet. So keep that in mind before you fill up your disk.
