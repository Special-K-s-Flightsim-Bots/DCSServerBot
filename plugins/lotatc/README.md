# Plugin "LotAtc"
[LotAtc](https://www.lotatc.com/) is a famous GCI tool for DCS World.<br>
DCSServerBot supports it as an [extension](../../extensions/README.md#lotatc), to manage and display the 
configuration in your servers and a plugin, that enables the possibility to upload your transponder files through
discord and to inform you about active GCIs on the server. The best result you'll get, if you use LotAtc together with 
the [SRS](../../extensions/README.md/#srs) extension.

## Configuration
As LotAtc is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - lotatc
```

## Transponder File Upload
You need to create a transponder JSON file according to the LotAtc [documentation](https://www.lotatc.com/documentation/client/transponder.html#add-transponder-table-to-automatically-fill-names-from-code).<br>
To mark the coalition, use "blue" or "red" inside the name of your transponder file, e. g. `bluetransponders.json`, to 
tell DCSServerBot where to upload the file. 

To perform the upload, you just drag and drop the file into the respective servers admin channel (or your central one). 
The file will then be uploaded into `Saved Games\<instance>\Mods\services\LotAtc\userdb\transponders\(blue or red)`,
according to the coalition where this transponder is relevant for.

## Discord Commands
The following Discord commands are available through the LotAtc plugin:

| Command   | Parameter            | Channel | Role | Description                                                            |
|-----------|----------------------|---------|------|------------------------------------------------------------------------|
| /gci list | server blue\|red     | all     | DCS  | List all active GCIs for that coalition.                               |
| /gci info | server blue\|red gci | all     | DCS  | Shows information about this GCI incl. SRS frequencies, if available.  |

> ⚠️ **Attention!**<br> 
> If [Coalitions](../../COALITIONS.md) are enabled in this server, you can only display information about GCIs of your 
> coalition!


## In-Game Chat Commands
| Command | Parameter | Role | Description                                                           |
|---------|-----------|------|-----------------------------------------------------------------------|
| .gcis   |           | all  | Lists all active GCIs for your coalition. Can only be used in a slot. |
| .gci    | name      | all  | Shows information about this GCI incl. SRS frequencies, if available. |
