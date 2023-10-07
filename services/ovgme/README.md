# OvGME Service

This is the OvGME service, which lets you install packaged modifications (mods) to your DCS server.

## Configuration
The service is configured via yaml. There is an additional [plugin](../../plugins/ovgme/README.md) to provide Discord
commands to operate the service.

```yaml
DEFAULT:
  SavedGames: '%USERPROFILE%\Documents\OvGME\SavedGames'  # folder to store plugins that should be installed into Saved Games
  RootFolder: '%USERPROFILE%\Documents\OvGME\RootFolder'  # folder to store plugins that should go into the base game directories
DCS.openbeta_server:
  packages:
  - name: Community_A-4E-C    # The community A-4E-C model can be used out of the box with this service
    version: latest           # we will always use the latest available version on disk
    source: SavedGames        # the mod will be placed inside the Saved Games folder structure
```
"latest" means, that the package with the highest version number will be installed. If a newer package is provided, it
will be automatically taken on the next restart of the bot or reload of this plugin.<br/>
A strict version number means that this exact version will be installed. If a **newer** version has been installed 
manually (e. g. by updating), the bot will **not** replace the newer version with an older one. A warning will be 
printed instead.

> **ATTENTION:**<br/>
> On every DCS update that is performed by the bot, this plugin will automatically __uninstall__ and __reinstall__ all
> packages configured for the "RootFolder", so that after the update, all packages should be up-to-date and reapplied.

### Package Structure
All packages must have a strict naming convention, that follows (package_name)_v(version).
Packages might be provided either as a directory or as a ZIP file. The structure of the directory / ZIP file must follow
the OvGME rules, which means, that the directory structure inside the package must be the same as how the package will
be unpacked over either the Saved Games\DCS(...) folder or over the DCS World installation directory.

### Backups
Every installed package creates a backup folder. This is stored below the directory provided in the configuration
("SavedGames" or "RootFolder").<br/>
The backup will be found in<p> 
```
.(instance_name)
|_ (package)_v(version)
    |_install.log
    |_ ...
```

`install.log` contains a detailed log about every file that has been installed into the DCS World directories.<br/>
Besides that, the folder contains all files that are being overwritten by the package on installation for a later 
restore.
