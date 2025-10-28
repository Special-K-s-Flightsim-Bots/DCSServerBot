# Plugin "FunkMan"
This plugin adds support for the Moose Funkman protocol. You need to install [FunkMan](https://github.com/funkyfranky/FunkMan) for it to work.

> [!IMPORTANT]
> You do not need to run the FunkMan bot! 
> I just use some modules from FunkMan, so all you need is to tell the bot where it finds the files.

FunkMan supports the following Moose modules:
* AIRBOSS
* RANGE (Bombing & Strafing)

For samples on how to configure FunkMan support into Moose, see the respective samples in the samples directory.</br>

> [!NOTE]
> You need to set the DCSServerBot port to be used by FunkMan. The default for both, 
> DCSServeBot and Funkman is port **10042**. 

## Configuration
As FunkMan is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - funkman
```

The plugin itself is configured with a yaml file in config/plugins/funkman.yaml. For a start, you just need to add the 
path to your FunkMan installation to your funkman.yaml. If you have configured your FunkMan.ini already, DCSServerBot 
will automatically take over your config parameters.

```yaml
DEFAULT:
  install: ../FunkMan
```
### Channels to publish range- and trap-sheets 
If you have configured your FunkMan.ini already, DCSServerBot will automatically take over your config parameters.        
```yaml
DEFAULT:
  install: ../FunkMan
  CHANNELID_MAIN: 1122334455667788
  CHANNELID_RANGE: 8877665544332211
  CHANNELID_AIRBOSS: 1188227733664455
  IMAGEPATH: ../FunkMan/funkpics/
```

If you want to use different channels for your different servers, you can add a section for each server:
```yaml
DEFAULT:
  install: ../FunkMan
  IMAGEPATH: ../FunkMan/funkpics/
DCS.dcs_serverrelease:
  CHANNELID_MAIN: 1122334455667788
  CHANNELID_RANGE: 8877665544332211
  CHANNELID_AIRBOSS: 1188227733664455
DCS.dcs_serverrelease02:
  CHANNELID_MAIN: 1234567812345678
  CHANNELID_RANGE: 8765432187654321
```

### Rangeboards for Strafing and Bombing
You can display the top X (default: 10) players for your bomb- and strafe-runs in a nice persistent board in some of
your channels. For [greenieboards](../../plugins/greenieboard/README.md) see the respective plugin.
```yaml
DEFAULT:
  install: ../FunkMan
  # [...]
  strafe_board: true
  strafe_channel: 1122334455667788
  bomb_board: true
  bomb_channel: 8877665544332211
```

## Credits
Thanks to funkyfranky and the Moose team to align the FunkMan protocol with me, which made it very easy for me to add
support for it and to provide this awesome functionality to you!
