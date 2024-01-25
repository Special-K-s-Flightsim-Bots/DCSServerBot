local base 	        = _G
local utils         = base.require("DCSServerBotUtils")
local missionstats  = missionstats or {}

function missionstats.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'Missionstats: onMissionLoadEnd()')
    utils.loadScript('DCSServerBot.lua')
    utils.loadScript('missionstats/mission.lua')
end

DCS.setUserCallbacks(missionstats)
