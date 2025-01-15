# Extension "SkyEye"
[SkyEye](https://github.com/dharmab/skyeye) is an AI powered GCI bot for DCS World, to which you can talk to using SRS.
The solution comes with a server that connects to SRS and (optional) Tacview and gRPC.

## Installation
Just download the latest release version of SkyEye from [here](https://github.com/dharmab/skyeye/releases/latest). You
most likely want the skyeye-windows-amd64.zip.
Unzip the file to a directory of your choice. This will be called the "installation directory" further on.
> [!NOTE]
> Please keep in mind that the bot needs write permissions to this directory to download the whisper model and for
> auto-updating of SkyEye (yet to come).

## Configuration
Then you can configure the SkyEye extension in your nodes.yaml like so:
```yaml
MyNode:
  # [...]
  extensions:
    SkyEye:
      installation: '%USERPROFILE%\Documents\skyeye-windows-amd64'  # or wherever you have installed it
  # [...]
  instances:
    DCS.release_server:
      affinity: 1, 2                  # Recommended, set core affinity for your DCS server process when using SkyEye
      # [...]
      extensions:
        SkyEye:
          debug: true                   # Replicate the SkyEye console log into the DCSSB log
          config: '%USERPROFILE%\Saved Games\DCS.release_server\Config\SkyEye.yaml' # your SkyEye config file.
          affinity: 14,15               # Set the core affinity for SkyEye (recommended!)
          coalition: blue               # Which coalition should SkyEye be active on   
          any-other-skyeye-config: xxx  # See the SkyEye documentation. 
          # No need to provide SRS, Tacview and gRPC configuration, as long as they are configured in nodes.yaml
```
> [!NOTE]
> It is recommended to have your SkyEye configuration in your instance-specific Saved Games\instance\Config directory, 
> as that allows the auto-detection of the process and is a clean way of having all your instance configurations at one 
> place.

> [!NOTE]
> DCSServerBot uses the local Whisper model per default, if not configured otherwise. If you see performance issues,
> try the (paid) API version instead.

> [!IMPORTANT]
> SkyEye can be very heavy on your CPU, if you use local (free) whisper models. The recommended way of running it, is
> to use the external model with an API-key. If you still decide to run SkyEye locally, it is recommended to separate 
> SkyEye from your running DCS servers. You can use the affinity setting in your instance configuration (see above) and 
> in the SkyEye configuration to separate the cores from each other.
