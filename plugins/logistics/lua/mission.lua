-----------------------------------------------------
-- Logistics Plugin - Mission Environment
-- Map markers and position checking functions
-- Runs inside mission scripting sandbox
-----------------------------------------------------
local base      = _G
dcsbot          = base.dcsbot

-- Storage for marker IDs by task
dcsbot.logisticsMarkers = dcsbot.logisticsMarkers or {}

-- Marker ID counter (unique per mission)
dcsbot.logisticsMarkerCounter = dcsbot.logisticsMarkerCounter or 10000

local function getNextMarkerId()
    dcsbot.logisticsMarkerCounter = dcsbot.logisticsMarkerCounter + 1
    return dcsbot.logisticsMarkerCounter
end

-- Get coalition constant from number
local function getCoalition(num)
    if num == 1 then
        return coalition.side.RED
    elseif num == 2 then
        return coalition.side.BLUE
    else
        return coalition.side.ALL
    end
end

-- Calculate distance between two points (2D)
local function distance2D(pos1, pos2)
    local dx = pos1.x - pos2.x
    local dz = pos1.z - pos2.z
    return math.sqrt(dx*dx + dz*dz)
end

-- Create markers for a logistics task
-- Parameters:
--   task_id: unique task identifier
--   coalitionNum: 1=RED, 2=BLUE
--   source_name: pickup location name
--   source_pos: {x, y, z} position
--   dest_name: delivery location name
--   dest_pos: {x, y, z} position
--   cargo_type: cargo description
--   pilot_name: assigned pilot (empty if unassigned)
--   deadline: deadline string (empty if none)
--   waypoints_json: JSON array of waypoints
--   channel: response channel
--   timeout: seconds until markers auto-remove (0 = permanent)
function dcsbot.createLogisticsMarkers(task_id, coalitionNum, source_name, source_pos, dest_name, dest_pos, cargo_type, pilot_name, deadline, waypoints_json, channel, timeout)
    env.info('DCSServerBot - Logistics: createLogisticsMarkers(' .. task_id .. ')')

    -- Validate position data to prevent crashes from nil coordinates
    if not source_pos or source_pos.x == nil or source_pos.z == nil then
        env.error('DCSServerBot - Logistics: Invalid source_pos for task ' .. task_id)
        local msg = {
            command = "createLogisticsMarkers",
            task_id = task_id,
            success = false,
            error = "Invalid source position"
        }
        dcsbot.sendBotTable(msg, channel)
        return
    end
    if not dest_pos or dest_pos.x == nil or dest_pos.z == nil then
        env.error('DCSServerBot - Logistics: Invalid dest_pos for task ' .. task_id)
        local msg = {
            command = "createLogisticsMarkers",
            task_id = task_id,
            success = false,
            error = "Invalid destination position"
        }
        dcsbot.sendBotTable(msg, channel)
        return
    end

    -- Remove any existing markers for this task first
    dcsbot.removeLogisticsMarkersInternal(task_id)

    local coal = getCoalition(coalitionNum)
    local markers = {}

    -- Source marker (pickup point)
    local sourceMarkerId = getNextMarkerId()
    local sourceText = "[PICKUP #" .. task_id .. "] " .. source_name
    trigger.action.markToCoalition(sourceMarkerId, sourceText, source_pos, coal, false)
    table.insert(markers, {id = sourceMarkerId, type = "source_marker"})

    -- Destination marker with cargo, pilot, and deadline info
    local destMarkerId = getNextMarkerId()
    local destText = "[DELIVERY #" .. task_id .. "] " .. dest_name .. "\n"
    destText = destText .. "Cargo: " .. cargo_type
    if pilot_name and pilot_name ~= "" then
        destText = destText .. "\nPilot: " .. pilot_name
    else
        destText = destText .. "\nPilot: UNASSIGNED"
    end
    if deadline and deadline ~= "" then
        destText = destText .. "\nDeadline: " .. deadline
    end
    trigger.action.markToCoalition(destMarkerId, destText, dest_pos, coal, false)
    table.insert(markers, {id = destMarkerId, type = "dest_marker"})

    -- Parse and create waypoint markers
    local waypoints = {}
    if waypoints_json and waypoints_json ~= "" and waypoints_json ~= "[]" then
        waypoints = net.json2lua(waypoints_json) or {}
    end

    for i, wp in ipairs(waypoints) do
        local wpMarkerId = getNextMarkerId()
        local wpPos = {x = wp.x, y = 0, z = wp.z}
        local wpText = "[VIA " .. i .. "] " .. (wp.name or "Waypoint " .. i)
        trigger.action.markToCoalition(wpMarkerId, wpText, wpPos, coal, false)
        table.insert(markers, {id = wpMarkerId, type = "waypoint_marker"})
    end

    -- Create route lines
    -- Source to first waypoint (or destination if no waypoints)
    local routeLineId = getNextMarkerId()
    local firstPoint = #waypoints > 0 and {x = waypoints[1].x, y = 0, z = waypoints[1].z} or dest_pos
    trigger.action.lineToAll(coal, routeLineId, source_pos, firstPoint, {1, 1, 0, 0.8}, 2)
    table.insert(markers, {id = routeLineId, type = "route_line"})

    -- Add text box at midpoint of first line segment with task info
    local midPoint = {
        x = (source_pos.x + firstPoint.x) / 2,
        y = 0,
        z = (source_pos.z + firstPoint.z) / 2
    }
    local infoText = "TASK #" .. task_id .. "\n"
    infoText = infoText .. "From: " .. source_name .. "\n"
    infoText = infoText .. "To: " .. dest_name .. "\n"
    infoText = infoText .. "Cargo: " .. cargo_type
    if pilot_name and pilot_name ~= "" then
        infoText = infoText .. "\nPilot: " .. pilot_name
    else
        infoText = infoText .. "\nPilot: UNASSIGNED"
    end
    if deadline and deadline ~= "" then
        infoText = infoText .. "\nDeadline: " .. deadline
    end
    local textMarkerId = getNextMarkerId()
    trigger.action.textToAll(coal, textMarkerId, midPoint, {1, 1, 0, 1}, {0, 0, 0, 0.5}, 12, false, infoText)
    table.insert(markers, {id = textMarkerId, type = "info_text"})

    -- Between waypoints
    for i = 1, #waypoints - 1 do
        local lineId = getNextMarkerId()
        local fromPos = {x = waypoints[i].x, y = 0, z = waypoints[i].z}
        local toPos = {x = waypoints[i+1].x, y = 0, z = waypoints[i+1].z}
        trigger.action.lineToAll(coal, lineId, fromPos, toPos, {1, 1, 0, 0.8}, 2)
        table.insert(markers, {id = lineId, type = "route_line"})
    end

    -- Last waypoint to destination
    if #waypoints > 0 then
        local lastLineId = getNextMarkerId()
        local lastWp = waypoints[#waypoints]
        local lastPos = {x = lastWp.x, y = 0, z = lastWp.z}
        trigger.action.lineToAll(coal, lastLineId, lastPos, dest_pos, {1, 1, 0, 0.8}, 2)
        table.insert(markers, {id = lastLineId, type = "route_line"})
    end

    -- Store marker IDs for later removal
    dcsbot.logisticsMarkers[task_id] = {
        markers = markers,
        coalition = coalitionNum,
        dest_pos = dest_pos,
        dest_text_base = "[DELIVERY] " .. dest_name .. "\nCargo: " .. cargo_type,
        deadline = deadline
    }

    -- Schedule auto-removal if timeout is set
    if timeout and timeout > 0 then
        local tid = task_id  -- capture for closure
        timer.scheduleFunction(function(args, time)
            dcsbot.removeLogisticsMarkersInternal(tid)
            env.info('DCSServerBot - Logistics: Auto-removed markers for task ' .. tid .. ' after timeout')
            return nil
        end, {}, timer.getTime() + timeout)
    end

    -- Send confirmation back to bot (only if channel is valid)
    if channel and channel ~= "-1" then
        local msg = {
            command = "createLogisticsMarkers",
            task_id = task_id,
            marker_count = #markers,
            marker_ids = {}
        }
        for _, m in ipairs(markers) do
            table.insert(msg.marker_ids, {id = m.id, type = m.type})
        end
        dcsbot.sendBotTable(msg, channel)
    end
end

-- Internal function to remove markers without sending response
function dcsbot.removeLogisticsMarkersInternal(task_id)
    local taskMarkers = dcsbot.logisticsMarkers[task_id]
    if taskMarkers and taskMarkers.markers then
        for _, marker in ipairs(taskMarkers.markers) do
            if marker.type == "route_line" then
                trigger.action.removeMark(marker.id)
            else
                trigger.action.removeMark(marker.id)
            end
        end
        dcsbot.logisticsMarkers[task_id] = nil
    end
end

-- Remove markers for a logistics task
function dcsbot.removeLogisticsMarkers(task_id, channel)
    env.info('DCSServerBot - Logistics: removeLogisticsMarkers(' .. task_id .. ')')

    local count = 0
    local taskMarkers = dcsbot.logisticsMarkers[task_id]
    if taskMarkers and taskMarkers.markers then
        count = #taskMarkers.markers
    end

    dcsbot.removeLogisticsMarkersInternal(task_id)

    -- Send confirmation back to bot (only if channel is valid)
    if channel and channel ~= "-1" then
        local msg = {
            command = "removeLogisticsMarkers",
            task_id = task_id,
            removed_count = count
        }
        dcsbot.sendBotTable(msg, channel)
    end
end

-- Update marker with pilot name when task is assigned
function dcsbot.updateLogisticsMarkerPilot(task_id, pilot_name, channel)
    env.info('DCSServerBot - Logistics: updateLogisticsMarkerPilot(' .. task_id .. ', ' .. pilot_name .. ')')

    local taskMarkers = dcsbot.logisticsMarkers[task_id]
    if not taskMarkers then
        local msg = {
            command = "updateLogisticsMarkerPilot",
            task_id = task_id,
            success = false,
            error = "Task markers not found"
        }
        dcsbot.sendBotTable(msg, channel)
        return
    end

    -- Find and update the destination marker
    local coal = getCoalition(taskMarkers.coalition)
    for _, marker in ipairs(taskMarkers.markers) do
        if marker.type == "dest_marker" then
            -- Remove old marker
            trigger.action.removeMark(marker.id)

            -- Create new marker with updated text
            local newText = taskMarkers.dest_text_base
            newText = newText .. "\nPilot: " .. pilot_name
            if taskMarkers.deadline and taskMarkers.deadline ~= "" then
                newText = newText .. "\nDeadline: " .. taskMarkers.deadline
            end

            trigger.action.markToCoalition(marker.id, newText, taskMarkers.dest_pos, coal, true)
            break
        end
    end

    local msg = {
        command = "updateLogisticsMarkerPilot",
        task_id = task_id,
        success = true,
        pilot_name = pilot_name
    }
    dcsbot.sendBotTable(msg, channel)
end

-- Get player's current unit position
function dcsbot.getPlayerPosition(unit_name, channel)
    env.info('DCSServerBot - Logistics: getPlayerPosition(' .. unit_name .. ')')

    local unit = Unit.getByName(unit_name)
    local msg = {
        command = "getPlayerPosition",
        unit_name = unit_name
    }

    if unit and unit:isExist() then
        local pos = unit:getPoint()
        msg.position = {
            x = pos.x,
            y = pos.y,
            z = pos.z
        }
        msg.found = true
    else
        msg.found = false
        msg.error = "Unit not found or does not exist"
    end

    dcsbot.sendBotTable(msg, channel)
end

-- Check if player is within delivery proximity of destination
function dcsbot.checkDeliveryProximity(unit_name, task_id, dest_pos, threshold, channel)
    env.info('DCSServerBot - Logistics: checkDeliveryProximity(' .. unit_name .. ', ' .. task_id .. ')')

    local unit = Unit.getByName(unit_name)
    local msg = {
        command = "checkDeliveryProximity",
        task_id = task_id,
        unit_name = unit_name
    }

    if unit and unit:isExist() then
        local pos = unit:getPoint()
        local dist = distance2D(pos, dest_pos)
        msg.distance = dist
        msg.within_threshold = dist <= threshold
        msg.threshold = threshold
        msg.found = true
    else
        msg.found = false
        msg.within_threshold = false
        msg.error = "Unit not found or does not exist"
    end

    dcsbot.sendBotTable(msg, channel)
end

-- Send popup message to coalition
function dcsbot.logisticsPopup(coalitionNum, message, time)
    env.info('DCSServerBot - Logistics: logisticsPopup()')
    local coal = getCoalition(coalitionNum)
    trigger.action.outTextForCoalition(coal, message, time or 10)
end

env.info("DCSServerBot - Logistics: mission.lua loaded.")
