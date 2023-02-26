# Plugin WeaponBlocking
A DCSServerBot module to kick players upon firing a prohibited weapon.
Concept and initial version created by [NanoTrasen](https://github.com/NanoTrasen-Inc).

## Configuration
The plugin is configured with a file named config\weaponblocking.json. You'll find a sample file in that directory:
```json
{
  "configs": [
    {
      "installation": "DCS.server1", -- which server instance to apply to
      "restricted_units": [ -- list of units to apply restrictions to
        {
          "unit_type": "FA-18C_hornet", -- unit type, can be found in logs/.modulestats
          "mode": "whitelist", -- type of restriction, whitelist allows only specific weapons (or none if blank)
          "weapons": ["M_61", "AIM_9X", "AIM-7P", "AIM_120C"] -- weapon name, allows only guns and air to air missiles, kick upon dropping a bomb, etc
        },
        {
          "unit_type": "SA342L", -- unit type, can be found in logs/.modulestats
          "mode": "blacklist", -- type of restriction, blacklist prohibits the weapons specified below
          "weapons": ["GIAT_M621G"] -- weapon name, can be found in logs/.modulestats
        },
        {
          "unit_type": "UH-60L", -- kick blackhawk pilot if they fire any weapon at all
          "mode": "whitelist",
          "weapons": [] -- if empty, block all weapons by default
        }
      ]
    }
  ]
}
```
### How it works
Upon receiving a S_EVENT_SHOT or S_EVENT_SHOOTING_START event, if the unit is a player it is compared to the restrictions in the config and if a match is found, that player is kicked.

**ATTENTION:** The weapons the player has fired will still remain! This does not delete the fired weapons!

### Exemptions
Exemptions have not been implemted yet.
