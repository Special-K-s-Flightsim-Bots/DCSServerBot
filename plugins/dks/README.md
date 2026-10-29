# Plugin DKS (Digital Kneeboard Simulator)
This plugin will help you connect your DCSServerBot to the [Digital Kneeboard Simulator](https://www.digitalkneeboardsimulator.com/).
DKS can then use the [RestAPI](/plugins/restapi/README.md) to add valuable information to your DKS website.

> [!IMPORTANT]
> When linking your DCSServerBot to DKS you allow DKS to call all RestAPI endpoints on your bot.
> I have worked with Jason, the developer of DKS, to do our best to secure this connection as good as possible.
> You can disable any endpoint or secure it, so that no external connection is possible by configuring the RestAPI
> plugin to your needs. DKS will obey that you might not want to expose specific information or allow specific calls.

## Configuration
As DKS is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
# config/main.yaml
opt_plugins:
  - dks
```

The plugin itself usually does not need any configuration. 
In some rare cases, when you have your RestAPI behind a proxy server that hides the port or if you use a DNS name,
you can add it in here.
```yaml
# config/plugins/dks.yaml
DEFAULT:
  callback_url: 'https://myfancydns/register_dks'
```
The register_dks endpoint has to be reachable at this URL.

> [!WARNING]
> If you have configured a prefix in your RestAPI plugin configuration, this prefix will also be used here!

> [!IMPORTANT]
> You need to allow remote access to the bot's WebService.
> This means that you expose API endpoints to the outside world. 
> The RestAPI has a security configuration, which should prevent people from calling your endpoints that do not have
> the necessary permissions. Please check your RestAPI configuration, if you have set up the security scope for
> remote networks to "jwt".

## Discord Commands

| Command       | Parameter | Channel | Role    | Description                                                             |
|---------------|-----------|---------|---------|-------------------------------------------------------------------------|
| /dks register |           | all     | Admin   | Creates a registration URL including an OTP and provides it in Discord. |

> [!NOTE]
> The OTP will expire after 5 minutes.
> This means you have 5 minutes to register your bot on the DKS website.
> You can re-run the registration as often as you want, if something goes wrong.

## Installing the DKS exporter from DKS
Once your bot is linked, DKS can install its exporter on one of your servers (extension
[DKS](../../extensions/dks/README.md)): press **Install** on the server's page in DKS. DKS calls
`install_dks_exporter`, next to `register_dks`, signed like every DKS call. Its parameters are claims of the
signed token (the server, a single-use code and where to redeem it), so they can't be changed on the way. The bot redeems that code at DKS over HTTPS for the server's install code, so the exporter key never travels
through your bot's API. The bot then enables the DKS extension for that server, turns the Tacview extension off, and
the exporter starts with the next server start.
