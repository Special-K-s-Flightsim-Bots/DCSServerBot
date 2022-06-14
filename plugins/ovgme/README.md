# Plugin OvGME
This plugin lets you install additional packages (aka mods) into your DCS World servers.</br>
The plugin creates backup of files that have been replaced by the mod. Install, update and uninstallation is supported.

## Configuration
The plugin is configured via JSON, as many others. If there is no JSON file, the plugin will do nothing.

```json
{
  "configs": [
    {
      "SavedGames": "%USERPROFILE%\\Documents\\OvGME\\SavedGames",    -- folder to store plugins that should be installed into Saved Games
      "RootFolder": "%USERPROFILE%\\Documents\\OvGME\\RootFolder"     -- folder to store plugins that should go into the base game directories
    },
    {
      "installation": "instance2",
      "packages": [                                                   -- list of packages to install
          {"name": "Community_A-4E-C", "version": "2.0.1", "source": "SavedGames"}
      ]
    },
    {
      "installation": "testdriver",
      "packages": [
          {"name": "Community_A-4E-C", "version": "latest", "source": "SavedGames"}
      ]
    }
  ]
}
```

## Discord Commands

| Command         | Parameter                | Channel       | Role             | Description                                  |
|-----------------|--------------------------|---------------|------------------|----------------------------------------------|
| .packages       |                          | admin-channel | Admin, DCS Admin | Lists all installed packages of that server. |
| .add_package    |                          | admin-channel | Admin            | Installs a specific package.                 |
| .remove_package |                          | admin-channel | Admin            | Uninstalls a specific package.               |
