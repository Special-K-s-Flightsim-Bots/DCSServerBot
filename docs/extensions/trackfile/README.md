# Extension "Trackfile"
Simple extension to upload a trackfile on mission change to either Discord or a path (e.g. cloud drive).

## Configuration
To enable track file upload, a change in nodes.yaml is needed:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        Trackfile:
          enabled: true # Optional: you can disable the extension with false
          target: '<id:112233445566778899>'     # channel id or directory
```
The extension will raise a warning, if track files are disabled for this server.
