local base	    = _G
local Terrain   = base.require('terrain')

dcsbot 		    = base.dcsbot

local GROUP_CATEGORY = {
	[Group.Category.AIRPLANE] = 'Airplanes',
	[Group.Category.HELICOPTER] = 'Helicopters',
	[Group.Category.GROUND] = 'Ground Units',
	[Group.Category.SHIP] = 'Ships'
}

-- MOOSE
world.event.S_EVENT_NEW_CARGO = world.event.S_EVENT_MAX + 1000
world.event.S_EVENT_DELETE_CARGO = world.event.S_EVENT_MAX + 1001
world.event.S_EVENT_NEW_ZONE = world.event.S_EVENT_MAX + 1002
world.event.S_EVENT_DELETE_ZONE = world.event.S_EVENT_MAX + 1003
world.event.S_EVENT_NEW_ZONE_GOAL = world.event.S_EVENT_MAX + 1004
world.event.S_EVENT_DELETE_ZONE_GOAL = world.event.S_EVENT_MAX + 1005
world.event.S_EVENT_REMOVE_UNIT = world.event.S_EVENT_MAX + 1006
world.event.S_EVENT_PLAYER_ENTER_AIRCRAFT = world.event.S_EVENT_MAX + 1007

-- ECW
world.event.S_EVENT_ECW_TROOP_DROP   = world.event.S_EVENT_MAX + 1050
world.event.S_EVENT_ECW_TROOP_KILL   = world.event.S_EVENT_MAX + 1051
world.event.S_EVENT_ECW_TROOP_PICKUP = world.event.S_EVENT_MAX + 1052

dcsbot.mission_stats_enabled = false
dcsbot.eventHandler = dcsbot.eventHandler or {}

local event_by_id = {}

local function is_on_runway(runway, pos)
    local dx            = pos.x - runway.position.x
    local dz            = pos.z - runway.position.z

    local course_rad    = math.rad(runway.course)
    local proj          = dx * math.sin(course_rad) + dz * math.cos(course_rad)
    local lateral       = -dx * math.cos(course_rad) + dz * math.sin(course_rad)

    local half_len      = runway.length / 2.0
    local half_wid      = runway.width  / 2.0

    local on_length     = math.abs(proj) <= half_len
    local on_width      = math.abs(lateral) <= half_wid

    return on_length and on_width
end

function getDistance(point1, point2)
    local y1, y2

    if point1.z ~= nil then y1 = point1.z else y1 = point1.y end
    if point2.z ~= nil then y2 = point2.z else y2 = point2.y end

    local dx = point2.x - point1.x
    local dy = y2 - y1

    return math.sqrt(dx * dx + dy * dy)
end

function dcsbot.eventHandler:onEvent(event)
	status, err = pcall(onMissionEvent, event)
	if not status then
		env.warning("DCSServerBot - Error during MissionStatistics:onEvent(): " .. err)
	end
end

