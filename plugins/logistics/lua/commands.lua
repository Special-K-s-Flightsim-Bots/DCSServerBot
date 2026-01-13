-----------------------------------------------------
-- Logistics Plugin - Commands
-- Handles commands from Python -> DCS
-----------------------------------------------------
local base      = _G
local dcsbot    = base.dcsbot
local utils     = base.require("DCSServerBotUtils")

-- Create/update markers for a logistics task
function dcsbot.createLogisticsMarkers(json)
    log.write('DCSServerBot', log.DEBUG, 'Logistics: createLogisticsMarkers()')
    local script = 'dcsbot.createLogisticsMarkers(' ..
        json.task_id .. ', ' ..
        json.coalition .. ', ' ..
        utils.basicSerialize(json.source_name) .. ', ' ..
        '{x=' .. json.source_x .. ', y=0, z=' .. json.source_z .. '}, ' ..
        utils.basicSerialize(json.dest_name) .. ', ' ..
        '{x=' .. json.dest_x .. ', y=0, z=' .. json.dest_z .. '}, ' ..
        utils.basicSerialize(json.cargo_type) .. ', ' ..
        utils.basicSerialize(json.pilot_name or '') .. ', ' ..
        utils.basicSerialize(json.deadline or '') .. ', ' ..
        utils.basicSerialize(json.waypoints or '[]') .. ', ' ..
        '"' .. json.channel .. '")'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end

-- Remove markers for a logistics task
function dcsbot.removeLogisticsMarkers(json)
    log.write('DCSServerBot', log.DEBUG, 'Logistics: removeLogisticsMarkers()')
    local script = 'dcsbot.removeLogisticsMarkers(' .. json.task_id .. ', "' .. json.channel .. '")'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end

-- Update marker with pilot name when task is assigned
function dcsbot.updateLogisticsMarkerPilot(json)
    log.write('DCSServerBot', log.DEBUG, 'Logistics: updateLogisticsMarkerPilot()')
    local script = 'dcsbot.updateLogisticsMarkerPilot(' ..
        json.task_id .. ', ' ..
        utils.basicSerialize(json.pilot_name) .. ', ' ..
        '"' .. json.channel .. '")'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end

-- Get player's current unit position
function dcsbot.getPlayerPosition(json)
    log.write('DCSServerBot', log.DEBUG, 'Logistics: getPlayerPosition()')
    local script = 'dcsbot.getPlayerPosition(' ..
        utils.basicSerialize(json.unit_name) .. ', ' ..
        '"' .. json.channel .. '")'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end

-- Check if player is within delivery proximity of destination
function dcsbot.checkDeliveryProximity(json)
    log.write('DCSServerBot', log.DEBUG, 'Logistics: checkDeliveryProximity()')
    local script = 'dcsbot.checkDeliveryProximity(' ..
        utils.basicSerialize(json.unit_name) .. ', ' ..
        json.task_id .. ', ' ..
        '{x=' .. json.dest_x .. ', y=0, z=' .. json.dest_z .. '}, ' ..
        json.threshold .. ', ' ..
        '"' .. json.channel .. '")'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end

-- Send popup message to player
function dcsbot.logisticsPopup(json)
    log.write('DCSServerBot', log.DEBUG, 'Logistics: logisticsPopup()')
    local script = 'dcsbot.logisticsPopup(' ..
        json.coalition .. ', ' ..
        utils.basicSerialize(json.message) .. ', ' ..
        (json.time or 10) .. ')'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end
