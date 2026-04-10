# Extension "GitHub"
Simple extension to clone or update a given repository into a directory on your server.

## Configuration
You can use `/extension enable <GitHub>` to enable the extension.
This will add an entry in your `nodes.yaml` like so:
```yaml
MyNode:
  # [...]
  instances:
    DCS.dcs_serverrelease:
      # [...]
      extensions:
        GitHub:
          repo: 'https://github.com/mrSkortch/MissionScriptingTools.git'
          branch: master  # optional branch, default is the repositories default branch (e. g. main or master)
          target: '{server.instance.home}\Missions\Scripts'
          filter: '*.lua'
```
