local base      = _G
local utils 	= base.require("DCSServerBotUtils")

local serverstats = serverstats or {}

function createSimulateFrame()
    local startTime = os.clock()
    local counter = 0
    return function()
        if counter == 3600 then
            local currentTime = os.clock()
            local elapsedTime = currentTime - startTime
            local fps = counter / elapsedTime
            utils.sendBotTable({command = 'perfmon', fps = fps})
            startTime = currentTime
            counter = 0
        else
            counter = counter + 1
        end
    end
end

serverstats.onSimulationFrame = createSimulateFrame()

DCS.setUserCallbacks(serverstats)
