local base 	        = _G
local utils         = base.require("DCSServerBotUtils")
local creditsystem  = creditsystem or {}

function creditsystem.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'CreditSystem: onMissionLoadEnd()')
    utils.loadScript('DCSServerBot.lua')
    utils.loadScript('creditsystem/mission.lua')
end

Sim.setUserCallbacks(creditsystem)
