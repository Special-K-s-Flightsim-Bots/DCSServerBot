# Extension "HoundTTS"
This extension provides integration with the HoundTTS text-to-speech engine for use with your DCS World server.
For more information, refer to the [HoundTTS documentation](https://github.com/uriba107/HoundTTS).

## Configuration
You can enabled HoundTTS in your nodes.yaml like so:
```yaml
MyNode:
  # [...]
  instances:
    DCS.dcs_serverrelease:
      # [...]
      extensions:
        HoundTTS: 
          enabled: true     # enable this extension (default: true)
          autoupdate: true  # automatically update to the latest HoundTTS version (default: true)
```

> [!NOTE]
> If you have the [ModManager](../../services/modmanager/README.md) enabled, 
> the HondTTS extension will download and install HoundTTS automatically on the first run.
> It will also add a line to your MissionScripting.lua, which is necessary to run HoundTTS.