function onMissionEvent(event)
	if event == nil then
	    return
	end

    if event_by_id[event.id] == nil then
        return
    end

    local msg = {
        command = 'onMissionEvent',
        id = event.id,
        time = event.time,
        eventName = event_by_id[event.id]
    }

    if event.initiator then
        msg.initiator = {}
        local category = Object.getCategory(event.initiator)
        if category == Object.Category.UNIT then
            msg.initiator.type = 'UNIT'
            msg.initiator.unit = event.initiator
            msg.initiator.unit_name = msg.initiator.unit:getName()
            msg.initiator.group = msg.initiator.unit:getGroup()
            if msg.initiator.group and msg.initiator.group:isExist() then
                msg.initiator.group_name = msg.initiator.group:getName()
            end
            msg.initiator.name = msg.initiator.unit:getPlayerName()
            msg.initiator.coalition = msg.initiator.unit:getCoalition()
            msg.initiator.unit_type = msg.initiator.unit:getTypeName()
            msg.initiator.category = msg.initiator.unit:getDesc().category
            local point = msg.initiator.unit:getPosition().p
            local lat, lon = Terrain.convertMetersToLatLon(point.x, point.z)
            msg.initiator.position = {
                point = point,
                lat = lat,
                lon = lon
            }
            if event.id == world.event.S_EVENT_RUNWAY_TAKEOFF then
                if not event.place then
                    msg['eventName'] = 'S_EVENT_GROUND_TAKEOFF'
                else
                    local airbase = Airbase.getByName(event.place:getName())
                    if airbase:getDesc().category ~= Airbase.Category.SHIP then
                        local runways = airbase:getRunways()
                        local on_runway = false
                        for _, runway in pairs(runways) do
                            if is_on_runway(runway, point) then
                                on_runway = true
                                break
                            end
                        end
                        if not on_runway then
                            -- ignore unnecessary events for helicopters
                            if msg.initiator.category ~= Group.Category.AIRPLANE then
                                return
                            end
                            msg['eventName'] = 'S_EVENT_TAXIWAY_TAKEOFF'
                        end
                    end
                end
            end
        elseif category == Object.Category.WEAPON then
            msg.initiator.type = 'WEAPON'
            msg.initiator.unit = event.initiator
            msg.initiator.unit_name = msg.initiator.unit:getName()
            msg.initiator.coalition = msg.initiator.unit:getCoalition()
            msg.initiator.unit_type = msg.initiator.unit:getTypeName()
            msg.initiator.category = msg.initiator.unit:getDesc().category
        elseif category == Object.Category.STATIC then
            msg.initiator.type = 'STATIC'
            -- ejected pilot, unit will not be counted as dead but only lost
            if event.id == world.event.S_EVENT_LANDING_AFTER_EJECTION then
                msg.initiator.unit = event.initiator
                msg.initiator.unit_name = string.format("Ejected Pilot ID %s", tostring(event.initiator.id_))
                msg.initiator.coalition = 0
                msg.initiator.unit_type = 'Ejected Pilot'
                msg.initiator.category = 0
            else
                msg.initiator.unit = event.initiator
                msg.initiator.unit_name = msg.initiator.unit:getName()
                msg.initiator.coalition = msg.initiator.unit:getCoalition()
                msg.initiator.unit_type = msg.initiator.unit:getTypeName()
            end
        elseif category == Object.Category.BASE then
            msg.initiator.type = 'BASE'
            msg.initiator.unit = event.initiator
            msg.initiator.unit_name = msg.initiator.unit:getName()
            msg.initiator.coalition = msg.initiator.unit:getCoalition()
            msg.initiator.unit_type = msg.initiator.unit:getTypeName()
        elseif category == Object.Category.SCENERY  then
            msg.initiator.type = 'SCENERY'
            msg.initiator.unit = event.initiator
            if msg.initiator.unit.getName ~= nil then
                msg.initiator.unit_name = msg.initiator.unit:getName()
            else
                msg.initiator.unit_name = 'n/a'
            end
            if msg.initiator.unit.getTypeName ~= nil then
                msg.initiator.unit_type = msg.initiator.unit:getTypeName()
            else
                msg.initiator.unit_type = "SCENERY"
            end
            msg.initiator.coalition = coalition.side.NEUTRAL
        elseif category == Object.Category.CARGO then
            msg.initiator.type = 'CARGO'
            msg.initiator.unit = event.initiator
            msg.initiator.unit_name = msg.initiator.unit:getName()
            msg.initiator.coalition = msg.initiator.unit:getCoalition()
            msg.initiator.unit_type = msg.initiator.unit:getTypeName()
        else
            -- ignore the event
            return
        end
    end
    if event.target then
        msg.target = {}
        local category = Object.getCategory(event.target)
        if category == Object.Category.UNIT then
            msg.target.type = 'UNIT'
            msg.target.unit = event.target
            msg.target.unit_name = msg.target.unit:getName()
            msg.target.group = msg.target.unit:getGroup()
            if msg.target.group and msg.target.group:isExist() then
                msg.target.group_name = msg.target.group:getName()
            end
            msg.target.name = msg.target.unit:getPlayerName()
            msg.target.coalition = msg.target.unit:getCoalition()
            msg.target.unit_type = msg.target.unit:getTypeName()
            msg.target.category = msg.target.unit:getDesc().category
        elseif category == Object.Category.WEAPON then
            msg.target.type = 'WEAPON'
            msg.target.unit = event.target
            msg.target.unit_name = msg.target.unit:getName()
            msg.target.coalition = msg.target.unit:getCoalition()
            msg.target.unit_type = msg.target.unit:getTypeName()
            msg.target.category = msg.target.unit:getDesc().category
        elseif category == Object.Category.STATIC then
            msg.target.type = 'STATIC'
            msg.target.unit = event.target
            if msg.target.unit.isExist ~= nil and msg.target.unit:isExist() == true then
                msg.target.unit_name = msg.target.unit:getName()
                if msg.target.unit_name ~= nil and msg.target.unit_name ~= '' then
                    msg.target.coalition = msg.target.unit:getCoalition()
                    msg.target.unit_type = msg.target.unit:getTypeName()
                end
            end
        elseif category == Object.Category.BASE then
            msg.target.type = 'BASE'
            msg.target.unit = event.target
            msg.target.unit_name = msg.target.unit:getName()
            msg.target.coalition = msg.target.unit:getCoalition()
            msg.target.unit_type = msg.target.unit:getTypeName()
        elseif category == Object.Category.SCENERY then
            msg.target.type = 'SCENERY'
            msg.target.unit = event.target
            if msg.target.unit.getName ~= nil then
                msg.target.unit_name = msg.target.unit:getName()
            else
                msg.target.unit_name = 'n/a'
            end
            msg.target.coalition = coalition.side.NEUTRAL
            if msg.target.unit.getTypeName ~= nil then
                msg.target.unit_type = msg.target.unit:getTypeName()
            else
                msg.target.unit_type = 'n/a'
            end
        elseif category == Object.Category.CARGO then
            msg.target.type = 'CARGO'
            msg.target.unit = event.target
            msg.target.unit_name = msg.target.unit:getName()
            msg.target.coalition = msg.target.unit:getCoalition()
            msg.target.unit_type = msg.target.unit:getTypeName()
        else
            -- ignore the event
            return
        end
    end
    if event.place and event.place:isExist() then
        msg.place = {}
        msg.place.id = event.place.id_
        msg.place.name = event.place:getName()
    end
    if event.weapon and event.weapon:isExist() then
        msg.weapon = {}
        msg.weapon.name = event.weapon:getTypeName()
        -- msg.weapon.category = event.weapon:getDesc().category
        if msg.weapon.name == nil or msg.weapon.name == '' then
            msg.weapon.name = 'Gun'
        end
    elseif event.weapon_name then
        msg.weapon = {}
        msg.weapon.name = event.weapon_name
        if msg.weapon.name == nil or msg.weapon.name == '' then
            msg.weapon.name = 'Gun'
        end
    end
    if event.comment then
        msg.comment = event.comment
    end
    dcsbot.sendBotTable(msg)
