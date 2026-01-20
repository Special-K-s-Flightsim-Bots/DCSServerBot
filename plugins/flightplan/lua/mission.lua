-----------------------------------------------------
-- Flight Plan Plugin - Mission Environment
-- F10 map markers for flight plans
-- Runs inside mission scripting sandbox
-----------------------------------------------------
local base      = _G
dcsbot          = base.dcsbot

-- Storage for marker IDs by flight plan
dcsbot.flightPlanMarkers = dcsbot.flightPlanMarkers or {}

-- Marker ID counter (unique per mission)
dcsbot.flightPlanMarkerCounter = dcsbot.flightPlanMarkerCounter or 20000

local function getNextMarkerId()
    dcsbot.flightPlanMarkerCounter = dcsbot.flightPlanMarkerCounter + 1
    return dcsbot.flightPlanMarkerCounter
end

-- Get coalition constant from number
-- For markToCoalition: -1 = all, 1 = red, 2 = blue
local function getCoalition(num)
    if num == 1 then
        return coalition.side.RED  -- 1
    elseif num == 2 then
        return coalition.side.BLUE  -- 2
    else
        return -1  -- All coalitions (markToCoalition uses -1, not coalition.side.ALL)
    end
end

-- Flight plan marker color: Cyan (distinct from logistics yellow)
local FLIGHT_PLAN_COLOR = {0, 1, 1, 0.8}  -- R, G, B, A
local FLIGHT_PLAN_TEXT_COLOR = {0, 1, 1, 1}
local FLIGHT_PLAN_TEXT_BG = {0, 0, 0, 0.5}

