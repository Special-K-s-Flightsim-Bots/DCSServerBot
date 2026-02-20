# Plugin "SRS"
[SimpleRadioStandalone](http://dcssimpleradio.com/) (DCS-SRS) is a radio solution for DCS World.<br>
If you want to use SRS in DCSServerBot, in most cases it is enough to activate the respective 
[extension](../../extensions/srs/README.md). Especially when it comes to [LotAtc](../../plugins/lotatc/README.md),
or, if you want to display an SRS activity status for your players, or if you even want to use slot blocking based
on SRS - then you want to install this plugin.

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
  enforce_atc: true   # Enforce ATC frequencies for SRS users (default: false)
  atc_frequencies:    # Optional: default is airbase frequencies, according to the mission
    blue:
      'CVN*': 252000AM
      '*': 251000AM
    red:
      'CVN*': 125000AM
      '*': 124000AM
```

> [!NOTE]
> The enforce_atc option will create an event "noATCUsage" which can then be used in the 
> Punishment plugin to punish based on violations.

## Discord Commands
The following Discord commands are available through the SRS plugin:

| Command     | Parameter                        | Channel | Role      | Description                                                           |
|-------------|----------------------------------|---------|-----------|-----------------------------------------------------------------------|
| /srs list   |                                  | all     | DCS       | Shows active users on SRS with their connected radios.                |
| /srs tts    | server text [player] [coalition] | all     | DCS Admin | Send a TTS message to a server (optional to a coalition or a player). |
| /srs update | server                           | all     | DCS Admin | Updates SRS on the respective node.                                   |
| /srs repair | server                           | all     | DCS Admin | Repairs (re-installs) SRS on the respective node.                     |

> [!NOTE]
> If a TTS message is sent to a coalition or a server, the message will be sent to the guard channels.
> If a TTS message is sent to a player, it will be sent to the first radio of that player.

## Mission-Scripting - Text To Speech
If you want to use TTS (text-to-speech) in your mission, you can do it like so:
```lua
if dcsbot then
    local message = "Test"
    local frequency = 243.0
    local coalition = 1 -- red
    local volume = 1.0
    local point = Airbase.getByName("Kutaisi"):getPoint()
    dcsbot.send_tts(message, frequency, coalition, volume, point)
end    
```
