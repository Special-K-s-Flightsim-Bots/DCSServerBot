# Extension "Persistence"
This extension allows you to persist mission information over the course of a mission / server restart.
As there are many great persistence libraries out there, like [DSMC](../DSMC/README.md) or even the default persistency
of DCS World, this extension only offers a small subset of what these libraries offer.

## Configuration
To use this extension, use `/extension enable <Persistence>`. 
This will add an entry in your `nodes.yaml` like so:
```yaml
MyNode:
  # [...]
  instances:
    DCS.dcs_serverrelease:
      # [...]
      extensions:
        Persistence:
          enabled: true # Default is true.
          path: Saves   # Optional, default is "Saves". Will save your persistence data into Missions/Saves.
```

This will create a folder called `Saves` in your Missions folder if it does not exist yet.
The extension will automatically save your mission time and mission date during the cause of your mission.
It will then automatically amend your mission on startup to restore the time and date of your mission.

> [!NOTE]
> Date and time will be saved for every mission individually.

To reset the stored persistence data, either delete the respective ".pkl" file from your Missions/Saves folder
or use `/mission rollback` in your Admin channel. You can also just disable the extension 
with `/extension disable <Persistence>`.
