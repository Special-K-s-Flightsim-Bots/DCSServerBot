# Plugin "LotAtc"
[LotAtc](https://www.lotatc.com/) is a famous GCI tool for DCS World.<br>
DCSServerBot supports it as an [extension](../../extensions/README.md#lotatc), to manage and display the 
configuration in your servers and a plugin, that enables the possibility to upload your transponder files through
discord.

## Configuration
As LotAtc is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - lotatc
```

## Usage
You need to create a transponder JSON file according to the LotAtc [documentation](https://www.lotatc.com/documentation/client/transponder.html#add-transponder-table-to-automatically-fill-names-from-code).<br>
To mark the coalition, use "blue" or "red" inside the name of your transponder file, e. g. `bluetransponders.json`, to 
tell DCSServerBot where to upload the file. 

To perform the upload, you just drag and drop the file into the respective servers admin channel (or your central one). 
The file will then be uploaded into `Saved Games\<instance>\Mods\services\LotAtc\userdb\transponders\(blue or red)`,
according to the coalition where this transponder is relevant for.
