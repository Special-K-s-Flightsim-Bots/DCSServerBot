# Plugin "Charity"
This plugin is designed to track GoFundMe campaigns and report new donations both to Discord and in-game 
to the configured DCS servers.

## Configuration
As Charity is an optional plugin, you need to activate it in `main.yaml` first like so:
```yaml
# config/main.yaml
opt_plugins:
  - charity
```

The plugin itself is configured with a file named `config/plugins/charity.yaml`. 
You'll find a sample file in the `./samples/plugins` directory:
```yaml
# config/plugins/charity.yaml
DEFAULT:
  bot_status: true                  # Display the donations and the goal in the bot status (default: true)
  gofundme:
    - campaign: campaign-1          # The URL or slug of your GoFundMe campaign
      channel: 123456789012345678   # The Discord channel ID or name to report donations
      interval: 5                   # Optional: check interval in minutes (default is 5)
```

### Multiple Campaigns
You can track multiple campaigns by adding more entries to the `gofundme` list:
```yaml
DEFAULT:
  gofundme:
    - campaign: campaign-1
      channel: donations-channel
    - campaign: campaign-2
      channel: 987654321098765432
```

### Server-Specific Notifications
By default, no in-game notifications are sent. To enable them for specific servers, add the campaign slug to the `campaigns` list under the server's instance name (or in the `DEFAULT` section for all servers):
```yaml
DCS_MERC:
  campaigns:
    - support-matt-wags-wagners-cancer-fight
```

## Commands
| Command         | Parameter  | Description                                                                            |
|-----------------|------------|----------------------------------------------------------------------------------------|
| /charity status | -          | Shows the current status of all configured GoFundMe campaigns (total raised vs. goal). |

## Notifications
When a new donation is detected:
1. An embed is sent to the configured Discord channel with the donor's name, amount, and message (if any).
2. A message and popup are sent in-game to all DCS servers that have the campaign configured in their `campaigns` list.

> [!NOTE]
> To avoid spamming, the plugin only reports new donations that occur after the plugin has been started for the first 
> time for a specific campaign.
