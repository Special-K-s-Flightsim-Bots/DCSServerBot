-----------------------------------------------------
-- Logistics Plugin - Callbacks
-- Hook environment event handlers
-----------------------------------------------------
local base      = _G
local dcsbot    = base.dcsbot
local utils     = base.require("DCSServerBotUtils")

local logistics = {}

-- Called on mission load to register our mission.lua
function logistics.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'Logistics: onMissionLoadEnd()')
    utils.loadScript('logistics/mission.lua')
end

-- Called on simulation start - notify bot to recreate markers for active tasks
function logistics.onSimulationStart()
    log.write('DCSServerBot', log.DEBUG, 'Logistics: onSimulationStart()')
    local msg = {
        command = 'logisticsSimulationStart'
    }
    utils.sendBotTable(msg)
end

-- Register callbacks
Sim.setUserCallbacks(logistics)
