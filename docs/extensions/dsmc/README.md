# Extension "DSMC"
If you want to enable persistence for your missions, [DSMC](https://dsmcfordcs.wordpress.com/) is one way to go.
DSMC does not need any change in your missions (but you can, see their documentation!). It will write out a new
miz-file with the state of the mission at the time of saving. This is perfect for instance for campaigns, where you
want to follow up on the next campaign day with the exact state of the mission it had at the end of the current day.</br>

## Configuration
To use DSMC, you need to install it, according to the documentation linked above. In DCSServerBot, you activate the 
extension like with all others:
```yaml
MyNode:
  # [...]
  instances:
    DCS.release_server:
      # [...]
      extensions:
        DSMC:
          enabled: true
```
DCSServerBot will detect if DSMC is enabled and - if yes - change the settings in your DSMC_Dedicated_Server_options.lua
to fit to its needs. DSMC will write out a new miz-file with a new extension (001, 002, ...) after each run. The bot
will take care, that this generated mission will be the next to launch. Other extensions like RealWeather work together
with these generated missions, so you can use a DSMC generated mission but apply a preset or any real time weather to
it.
