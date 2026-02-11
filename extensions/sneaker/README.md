# Extension "Sneaker"
Well, this "sneaked" in here somehow. Many people were asking for a moving map, and we looked at several solutions. 
Nearly all took a lot of effort to get them running, if ever. Then we stumbled across 
[Sneaker](https://github.com/b1naryth1ef/sneaker) and in all fairness - that was more or less all that we needed. It 
looks good, it is easy to set up. We tried to contact the developer, but unfortunately they are quite unresponsive. So
we created a [fork](https://github.com/Special-K-s-Flightsim-Bots/sneaker), added all the maps and maybe will remove
the major bugs in the upcoming future.<br/>
Sneaker itself provides a webserver that then connect via the Tacview Realtime protocol to your server. You need to 
have Tacview running on your server though, to use sneaker. As there are still some issues, please don't configure a
realtime password for now.

## Configuration
Adding sneaker is quite straightforward, if you looked at the above examples already:
```yaml
MyNode:
  # [...]
  extensions:
    Sneaker:
      cmd: '%USERPROFILE%\Documents\GitHub\sneaker\sneaker.exe'
      bind: 0.0.0.0:8080            # local listen configuration for Sneaker
      url: https://myfancyhost.com  # optional: show a different host instead of the servers external IP
  # [...]
  instances:
    DCS.dcs_serverrelease:
      # [...]
      extensions:
        Sneaker:
          enabled: true
          debug: true               # Show the Sneaker console output in the DCSSB console. Default = false
          auto_affinity:            # Optional: core affinity settings
            min_cores: 1            # Min number of cores to be used (default: 1)
            max_cores: 1            # Max number of cores to be used (default: 1)
            quality: 1              # Quality (0 = low, 1 = normal, 2 = high, default: 1)
```
You need to let the sneaker cmd point to wherever you've installed the sneaker.exe binary (name might vary, usually 
there is a version number attached to it). DCSServerBot will auto-create the config.json for sneaker 
(config/sneaker.json) and start / stop / monitor the sneaker process.

> [!TIP]
> You can rename the Sneaker extension in your server status embed by setting a "name" in the configuration like so:
> ```yaml
> extension:
>   Sneaker:
>     name: MyFancyName  # Optional: default is "Sneaker"
> ```
