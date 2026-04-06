# Extension "Trackfile"
Simple extension to upload a trackfile on mission change to either Discord or a path (e.g. cloud drive).

## Configuration
You can use `/extension enable <Trackfile>` to enable the extension.
This will add an entry in your `nodes.yaml` like so:
```yaml
MyNode:
  # [...]
  instances:
    DCS.dcs_serverrelease:
      # [...]
      extensions:
        Trackfile:
          enabled: true # Optional: you can disable the extension with false
          target: '<id:112233445566778899>'     # channel id or directory
```
The extension will raise a warning, if track files are disabled for this server.
