---
layout: default
title: README
nav_section: plugins/lotatc
---

# Plugin "LotAtc"
[LotAtc](https://www.lotatc.com/) is a famous GCI tool for DCS World.<br>
DCSServerBot supports it as an [extension](../../extensions/lotatc/README.md), to manage and display the 
configuration in your servers and a plugin, that enables the possibility to upload your transponder files through
discord and to inform you about active GCIs on the server. The best result you'll get, if you use LotAtc together with 
the [SRS](../../extensions/srs/README.md) extension.

## Configuration
As LotAtc is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - lotatc
```

In the default case, you do not need any additional configuration. There is an exception though, if you want to block
people from joining your server, if they are GCIs on station. This is to prevent cheating (if you have the password,
you could open LotAtc and fly.) The configuration goes into config\plugins\lotatc.yaml:
```yaml
DEFAULT:
  kick_gci: true  # you must not play if you are a GCI (you will be moved to spectators). Default is false. 
  exemptions:     # as always, allow for exemptions...
    discord:
      - DCS Admin
    ucid:
      - '11223344556677'
```

## Transponder File Upload
You need to create a transponder JSON file according to the LotAtc [documentation](https://www.lotatc.com/documentation/client/transponder.html#add-transponder-table-to-automatically-fill-names-from-code).<br>
To mark the coalition, use "blue" or "red" inside the name of your transponder file, e.g. `bluetransponders.json`, to 
tell DCSServerBot where to upload the file. 

To perform the upload, you just drag and drop the file into the respective servers admin channel (or your central one). 
The file will then be uploaded into `Saved Games\<instance>\Mods\services\LotAtc\userdb\transponders\(blue or red)`,
according to the coalition where this transponder is relevant for.

## Discord Commands
The following Discord commands are available through the LotAtc plugin:

| Command           | Parameter            | Channel | Role      | Description                                                            |
|-------------------|----------------------|---------|-----------|------------------------------------------------------------------------|
| /lotatc update    | server               | all     | DCS Admin | Update LotAtc and install the latest version in the respective server. |
| /lotatc install   | server               | all     | DCS Admin | Install LotAtc into this server (needs to be available on the node).   |
| /lotatc uninstall | server               | all     | DCS Admin | Uninstall LotAtc from this server.                                     |
| /lotatc configure | server               | all     | DCS Admin | Change the LotAtc configuration on this server.                        |
| /gci list         | server blue\|red     | all     | DCS       | List all active GCIs for that coalition.                               |
| /gci info         | server blue\|red gci | all     | DCS       | Shows information about this GCI incl. SRS frequencies, if available.  |

> [!NOTE]
> If [Coalitions](../../COALITIONS.md) are enabled in this server, you can only display information about GCIs of your 
> coalition!


## In-Game Chat Commands
| Command | Parameter | Role | Description                                                           |
|---------|-----------|------|-----------------------------------------------------------------------|
| -gcis   |           | all  | Lists all active GCIs for your coalition. Can only be used in a slot. |
| -gci    | name      | all  | Shows information about this GCI incl. SRS frequencies, if available. |
