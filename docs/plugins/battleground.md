---
title: Battleground
parent: Plugin System
nav_order: 0
---

# Plugin "Battlegound"
The Battleground plugin adds commands for the [DCSBattleground](https://github.com/Frigondin/DCSBattleground/) tool.</br> 
The plugin use the coalition feature for communication with the database so you need to set the "COALITION_BLUE_CHANNEL" 
and "COALITION_RED_CHANNEL" for your servers in dcsserverbot.ini

## DCSBattlegound's configuration
DCSBattleground is a sneaker's fork created by Frigondin, you can find all information about it [here](https://github.com/Frigondin/DCSBattleground/).</br>
You can find a wiki with some examples and an introduction to the tool [here](https://github.com/Frigondin/DCSBattleground/wiki).</br>

An extension for DCSBattleground will be created at a later stage, so it can't be auto-started by DCSServerBot for now.

## Discord Commands

| Command             | Parameter                                  | Channel                 | Role | Description                                                                                                                    |
|---------------------|--------------------------------------------|-------------------------|------|--------------------------------------------------------------------------------------------------------------------------------|
| /battleground recon | \<name\> \<mgrs\> Attachment: screenshots  | coalition chat channels | DCS  | Add recon data with screenshots to DCSBattleground. Coordinates needs to be in MGRS-format and screeshots in JPEG, GIF or PNG. |

## Database Tables

- [BG_GEOMETRY](../database.md#bg_geometry)
