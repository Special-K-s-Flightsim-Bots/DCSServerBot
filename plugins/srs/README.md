# Plugin "SRS"
[SimpleRadioStandalone](http://dcssimpleradio.com/) (DCS-SRS) is a radio solution for DCS World.<br>
If you want to use SRS in DCSServerBot, in most cases it is enough to activate the respective [extension](../../extensions/srs/README.md). 
When you are dealing with [LotAtc](../../plugins/lotatc/README.md), or if you want to display the SRS activity status for your players, 
or if you even want to use slot blocking based on SRS – then you want to install this plugin.

## Configuration
As SRS is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - srs
```

If you want to use the slot blocking feature, you need to create a config/plugins/srs.yaml file like so:
```yaml
DEFAULT:
  message_no_srs: You need to use SRS to play on this server!
  enforce_srs: true   # Block slots until SRS is activated. People cannot use slots if not on SRS!
  move_to_spec: true  # Move people back to spectators if they leave SRS (only if enforce is true, default: false)
  enforce_atc: true   # Optional: Enforce ATC frequencies for SRS users (default: false)
  atc_frequencies:    # Optional: default is airbase frequencies, according to the mission
    blue:
      'Airbase': [30000FM, 252000AM]
      'Carrier': 251000AM
      'FARP': 250000AM
      '*': 253000AM
    red:
      'Airbase': 126000AM
      'Carrier': 125000AM
      'FARP': 125500AM
      '*': 124000AM
```

> [!NOTE]
> The enforce_atc option will create a "punish" event "no_atc_usage" on takeoffs without the ATC frequency dialed in,
> which can then be used in the Punishment plugin to punish based on violations:
> ```yaml
> penalties:  
>   - event: no_atc_usage
>     reason: No ATC usage
>     action: move_to_spec
> ```
> There will be **no** automated punishement if not configured.

## TextToSpeech (TTS)
You can use TextToSpeech either from your missions or from Discord.
The bot supports two solutions:

1) SRS-TTS
Using SRS-TTS in a mission can cause some trouble, as you usually need to desanitize os and call an external program.
This is no longer necessary, if you use DCSServerBot. DCSSB decouples the TTS functionality from the mission and 
provides a more reliable and efficient way to handle TextToSpeech.
2) HoundTTS
[HoundTTS](https://github.com/uriba107/HoundTTS) is an addon developed by Uri. 
It supports different TTS providers and hooks nicely into DCS.
To install HoundTTS with DCSServerBot, you can just enable it with `/extension enable <HoundTTS>`.

If DCSServerBot finds HoundTTS installed, it will automatically use it instead of SRS-TTS.
To make sure, your mission always uses the best available TTS implementation, you can use TTS like so:
```lua
if dcsbot then
    -- function dcsbot.send_tts(<message>, <frequency>, <coalition>, [volume], [point])
    dcsbot.send_tts("Hello World", "251.0", 2, 1.0)
end
```

## Discord Commands
The following Discord commands are available through the SRS plugin:

| Command     | Parameter                                    | Channel | Role      | Description                                                                                     |
|-------------|----------------------------------------------|---------|-----------|-------------------------------------------------------------------------------------------------|
| /srs list   |                                              | all     | DCS       | Shows active users on SRS with their connected radios.                                          |
| /srs tts    | server text [player] [coalition] <frequency> | all     | DCS Admin | Send a TTS message to a specific frequency. Chose coalition or player to limit the frequencies. |
| /srs update | server                                       | all     | DCS Admin | Updates SRS on the respective node.                                                             |
| /srs repair | server                                       | all     | DCS Admin | Repairs (re-installs) SRS on the respective node.                                               |

## Mission-Scripting - Text To Speech
If you want to use TTS (text-to-speech) in your mission, you can do it like so:
```lua
if dcsbot then
    local message = "Test"
    local frequency = 243.0
    local coalition = 1 -- red
    local volume = 1.0
    local point = Airbase.getByName("Kutaisi"):getPoint()  -- if LOS is enabled
    dcsbot.send_tts(message, frequency, coalition, volume, point)
end    
```
