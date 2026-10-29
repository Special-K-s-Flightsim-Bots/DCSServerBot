# Extension "DKS"
[Digital Kneeboard Simulator](https://www.digitalkneeboardsimulator.com/) (DKS) is a planning and debrief suite for
DCS squadrons. This extension installs the DKS exporter on your instance. The exporter records your missions for DKS
(3D debriefs, LSO grades, live ATC/GCI views) and **replaces Tacview**: it serves Tacview real-time telemetry and can
write Tacview files itself.

> [!IMPORTANT]
> Disable the [Tacview](../tacview/README.md) extension on the instance first. DKS refuses to run next to it.
> If Tacview is installed outside DCSServerBot, DKS turns it off in the instance's options.

## Configuration
Open your server's page in DKS (Settings → Servers), and press **DCSServerBot** next to the exporter. Copy the block
it shows into your nodes.yaml:
```yaml
# config/nodes.yaml
MyNode:
  # ...
  instances:
    DCS.dcs_serverrelease:
      extensions:
        DKS:
          enabled: true
          dksUrl: "https://www.digitalkneeboardsimulator.com"  # optional
          keyId: "dkt_..."
          secret: "..."
          ownerId: "..."
```
Restart the server. On its first start, DCSServerBot downloads the exporter from DKS, and from then on the exporter
updates itself. It only installs versions signed with DKS's release key, so `autoupdate` doesn't apply.

The exporter's other settings (Tacview real-time telemetry port and password, Tacview files, live overlay) are set on
the same page in DKS. If you enable real-time telemetry there, open its port in your firewall.

Keep the secret private. If it leaks, press **New key** in DKS and copy the block again.

## Uninstall
Disable the extension with `/extension disable`. Recordings that were not uploaded yet stay in
`Saved Games\<instance>\DKS\telemetry`.
