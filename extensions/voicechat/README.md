# Extension "DCS Voice Chat"
If you want to use the built-in Voice Chat system of DCS, you can use the VoiceChat extension.

## Configuration
```yaml
MyNode:
  # [...]
  instances:
    DCS.dcs_serverrelease:
      # [...]
      extensions:
        VoiceChat:
          enabled: true
```

> [!TIP]
> You can rename the VoiceChat extension in your server status embed by setting a "name" in the configuration like so:
> ```yaml
> extension:
>   VoiceChat:
>     name: MyFancyName  # Optional: default is "DCS Voice Chat"
> ```
