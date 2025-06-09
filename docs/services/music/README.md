# Music Service
The music service allows you to play music (mp3 files as of now) via defined [DCS-SRS](../../extensions/README.md) 
channels.<br>
It comes alongside the [Music Plugin](../../plugins/music/README.md), which allows you to upload music, create playlists
and configure which playlist should be played on which server and which frequency.

## Configuration
To activate the Music Service, you need to add an optional plugin to your main.yaml first like so:
```yaml
opt_plugins:
  - music
```

As per usual, the Music service itself is configured with a yaml file in config/services/music.yaml.

```yaml
DEFAULT:
  music_dir: G:\My Drive\Music    # Directory where your music is (or where it is uploaded). For clusters, it should be a cloud drive like here.
  popup: Now playing {song} on {frequency}{modulation}  # optional - popup message when a song starts to play
  chat: Now playing {song} on {frequency}{modulation}   # optional - chat message when a song starts to play
  pause_without_players: false    # Pause music, when no player is active (default: true)
  radios:
    Radio 1:                      # Name of the radio, can be anything
      type: SRSRadio              # we currently only support SRS, so this has to be in
      mode: 1                     # mode 1 = repeat (at the end of the list), mode 2 = shuffle
      frequency: '30.0'           # channel where the music should be played
      modulation: FM              # modulation, AM or FM
      coalition: 1                # coalition 1 = red, 2 = blue, 3 = neutral
      volume: '0.5'               # 50% volume
      display_name: Jungle Beats  # Name of the radio as displayed in SRS
    Radio 2:                      
      type: SRSRadio              
      mode: 1                     
      frequency: '32.0'           
      modulation: FM              
      coalition: 2                
      volume: '0.5'               
      display_name: Rock & Pop     
```
You define unlimited radios per server, too.

> [!NOTE]
> Please keep in mind that every radio runs a dedicated process on your server. If you run a lot of radios, that can<br>
> result in additional load, especially when a new song is decoded before playing.
