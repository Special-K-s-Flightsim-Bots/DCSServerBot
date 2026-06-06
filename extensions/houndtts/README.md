# Extension "HoundTTS"
This extension provides integration with the HoundTTS text-to-speech engine for use with your DCS World server.
For more information, refer to the [HoundTTS documentation](https://github.com/uriba107/HoundTTS).

## Installation
HoundTTS can be installed as a single mod package or in combination with different TTS providers.
The easiest way is to use `/extension enable <HoundTTS>` to install it.
If you have the [ModManager](../../services/modmanager/README.md) enabled, 
the HondTTS extension will then download and install HoundTTS automatically.

If you want to install additional TTS providers, you can use the [ModManager](../../services/modmanager/README.md) 
to install them:

### Piper
Run `/mods download repo:https://github.com/uriba107/HoundTTS` in your admin channel in Discord.
Select HondTTS-piper-engine-windows.zip and after that HoundTTS-piper-voices-windows.zip.
Use `/mods install` to install both mods into the respective servers.

### Supertonic
Run `/mods download repo:https://github.com/uriba107/HoundTTS` in your admin channel in Discord.
Select HondTTS-supertonic-engine-windows.zip.
Use `/mods install` to install the mod into the respective servers.


## Configuration
You can use `/extension enable <HoundTTS>` to enable the extension.
This will add an entry in your `nodes.yaml` like so:
```yaml
# config/nodes.yaml
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
