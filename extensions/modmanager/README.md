# Extension "ModManager"
This little extension checks, if you have any requiredModules in your miz file and shows them in the server status
embed in Discord. Nice addition for your users, if you show them what to install to fly on your server.

## Configuration
The configuration is as simple as it sounds:
```yaml
MyNode:
  # [...]
  instances:
    DCS.dcs_serverrelease:
      # [...]
      extensions:
        ModManager:
          enabled: true
```
