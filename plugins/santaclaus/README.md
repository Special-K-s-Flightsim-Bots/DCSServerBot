# Plugin "SantaClaus"
Just a little fun plugin that will print the position of Santaclaus every 5 minutes as a message to all on active 
servers.

## Configuration
You can enable or disable the plugin on any server you want. Add a config/plugins/santaclaus.yaml like so:
```yaml
DCS.release_server:
  enabled: true
DCS.missions:
  enabled: false
```
Default is enabled: true

Santaclaus is an optional plugin, you need to activate it in your main.yaml like so:
```yaml
opt_plugins:
  - santaclaus
```

## Discord Commands
| Command       | Parameter                         | Channel               | Role                  | Description                                  |
|---------------|-----------------------------------|-----------------------|-----------------------|----------------------------------------------|
| /whereissanta |                                   | all                   | DCS                   | Shows the position of Santaclaus in Discord. |
