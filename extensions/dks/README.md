# Extension "DKS"
[Digital Kneeboard Simulator](https://www.digitalkneeboardsimulator.com/) (DKS) is a planning and debrief suite for
DCS squadrons. This extension installs the DKS exporter on your instance. The exporter records your missions for DKS
(3D debriefs, LSO grades, live ATC/GCI views) and **replaces Tacview**: it serves Tacview real-time telemetry and can
write Tacview files itself.

> [!IMPORTANT]
> Disable the [Tacview](../tacview/README.md) extension on the instance first. DKS refuses to run next to it.
> If Tacview is installed outside DCSServerBot, DKS turns it off in the instance's options.

## Configuration
There are three ways. All of them need the server's install code from DKS: open your server's page in DKS
(Settings → Servers), and press **DCSServerBot** next to the exporter.

1. **From DKS, in one click.** If your bot is linked to DKS (plugin [DKS](../../plugins/dks/README.md)) and the
   server is linked to your bot's server, press **Install**. DKS asks your bot to install the exporter, and the bot
   turns the Tacview extension off for that instance.
2. **In Discord.** Run `/extension enable`, pick your server and `DKS`, and paste the install code.
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
   Instead of `key`, you can also give `keyId`, `secret`, `ownerId` (and `dksUrl`) one by one, e.g. from
   `Config\DKS-Telemetry.cfg` in the exporter ZIP you can download from that page.

The exporter starts with the next server start. DCSServerBot downloads it from DKS once, and from then on the exporter
updates itself. It only installs versions signed with DKS's release key, so `autoupdate` doesn't apply.

The exporter's other settings (Tacview real-time telemetry port and password, Tacview files, live overlay) are set on
the same page in DKS. If you enable real-time telemetry there, open its port in your firewall.

The install code holds the server's key. Keep it private. If it leaks, press **New key** in DKS and install again.

## Uninstall
Disable the extension with `/extension disable`. Recordings that were not uploaded yet stay in
`Saved Games\<instance>\DKS\telemetry`.
