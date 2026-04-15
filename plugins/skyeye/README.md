# Plugin SkyEye
[SkyEye](https://github.com/dharmab/skyeye) is an AI-powered GCI bot for DCS World, to which you can talk to using SRS.
The solution comes with a server that connects to SRS and gRPC and (optional) Tacview.

This plugin allows you the upload of the "locations.json" files to your individual servers.
They will be automatically used by Skyeye if uploaded.

## Configuration
As SkyEye is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - lotatc
```

> [!NOTE]
> Files to be uploaded have to be named `locations.json` and follow the SkyEye format.
> See [here](https://github.com/dharmab/skyeye/blob/master/docs/locations.md) for more information.

> [!IMPORTANT]
> `locations.json` files require SkyEye v1.9 or higher.
