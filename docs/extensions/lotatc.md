---
parent: Extensions
nav_order: 0
---

# LotAtc

Another famous extension for DCS is [LotAtc](https://www.lotatc.com/) by D'Art. If you think about any kind of proper
GCI or ATC work, there is no way around it. It perfectly integrates with DCS and DCS-SRS.<br/>
DCSServerBot can detect if it is there and enabled, but that's about it. You'll get a notification in your servers
status embed about ports and - if you like - passwords and the version of LotAtc printed in the footer.

```json
{
  "configs": [
    {
      "installation": "DCS.release_server",
      [...]
      "extensions": {
        "LotAtc": {
          "show_passwords": false,
          "host": "myfancyhost.com"  -- Optional, default is your external IP
        }
      }
    }
  ]
}
```

There is no default section for LotAtc, so if added to a server like described above, it is enabled, if not, then not.
