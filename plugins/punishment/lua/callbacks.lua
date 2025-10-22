local base 	        = _G
local utils         = base.require("DCSServerBotUtils")
local punishment    = punishment or {}

function punishment.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'Punishment: onMissionLoadEnd()')
    utils.loadScript('DCSServerBot.lua')
    utils.loadScript('punishment/mission.lua')
end

Sim.setUserCallbacks(punishment)
