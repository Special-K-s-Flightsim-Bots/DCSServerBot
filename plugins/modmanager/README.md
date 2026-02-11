# Plugin ModManager
This plugin belongs to the [ModManager Service](../../services/modmanager/README.md) that lets you install additional 
packages (aka mods) into your DCS World servers. Read there for further details.</br>

## Discord Commands

| Command         | Parameter            | Channel       | Role      | Description                                                                           |
|-----------------|----------------------|---------------|-----------|---------------------------------------------------------------------------------------|
| /mods manage    |                      | admin-channel | Admin     | Lists all installed packages of this server and lets you update or remove them.       |
| /mods install   | mod                  | admin-channel | Admin     | Installs a specific mod onto the selected server.                                     |
| /mods uninstall | mod                  | admin-channel | Admin     | Uninstalls a specific mod from the selected server.                                   |
| /mods list      |                      | admin-channel | DCS Admin | Lists the installed mods on this server.                                              |
| /mods download  | folder url [version] | admin-channel | Admin     | Download a package from a GitHub URL or any other URL. Version only works for GitHub. |

> [!NOTE]
> If `restricted_commands` is set to `true` in your nodes.yaml, you can only install and uninstall mods that are 
> in the SavedGames folder of your ModManager directory.
> This is to allow hosters to open mod commands to DCS Admin users without them being able to break the main
> DCS installation.
