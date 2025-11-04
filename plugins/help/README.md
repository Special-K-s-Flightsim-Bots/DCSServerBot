# Plugin Help
This is the help plugin. It provides interactive help in Discord and the in-game chat.

> [!NOTE]
> Only commands eligible to the caller will be displayed.

## Discord Commands
| Command  | Parameter                     | Channel | Role  | Description                                                                          |
|----------|-------------------------------|---------|-------|--------------------------------------------------------------------------------------|
| /help    | [command]                     | all     | DCS   | Display help with a selection of all modules or for a specific command, if provided. |
| /doc     | <what> [fmt] [role] [channel] | all     | Admin | See below.                                                                           |

The `/doc` command can generate different helpful information:
1. Command Overview
   Creates an Excel file with all Discord and in-game commands, or sends them to a channel of your choice. 
   You can specify a role for which these commands are applicable, such as displaying public commands only in publicly  
   accessible channels.
2. Server Config Sheet
   Produces an Excel sheet detailing your current server configuration along with all ports.
3. Firewall Ruleset
   Generates a PowerShell script for a firewall ruleset that can be executed to configure your firewall settings.

## In-Game Chat Commands
| Command | Parameter | Role                  | Description                                                                     |
|---------|-----------|-----------------------|---------------------------------------------------------------------------------|
| -help   |           | all                   | Print a list of commands you can fire either in the in-game chat or as a popup. |
