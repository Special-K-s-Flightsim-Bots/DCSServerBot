-----------------------------------------------------
-- Flight Plan Plugin - Commands
-- Handles commands from Python -> DCS
-----------------------------------------------------
local base      = _G
local dcsbot    = base.dcsbot
local utils     = base.require("DCSServerBotUtils")

-- Create markers for a flight plan
function dcsbot.createFlightPlanMarkers(json)
    log.write('DCSServerBot', log.DEBUG, 'FlightPlan: createFlightPlanMarkers()')
    local channel = json.channel or "-1"
    local timeout = json.timeout or 0
    -- Use basicSerialize for all string values and tostring for numbers to prevent injection
    local script = 'dcsbot.createFlightPlanMarkers(' ..
        tostring(json.plan_id) .. ', ' ..
        tostring(json.coalition) .. ', ' ..
        utils.basicSerialize(json.callsign) .. ', ' ..
        utils.basicSerialize(json.departure_name) .. ', ' ..
        '{x=' .. tostring(json.departure_x) .. ', y=0, z=' .. tostring(json.departure_z) .. '}, ' ..
        utils.basicSerialize(json.destination_name) .. ', ' ..
        '{x=' .. tostring(json.destination_x) .. ', y=0, z=' .. tostring(json.destination_z) .. '}, ' ..
        utils.basicSerialize(json.alternate_name or '') .. ', ' ..
        (json.alternate_x and ('{x=' .. tostring(json.alternate_x) .. ', y=0, z=' .. tostring(json.alternate_z) .. '}') or 'nil') .. ', ' ..
        utils.basicSerialize(json.aircraft_type or '') .. ', ' ..
        tostring(json.cruise_altitude or 0) .. ', ' ..
        utils.basicSerialize(json.etd or '') .. ', ' ..
        utils.basicSerialize(json.waypoints or '[]') .. ', ' ..
        utils.basicSerialize(channel) .. ', ' ..
        tostring(timeout) .. ')'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end

-- Remove markers for a flight plan
function dcsbot.removeFlightPlanMarkers(json)
    log.write('DCSServerBot', log.DEBUG, 'FlightPlan: removeFlightPlanMarkers()')
    local channel = json.channel or "-1"
    local script = 'dcsbot.removeFlightPlanMarkers(' .. tostring(json.plan_id) .. ', ' .. utils.basicSerialize(channel) .. ')'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end

-- Check proximity of a unit to destination for flight plan auto-completion
function dcsbot.checkFlightPlanProximity(json)
    log.write('DCSServerBot', log.DEBUG, 'FlightPlan: checkFlightPlanProximity()')
    local channel = json.channel or "-1"
    local script = 'dcsbot.checkFlightPlanProximity(' ..
        utils.basicSerialize(json.unit_name) .. ', ' ..
        tostring(json.plan_id) .. ', ' ..
        '{x=' .. tostring(json.dest_x) .. ', z=' .. tostring(json.dest_z) .. '}, ' ..
        tostring(json.threshold or 3000) .. ', ' ..
        utils.basicSerialize(channel) .. ')'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end
