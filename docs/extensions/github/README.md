# Extension "GitHub"
Simple extension to clone or update a given repository into a directory on your server.

## Configuration
To enable track file upload, a change in nodes.yaml is needed:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        GitHub:
          repo: 'https://github.com/mrSkortch/MissionScriptingTools.git'
          branch: master  # optional branch, default is the repositories default branch (e. g. main or master)
          target: '{server.instance.home}\Missions\Scripts'
          filter: '*.lua'
```