end

function fillCoalitionsData(color)
    local coalitionColor = {}

    coalitionColor.airbases = {}
    for _, airbase in pairs(coalition.getAirbases(coalition.side[color])) do
        table.insert(coalitionColor.airbases, airbase:getName())
    end

    coalitionColor.units = {}
    for _, group in pairs(coalition.getGroups(coalition.side[color])) do
        local category = GROUP_CATEGORY[group:getCategory()]
        if category ~= nil then
            if (coalitionColor.units[category] == nil) then
                coalitionColor.units[category] = {}
            end
            for _, unit in pairs(Group.getUnits(group)) do
                if unit:isExist() and unit:isActive() then
                    table.insert(coalitionColor.units[category], unit:getName())
                end
            end
        else
            env.warning('Category not in table: ' .. group:getCategory(), false)
        end
    end

    coalitionColor.statics = {}
	for _, static in pairs(coalition.getStaticObjects(coalition.side[color])) do
		table.insert(coalitionColor.statics, static:getName())
    end
	return coalitionColor
end

function dcsbot.getMissionSituation(channel)
    env.info('DCSServerBot - getMissionSituation()')
    local msg = {
        command = 'getMissionSituation',
        coalitions = {
			BLUE = fillCoalitionsData('BLUE'),
			RED = fillCoalitionsData('RED'),
			NEUTRAL = fillCoalitionsData('NEUTRAL')
		}
    }
    dcsbot.sendBotTable(msg, channel)
end

function dcsbot.enableMissionStats(filter)
    filter = net.json2lua(filter)

    local filter_lookup = {}
    for _, v in ipairs(filter) do
        filter_lookup[v] = true
    end

    if not dcsbot.mission_stats_enabled then
        for k, v in pairs(world.event) do
            if not filter_lookup[k] then
                event_by_id[v] = k
            end
        end
    end
    world.addEventHandler(dcsbot.eventHandler)
    env.info('DCSServerBot - Mission Statistics enabled.')
    dcsbot.mission_stats_enabled = true
end

function dcsbot.disableMissionStats()
	if dcsbot.mission_stats_enabled then
        world.removeEventHandler(dcsbot.eventHandler)
        env.info('DCSServerBot - Mission Statistics disabled.')
        dcsbot.mission_stats_enabled = false
    end
end

env.info("DCSServerBot - MissionStats: mission.lua loaded.")
