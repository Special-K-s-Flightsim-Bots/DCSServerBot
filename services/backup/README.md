# Backup Service
The backup service can be used to back up your bot configuration, the database and whatever you want to back up from 
your servers. It runs on a configurable schedule or can be launched by commands from the [Backup plugin](../../plugins/backup/README.md).

## Configuration
To activate the backup service, you need to add a line in your main.yaml like so:
```yaml
opt_plugins:
  - backup
```

The backup service itself can be configured with a yaml file /config/services/backup.yaml, that you might need to 
create. A sample is in the ./samples directory.

```yaml
target: G:\My Drive\Backup    # A directory of your choice, best case on a cloud drive
delete_after: never           # Delete the files after x days (never = never) 
backups:
  database:                                   # Backup your database
    username: postgres                        # Optional: use a different user to back up the database
    password: secret                          # Optional: password of that user
    path: C:\Program Files\PostgreSQL\17\bin  # Optional: path to your bin directory of your postgres database (will be auto-detected otherwise)
    schedule: 
      times:
      - 03:00                                 # do it every day at 03:00 LT
      days: YYYYYYY
  servers:                                    # Backup your DCS servers
    directories:                              # List of directories to be backed up
    - Config
    - Missions/Scripts                        # only back up this subdirectory
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

> [!NOTE]
> The backup service can't do incremental backups yet. So keep that in mind before you fill up your disk.
