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

## Tables
### BG_GEOMETRY
| Column       | Type             | Description                                                                       |
|--------------|------------------|-----------------------------------------------------------------------------------|
| #id          | INTEGER NOT NULL | Auto-increment ID.                                                                |
| type         | TEXT NOT NULL    | Type of geometry to be displayed in DCSBattleground                               |
| name         | TEXT             | Name of geometry, if null the ID is used in DCSBattlegound                        |
| posmgrs      | TEXT             | Coordinates, the screenshots should appear on in MGRS format.                     |
| screenshot   | TEXT[]           | List of screenshots                                                               |
| side         | TEXT NOT NULL    | Coalition side the screenshot should be displayed to.                             |
| server       | TEXT NOT NULL    | server_name of the server the screenshots should be published to.                 |
| position     | NUMERIC          | Used by DCSBattlegound to store markpoints                                        |
| points       | NUMERIC          | Used by DCSBattlegound to store zones and waypoints                               |
| center       | NUMERIC          | Used by DCSBattlegound to store circles                                           |
| radius       | NUMERIC          | Used by DCSBattlegound to store circles                                           |
| discordname  | TEXT NOT NULL    | Discord user that added the screenshots or draw in DCSBattleground                |
| avatar       | TEXT NOT NULL    | Discord avatar of the user that added the screenshots or draw in DCSBattleground. |
