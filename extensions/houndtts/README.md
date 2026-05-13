# Extension "HoundTTS"
This extension provides integration with the HoundTTS text-to-speech engine for use with your DCS World server.
For more information, refer to the [HoundTTS documentation](https://github.com/uriba107/HoundTTS).

## Configuration
You can use `/extension enable <HoundTTS>` to enable the extension.
This will add an entry in your `nodes.yaml` like so:
```yaml
MyNode:
  # [...]
  instances:
    DCS.dcs_serverrelease:
      # [...]
      extensions:
        HoundTTS: 
          enabled: true           # enable this extension (default: true)
          autoupdate: true        # automatically update to the latest HoundTTS version (default: true)
          debug: false            # enable debug logging (default: false)
          DEFAULT_PROVIDER: sapi  # See HoundTTS documentation
          DEFAULT_VOICE: ''       # See HoundTTS documentation
          DEFAULT_CULTURE: en-US  # See HoundTTS documentation
          DEFAULT_GENDER: female  # See HoundTTS documentation
          provider:               # See HoundTTS documentation, Piper example for reference
            Piper:
              path: /path/to/piper.exe    # Optional, path to the Piper executable. Default: not set
              voice_path: /path/to/voices # Optional, path to .onnx files. Default: not set
              threads: 4                  # Optional, number of used threads. Default: 4
              max_concurrent: 4           # Optional, maximum number of parallel piper synthesizers. Default: 4
```

> [!NOTE]
> If you have the [ModManager](../../services/modmanager/README.md) enabled, 
> the HondTTS extension will download and install HoundTTS automatically on the first run.
