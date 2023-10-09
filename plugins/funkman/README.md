# Plugin "FunkMan"
This plugin adds support for the Moose Funkman protocol. You need to install 
[FunkMan](https://github.com/funkyfranky/FunkMan) for it to work.<br/>
And no, you don't need to run two bots, I just use some modules from FunkMan, just because they are nice :).

FunkMan supports the following Moose modules:
* AIRBOSS
* RANGE (Bombing & Strafing)

For samples on how to configure FunkMan support into Moose, see the respective samples in the samples directory.</br>

> ⚠️ **Attention!**<br>
> You need to set the DCSServerBot port to be used by FunkMan. The default for both, 
> DCSServeBot and Funkman is port 10042. 

## Configuration
For a start, you just need to add the path to your FunkMan installation to your funkman.yaml. If you have configured 
your FunkMan.ini already, DCSServerBot will automatically take over your config parameters.

```yaml
DEFAULT:
  install: ../FunkMan
```
If you have configured your FunkMan.ini already, DCSServerBot will automatically take over your config parameters.        

```yaml
DEFAULT:
  install: ../FunkMan
  CHANNELID_MAIN: '1122334455667788'
  CHANNELID_RANGE: '8877665544332211'
  CHANNELID_AIRBOSS: '1188227733664455'
  IMAGEPATH: ../FunkMan/funkpics/
```
If you want to use different channels for your different servers, you can add a section for each server:

```yaml
DCS.openbeta_server:
  install: ../FunkMan
  CHANNELID_MAIN: '1122334455667788'
  CHANNELID_RANGE: '8877665544332211'
  CHANNELID_AIRBOSS: '1188227733664455'
  IMAGEPATH: ../FunkMan/funkpics/
instance2:
  CHANNELID_MAIN: '1234567812345678'
  CHANNELID_RANGE: '8765432187654321'
  CHANNELID_AIRBOSS: '1112223334445555'
  IMAGEPATH: ../FunkMan/funkpics/
```

## Credentials
Thanks to funkyfranky and the Moose team to align the FunkMan protocol with me, which made it very easy
to add support for it and to provide this awesome functionality to you!
