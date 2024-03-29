---
title: OvGME
parent: Plugin System
nav_order: 0
---

# Plugin "OvGME"

This plugin lets you install additional packages (aka mods) into your DCS World servers.

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
      "installation": "DCS.release_server",
      "packages": [                                                   -- list of packages to install
          {"name": "Community_A-4E-C", "version": "2.0.1", "source": "SavedGames"}
      ]
    },
    {
      "installation": "instance2",
      "packages": [
          {"name": "Community_A-4E-C", "version": "latest", "source": "SavedGames"}
      ]
    }
  ]
}
```

"latest" means, that the package with the highest version number will be installed. If a newer package is provided, it
will be automatically taken on the next restart of the bot or reload of this plugin.

A strict version number means that this exact version will be installed. If a **newer** version has been installed 
manually (e. g. by updating), the bot will **not** replace the newer version with an older one. A warning will be 
printed instead.

{: .note }
> On every DCS update that is performed by the bot, this plugin will automatically __uninstall__ and __reinstall__ all
> configured packages, so that after the update, all packages should be up-to-date and reapplied.<br/>
> This does **not** happen, if a DCS update is being run manually at the moment (to be implemented at a later stage).

### Package Structure

All packages must have a strict naming convention, that follows (package_name)_v(version).
Packages might be provided either as a directory or as a ZIP file. The structure of the directory / ZIP file must follow
the OvGME rules, which means, that the directory structure inside the package must be the same as how the package will
be unpacked over either the Saved Games\DCS(...) folder or over the DCS World installation directory.

### Backups

Every installed package creates a backup folder. This is stored below the directory provided in the configuration
("SavedGames" or "RootFolder").

The backup will be found in

```
.(instance_name)
|_ (package)_v(version)
    |_install.log<
    |_ ...
```

`install.log` contains a detailed log about every file that has been installed into the DCS World directories.

Besides that, the folder contains all files that are being overwritten by the package on installation for a later 
restore.

## Discord Commands

| Command         | Parameter                | Channel       | Role             | Description                                                                     |
|-----------------|--------------------------|---------------|------------------|---------------------------------------------------------------------------------|
| .packages       |                          | admin-channel | Admin, DCS Admin | Lists all installed packages of this server and lets you update or remove them. |
| .add_package    |                          | admin-channel | Admin            | Installs a specific package.                                                    |
