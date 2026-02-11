-----------------------------------------------------
-- Logistics Plugin - Callbacks
-- Hook environment event handlers
-----------------------------------------------------
local base      = _G
local utils     = base.require("DCSServerBotUtils")

local logistics = {}

-- Called on mission load to register our mission.lua
function logistics.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'Logistics: onMissionLoadEnd()')
    utils.loadScript('logistics/mission.lua')
end

-- Register callbacks
Sim.setUserCallbacks(logistics)
