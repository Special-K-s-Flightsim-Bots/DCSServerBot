-----------------------------------------------------
-- Flight Plan Plugin - Callbacks
-- Hook environment event handlers
-----------------------------------------------------
local base      = _G
local dcsbot    = base.dcsbot
local utils     = base.require("DCSServerBotUtils")

local flightplan = {}

-- Called on mission load to register our mission.lua
function flightplan.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'FlightPlan: onMissionLoadEnd()')
    utils.loadScript('flightplan/mission.lua')
end

-- Called on simulation start - notify bot to handle stale plans and recreate markers
function flightplan.onSimulationStart()
    log.write('DCSServerBot', log.DEBUG, 'FlightPlan: onSimulationStart()')
    local msg = {
        command = 'flightplanSimulationStart'
    }
    utils.sendBotTable(msg)
end

-- Register callbacks
Sim.setUserCallbacks(flightplan)
