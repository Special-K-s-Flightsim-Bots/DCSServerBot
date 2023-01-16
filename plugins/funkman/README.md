# Plugin "Funkman"
This plugin adds support for the Moose Funkman protocol. You need to install 
[FunkMan](https://github.com/funkyfranky/FunkMan) for it to work.<br/>
And no, you don't need to run two bots, I just use some modules from FunkMan, just because they are nice :).

FunkMan supports the following Moose modules:
* AIRBOSS
* RANGE (Bombing & Strafing)

For samples on how to configure FunkMan support into Moose, see the respective samples in the samples directory.</br>

**Attention**: You need to set the DCSServerBot port to be used by FunkMan. The default for both, 
DCSServeBot and Funkman is port 10042. 

## Configuration
For a start, you just need to add the path to your FunkMan installation. If you have configured your 
FunkMan.ini already, DCSServerBot will automatically take over your config parameters.

```json
{
  "configs": [
    {
      "install": "../FunkMan"
    }
  ]
}
```
If you have configured your FunkMan.ini already, DCSServerBot will automatically take over your config parameters.        

```json
{
  "configs": [
    {
      "install": "../FunkMan",
      "CHANNELID_MAIN": "1011372894162526329",
      "CHANNELID_RANGE": "1006216842509041786",
      "CHANNELID_AIRBOSS": "1011372920968323155",
      "IMAGEPATH": "../FunkMan/funkpics/"
    }
  ]
}
```
If you want to use different channels for your different servers, you can add a section for each server by
using the "installation" tag:

```json
{
  "configs": [
    {
      "install": "../FunkMan",
      "CHANNELID_MAIN": "1011372894162526329",
      "CHANNELID_RANGE": "1006216842509041786",
      "CHANNELID_AIRBOSS": "1011372920968323155",
      "IMAGEPATH": "../FunkMan/funkpics/"
    },
    {
      "installation": "DCS.openbeta",
      "CHANNELID_MAIN": "12345678901234567890",
      "CHANNELID_RANGE": "12345678901234567890",
      "CHANNELID_AIRBOSS": "12345678901234567890"
    }
  ]
}
```

## Credentials
Thanks to funkyfranky and the Moose team to align the FunkMan protocol with me, which made it very easy
to add support for it and to provide these awesome functionality to you!