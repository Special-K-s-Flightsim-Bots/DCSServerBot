-----------------------------------------------------
-- Flight Plan Plugin - Callbacks
-- Hook environment event handlers
-----------------------------------------------------
local base      = _G
local utils     = base.require("DCSServerBotUtils")

log.write('DCSServerBot', log.INFO, 'FlightPlan: callbacks.lua loading...')

local flightplan = flightplan or {}

-- Called on mission load to register our mission.lua
function flightplan.onMissionLoadEnd()
    log.write('DCSServerBot', log.INFO, 'FlightPlan: onMissionLoadEnd() called')
    utils.loadScript('DCSServerBot.lua')
    utils.loadScript('flightplan/mission.lua')
end

-- Register callbacks
Sim.setUserCallbacks(flightplan)
