---
layout: default
title: README
nav_section: plugins/battleground2
---

# Plugin "Battlegound2"
The Battleground plugin adds commands for the [DCSBattleground](https://github.com/Frigondin/DCSBattleground/) tool.</br> 
The plugin use the [Coalition](../../COALITIONS.md) feature for communication with the database, so you need to set 
the "blue" and "red" channel in your servers.yaml.

## DCSBattlegound's configuration
As Battleground2 is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - battleground2
```

DCSBattleground is a [Sneaker](https://github.com/b1naryth1ef/sneaker) fork created by Frigondin. 
You can find all information about it [here](https://github.com/Frigondin/DCSBattleground/).</br>
There is also a wiki with some examples and an introduction to the tool [here](https://github.com/Frigondin/DCSBattleground/wiki).</br>

An extension for DCSBattleground will be created at a later stage, so it can't be auto-started by DCSServerBot for now.

## Discord Commands

| Command             | Parameter                                  | Channel                 | Role | Description                                                                                                                    |
|---------------------|--------------------------------------------|-------------------------|------|--------------------------------------------------------------------------------------------------------------------------------|
| /battleground recon | \<name\> \<mgrs\> Attachment: screenshots  | coalition chat channels | DCS  | Add recon data with screenshots to DCSBattleground. Coordinates needs to be in MGRS-format and screeshots in JPEG, GIF or PNG. |

## Tables
### BG_GEOMETRY2
| Column       | Type             | Description                                                       |
|--------------|------------------|-------------------------------------------------------------------|
| #id          | INTEGER NOT NULL | Auto-increment ID.                                                |
| node         | TEXT NOT NULL    | server_name of the server the screenshots should be published to. |
| data         | JSON NOT NULL    | Raw data of the geometry (Title, author, fields)                  |
| time         | TIMESTAMP        | Timestamp of the created / updated object                         |

### BG_MISSION
| Column       | Type             | Description                                         |
|--------------|------------------|-----------------------------------------------------|
| #id          | INTEGER NOT NULL | Auto-increment ID.                                  |
| node         | TEXT NOT NULL    | server_name of the server associated to the mission |
| data         | JSON NOT NULL    | Raw data of the mission (Title, author, fields)     |
| time         | TIMESTAMP        | Timestamp of the created / updated object           |

### BG_TASK
| Column       | Type             | Description                                         |
|--------------|------------------|-----------------------------------------------------|
| #id          | INTEGER NOT NULL | Auto-increment ID.                                  |
| #id_mission  | INTEGER NOT NULL | ID of mission associated to the task                |
| node         | TEXT NOT NULL    | server_name of the server associated to the mission |
| data         | JSON NOT NULL    | Raw data of the task (Title, author, fields)        |
| time         | TIMESTAMP        | Timestamp of the created / updated object           |

### BG_TASK_USER_RLTN
| Column       | Type             | Description                                 |
|--------------|------------------|---------------------------------------------|
| #id          | INTEGER NOT NULL | Auto-increment ID.                          |
| #id_task     | INTEGER NOT NULL | ID of task associated to the the enrollment |
| discord_id   | BIGINT NOT NULL  | Discord id of the enrollated player         |
| time         | TIMESTAMP        | Timestamp of the created / updated object   |
