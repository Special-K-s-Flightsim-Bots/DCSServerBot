local base 	    = _G
local utils     = base.require("DCSServerBotUtils")
local userstats = userstats or {}

function userstats.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'UserStats: onMissionLoadEnd()')
    utils.loadScript('DCSServerBot.lua')
    utils.loadScript('userstats/mission.lua')
end

Sim.setUserCallbacks(userstats)
