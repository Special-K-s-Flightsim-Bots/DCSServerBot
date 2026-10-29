# Extension "DKS"
[Digital Kneeboard Simulator](https://www.digitalkneeboardsimulator.com/) (DKS) is a planning and debrief suite for
DCS squadrons. This extension installs the DKS exporter on your instance. The exporter records your missions for DKS
(3D debriefs, LSO grades, live ATC/GCI views) and **replaces Tacview**: once it is installed, DCSServerBot disables the
Tacview extension for that instance and turns Tacview off in its options. The exporter takes over what Tacview did
there:
- Tacview real-time telemetry, on Tacview's port and with its password (the firewall rule moves to this extension),
- Tacview files (.acmi), in Tacview's export folder.

> [!WARNING]
> What the Tacview extension did on its own is gone for that instance: uploading Tacview files to a channel or folder
> (`target`), `/tacview` commands, and the tournament plugin's Tacview handling. Tacview's files stay installed, so you
> can switch back by disabling DKS and enabling Tacview.

> [!NOTE]
> Earlier versions of this extension installed the DKS weather exporter into Tacview. It is removed (Tacview's own
> tacview.dll is put back), whether or not you install the new exporter. To install it, get an install code (below).

## Configuration
There are three ways. All of them need the server's install code from DKS: open your server's page in DKS
(Settings → Servers), and press **DCSServerBot** next to the exporter.

1. **From DKS, in one click.** If your bot is linked to DKS (plugin [DKS](../../plugins/dks/README.md)), the
   server is linked to your bot's server, and an Admin allowed it with `/dks install_permission`, press **Install**.
   DKS asks your bot to install the exporter.
2. **In Discord.** Run `/extension enable`, pick your server and `DKS`, and paste the install code. If DKS was
   enabled before (the earlier weather exporter), it isn't offered in the list: type `DKS`.
3. **In nodes.yaml:**
   ```yaml
   # config/nodes.yaml
   MyNode:
     # ...
     instances:
       DCS.dcs_serverrelease:
         extensions:
           DKS:
             enabled: true
             key: "dks1...."   # the install code
   ```
   Without `key`, you can also give `keyId`, `secret`, `ownerId` (and `dksUrl`) one by one, e.g. from
   `Config\DKS-Telemetry.cfg` in the exporter ZIP you can download from that page. With `key`, they are ignored.

The exporter starts with the next server start. DCSServerBot downloads it once, from DKS over https only, and from
then on the exporter updates itself. It only installs versions signed with DKS's release key, so `autoupdate` doesn't
apply.

Settings set on the server's page in DKS ("Set in DKS") win over the ones carried over from Tacview.

The install code holds the server's key. Keep it private. After **New key** in DKS, install again (or paste the new
code): DCSServerBot rewrites `Config\DKS-Telemetry.cfg` from nodes.yaml at every start.

## Uninstall
Stop the server, then disable the extension with `/extension disable` (DCS keeps the exporter's files in use while
it runs). Recordings that were not uploaded yet stay in
`Saved Games\<instance>\DKS\telemetry`.