-- Create markers for a flight plan
-- Parameters:
--   plan_id: unique flight plan identifier
--   coalitionNum: 0=ALL, 1=RED, 2=BLUE
--   callsign: pilot callsign
--   departure_name: departure location name
--   departure_pos: {x, y, z} position
--   destination_name: destination location name
--   destination_pos: {x, y, z} position
--   alternate_name: alternate location name (optional)
--   alternate_pos: {x, y, z} position (optional)
--   aircraft_type: aircraft type string
--   cruise_altitude: cruise altitude in feet
--   etd: estimated departure time string
--   waypoints_json: JSON array of waypoints
--   channel: response channel
--   timeout: seconds until markers auto-remove (0 = permanent)
function dcsbot.createFlightPlanMarkers(plan_id, coalitionNum, callsign, departure_name, departure_pos, destination_name, destination_pos, alternate_name, alternate_pos, aircraft_type, cruise_altitude, etd, waypoints_json, channel, timeout)
    env.info('DCSServerBot - FlightPlan: createFlightPlanMarkers(' .. plan_id .. ')')

    -- Remove any existing markers for this plan first
    dcsbot.removeFlightPlanMarkersInternal(plan_id)

    local coal = getCoalition(coalitionNum)
    local markers = {}

    -- Format flight level
    local fl_str = ""
    if cruise_altitude and cruise_altitude > 0 then
        fl_str = " @ FL" .. string.format("%03d", math.floor(cruise_altitude / 100))
    end

    -- Departure marker
    local depMarkerId = getNextMarkerId()
    local depText = "[DEPARTURE] " .. departure_name .. "\n"
    depText = depText .. "Callsign: " .. callsign
    if aircraft_type and aircraft_type ~= "" then
        depText = depText .. " (" .. aircraft_type .. ")"
    end
    if etd and etd ~= "" then
        depText = depText .. "\nETD: " .. etd
    end
    trigger.action.markToCoalition(depMarkerId, depText, departure_pos, coal, false)
    table.insert(markers, {id = depMarkerId, type = "departure_marker"})

    -- Destination marker
    local destMarkerId = getNextMarkerId()
    local destText = "[DESTINATION] " .. destination_name .. "\n"
    destText = destText .. "Pilot: " .. callsign .. fl_str
    trigger.action.markToCoalition(destMarkerId, destText, destination_pos, coal, false)
    table.insert(markers, {id = destMarkerId, type = "destination_marker"})

    -- Alternate marker (if provided)
    if alternate_name and alternate_name ~= "" and alternate_pos then
        local altMarkerId = getNextMarkerId()
        local altText = "[ALTERNATE] " .. alternate_name
        trigger.action.markToCoalition(altMarkerId, altText, alternate_pos, coal, false)
        table.insert(markers, {id = altMarkerId, type = "alternate_marker"})
    end

    -- Parse waypoints
    local waypoints = {}
    if waypoints_json and waypoints_json ~= "" and waypoints_json ~= "[]" then
        waypoints = net.json2lua(waypoints_json) or {}
    end

    -- Convert waypoints to DCS coordinates if needed
    local converted_waypoints = {}
    for i, wp in ipairs(waypoints) do
        local x = wp.x
        local z = wp.z

        -- Convert from lat/lon if no DCS coordinates
        if (not x or not z) and wp.lat and wp.lon then
            local converted = coord.LLtoLO(wp.lat, wp.lon, 0)
            if converted then
                x = converted.x
                z = converted.z
            end
        end

        if x and z then
            table.insert(converted_waypoints, {
                name = wp.name,
                x = x,
                z = z,
                altitude = wp.altitude
            })
        end
    end

    -- Create waypoint markers
    for i, wp in ipairs(converted_waypoints) do
        local wpMarkerId = getNextMarkerId()
        local wpPos = {x = wp.x, y = 0, z = wp.z}
        local wpAlt = ""
        if wp.altitude and wp.altitude > 0 then
            wpAlt = " @ " .. wp.altitude .. "ft"
        end
        local wpText = "[WP" .. i .. "] " .. (wp.name or "Waypoint " .. i) .. wpAlt
        trigger.action.markToCoalition(wpMarkerId, wpText, wpPos, coal, false)
        table.insert(markers, {id = wpMarkerId, type = "waypoint_marker"})
    end

    -- Build route points for lines
    local routePoints = {}
    table.insert(routePoints, departure_pos)
    for _, wp in ipairs(converted_waypoints) do
        table.insert(routePoints, {x = wp.x, y = 0, z = wp.z})
    end
    table.insert(routePoints, destination_pos)

    -- Create route lines (cyan color)
    for i = 1, #routePoints - 1 do
        local lineId = getNextMarkerId()
        trigger.action.lineToAll(coal, lineId, routePoints[i], routePoints[i + 1], FLIGHT_PLAN_COLOR, 2)
        table.insert(markers, {id = lineId, type = "route_line"})
    end

    -- Add info text box at midpoint of route
    if #routePoints >= 2 then
        local midIdx = math.ceil(#routePoints / 2)
        local midPoint = {
            x = (routePoints[midIdx].x + routePoints[midIdx + 1].x) / 2,
            y = 0,
            z = (routePoints[midIdx].z + routePoints[midIdx + 1].z) / 2
        }

        local infoText = "FLIGHT PLAN #" .. plan_id .. "\n"
        infoText = infoText .. "Callsign: " .. callsign .. "\n"
        if aircraft_type and aircraft_type ~= "" then
            infoText = infoText .. "Aircraft: " .. aircraft_type .. "\n"
        end
        infoText = infoText .. "From: " .. departure_name .. "\n"
        infoText = infoText .. "To: " .. destination_name
        if cruise_altitude and cruise_altitude > 0 then
            infoText = infoText .. "\nCruise: FL" .. string.format("%03d", math.floor(cruise_altitude / 100))
        end
        if etd and etd ~= "" then
            infoText = infoText .. "\nETD: " .. etd
        end

        local textMarkerId = getNextMarkerId()
        trigger.action.textToAll(coal, textMarkerId, midPoint, FLIGHT_PLAN_TEXT_COLOR, FLIGHT_PLAN_TEXT_BG, 12, false, infoText)
        table.insert(markers, {id = textMarkerId, type = "info_text"})
    end

    -- Store marker IDs for later removal
    dcsbot.flightPlanMarkers[plan_id] = {
        markers = markers,
        coalition = coalitionNum
    }

    -- Schedule auto-removal if timeout is set
    if timeout and timeout > 0 then
        local pid = plan_id  -- capture for closure
        timer.scheduleFunction(function(args, time)
            dcsbot.removeFlightPlanMarkersInternal(pid)
            env.info('DCSServerBot - FlightPlan: Auto-removed markers for plan ' .. pid .. ' after timeout')
            return nil
        end, {}, timer.getTime() + timeout)
    end

    -- Send confirmation back to bot (only if channel is valid)
    if channel and channel ~= "-1" then
        local msg = {
            command = "createFlightPlanMarkers",
            plan_id = plan_id,
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
function dcsbot.removeFlightPlanMarkersInternal(plan_id)
    local planMarkers = dcsbot.flightPlanMarkers[plan_id]
    if planMarkers and planMarkers.markers then
        for _, marker in ipairs(planMarkers.markers) do
            trigger.action.removeMark(marker.id)
        end
        dcsbot.flightPlanMarkers[plan_id] = nil
    end
end

-- Remove markers for a flight plan
function dcsbot.removeFlightPlanMarkers(plan_id, channel)
    env.info('DCSServerBot - FlightPlan: removeFlightPlanMarkers(' .. plan_id .. ')')

    local count = 0
    local planMarkers = dcsbot.flightPlanMarkers[plan_id]
    if planMarkers and planMarkers.markers then
        count = #planMarkers.markers
    end

    dcsbot.removeFlightPlanMarkersInternal(plan_id)

    -- Send confirmation back to bot (only if channel is valid)
    if channel and channel ~= "-1" then
        local msg = {
            command = "removeFlightPlanMarkers",
            plan_id = plan_id,
            removed_count = count
        }
        dcsbot.sendBotTable(msg, channel)
    end
end

env.info("DCSServerBot - FlightPlan: mission.lua loaded.")
