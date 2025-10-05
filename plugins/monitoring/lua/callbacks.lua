local base      = _G
local utils 	= base.require("DCSServerBotUtils")

local monitoring = monitoring or {}

function createSimulateFrame()
    local startTime = os.clock()
    local counter = 0
    return function()
        if counter == 3600 then
            local currentTime = os.clock()
            local elapsedTime = currentTime - startTime
            if elapsedTime > 0 then
                utils.sendBotTable({command = 'perfmon', fps = counter / elapsedTime})
            end
            startTime = currentTime
            counter = 0
        else
            counter = counter + 1
        end
    end
end

monitoring.onSimulationFrame = createSimulateFrame()

Sim.setUserCallbacks(monitoring)
