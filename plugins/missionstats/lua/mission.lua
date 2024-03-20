local base	= _G
dcsbot 		= base.dcsbot

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

dcsbot.eventHandler = {}
function dcsbot.eventHandler:onEvent(event)
	status, err = pcall(onEvent, event)
	if not status then
		env.warning("DCSServerBot - Error during MissionStatistics:onEvent(): " .. err)
	end
end

function onEvent(event)
	if event then
		local msg = {
			command = 'onMissionEvent',
			id = event.id,
			time = event.time
		}
		if event.id == world.event.S_EVENT_SHOT then
			msg.eventName = 'S_EVENT_SHOT'
		elseif event.id == world.event.S_EVENT_HIT then
			msg.eventName = 'S_EVENT_HIT'
		elseif event.id == world.event.S_EVENT_TAKEOFF then
			msg.eventName = 'S_EVENT_TAKEOFF'
		elseif event.id == world.event.S_EVENT_LAND then
			msg.eventName = 'S_EVENT_LAND'
		elseif event.id == world.event.S_EVENT_CRASH then
			msg.eventName = 'S_EVENT_CRASH'
		elseif event.id == world.event.S_EVENT_EJECTION then
			msg.eventName = 'S_EVENT_EJECTION'
		elseif event.id == world.event.S_EVENT_DEAD then
			msg.eventName = 'S_EVENT_DEAD'
		elseif event.id == world.event.S_EVENT_PILOT_DEAD then
			msg.eventName = 'S_EVENT_PILOT_DEAD'
		elseif event.id == world.event.S_EVENT_BASE_CAPTURED then
			msg.eventName = 'S_EVENT_BASE_CAPTURED'
		elseif event.id == world.event.S_EVENT_TOOK_CONTROL then
			msg.eventName = 'S_EVENT_TOOK_CONTROL'
		elseif event.id == world.event.S_EVENT_REFUELING_STOP then
			msg.eventName = 'S_EVENT_REFUELING_STOP'
		elseif event.id == world.event.S_EVENT_BIRTH then
			msg.eventName = 'S_EVENT_BIRTH'
		elseif event.id == world.event.S_EVENT_HUMAN_FAILURE then
			msg.eventName = 'S_EVENT_HUMAN_FAILURE'
		elseif event.id == world.event.S_EVENT_DETAILED_FAILURE then
			msg.eventName = 'S_EVENT_DETAILED_FAILURE'
		elseif event.id == world.event.S_EVENT_ENGINE_STARTUP then
			msg.eventName = 'S_EVENT_ENGINE_STARTUP'
		elseif event.id == world.event.S_EVENT_ENGINE_SHUTDOWN then
			msg.eventName = 'S_EVENT_ENGINE_SHUTDOWN'
		elseif event.id == world.event.S_EVENT_PLAYER_LEAVE_UNIT then
			msg.eventName = 'S_EVENT_PLAYER_LEAVE_UNIT'
		elseif event.id == world.event.S_EVENT_SHOOTING_START then
			msg.eventName = 'S_EVENT_SHOOTING_START'
		elseif event.id == world.event.S_EVENT_SHOOTING_END then
			msg.eventName = 'S_EVENT_SHOOTING_END'
		elseif event.id == world.event.S_EVENT_KILL then
			msg.eventName = 'S_EVENT_KILL'
		elseif event.id == world.event.S_EVENT_UNIT_LOST then
			msg.eventName = 'S_EVENT_UNIT_LOST'
		elseif event.id == world.event.S_EVENT_LANDING_AFTER_EJECTION then
			msg.eventName = 'S_EVENT_LANDING_AFTER_EJECTION'
		elseif event.id == world.event.S_EVENT_PARATROOPER_LENDING then
			msg.eventName = 'S_EVENT_PARATROOPER_LANDING'
		elseif event.id == world.event.S_EVENT_TRIGGER_ZONE then
			msg.eventName = 'S_EVENT_TRIGGER_ZONE'
		elseif event.id == world.event.S_EVENT_LANDING_QUALITY_MARK then
			msg.eventName = 'S_EVENT_LANDING_QUALITY_MARK'
		elseif event.id == world.event.S_EVENT_BDA then
			msg.eventName = 'S_EVENT_BDA'
		elseif event.id == world.event.S_EVENT_MAX then
			msg.eventName = 'S_EVENT_MAX'
		elseif event.id == world.event.S_EVENT_EMERGENCY_LANDING then
		    msg.eventName = 'S_EVENT_EMERGENCY_LANDING'
		-- MOOSE
		elseif event.id == world.event.S_EVENT_NEW_CARGO then
			msg.eventName = 'S_EVENT_NEW_CARGO'
		elseif event.id == world.event.S_EVENT_DELETE_CARGO then
			msg.eventName = 'S_EVENT_DELETE_CARGO'
		-- ECW
		elseif event.id == world.event.S_EVENT_ECW_TROOP_DROP then
			msg.eventName = 'S_EVENT_ECW_TROOP_DROP'
		elseif event.id == world.event.S_EVENT_ECW_TROOP_KILL then
			msg.eventName = 'S_EVENT_ECW_TROOP_KILL'
		elseif event.id == world.event.S_EVENT_ECW_TROOP_PICKUP then
			msg.eventName = 'S_EVENT_ECW_TROOP_PICKUP'
		else
			return -- ignore other events
		end
		if event.initiator then
			msg.initiator = {}
			category = Object.getCategory(event.initiator)
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
				msg.initiator.category = msg.initiator.unit
				msg.initiator.category = msg.initiator.unit:getDesc().category
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
				msg.initiator.unit_name = msg.initiator.unit:getName()
				msg.initiator.coalition = coalition.side.NEUTRAL
				msg.initiator.unit_type = event.initiator:isExist() and msg.initiator.unit:getTypeName() or "SCENERY"
			elseif category == Object.Category.CARGO then
				msg.initiator.type = 'CARGO'
				msg.initiator.unit = event.initiator
				msg.initiator.unit_name = msg.initiator.unit:getName()
				msg.initiator.coalition = msg.initiator.unit:getCoalition()
				msg.initiator.unit_type = msg.initiator.unit:getTypeName()
			end
		end
		if event.target then
			msg.target = {}
			category = Object.getCategory(event.target)
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
				if msg.target.unit.isExist ~= nil and msg.target.unit:isExist() then
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
				msg.target.unit_name = msg.target.unit:getName()
                msg.target.coalition = coalition.side.NEUTRAL
				msg.target.unit_type = msg.target.unit:getTypeName()
			elseif category == Object.Category.CARGO then
				msg.target.type = 'CARGO'
				msg.target.unit = event.target
				msg.target.unit_name = msg.target.unit:getName()
                msg.target.coalition = msg.target.unit:getCoalition()
				msg.target.unit_type = msg.target.unit:getTypeName()
			end
		end
		if event.place and event.place:isExist() then
			msg.place = {}
			msg.place.id = event.place.id_
			msg.place.name = event.place:getName()
		end
		if event.weapon then
			msg.weapon = {}
			msg.weapon.name = event.weapon:getTypeName()
			msg.weapon.category = event.weapon:getDesc().category
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
                if unit:isActive() then
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
			-- NEUTRAL = fillCoalitionsData('NEUTRAL')
		}
    }
    dcsbot.sendBotTable(msg, channel)
end

function dcsbot.enableMissionStats()
	if not dcsbot.mission_stats_enabled then
        world.addEventHandler(dcsbot.eventHandler)
        env.info('DCSServerBot - Mission Statistics enabled.')
        dcsbot.mission_stats_enabled = true
    end
end

function dcsbot.disableMissionStats()
	if dcsbot.mission_stats_enabled then
        world.removeEventHandler(dcsbot.eventHandler)
        env.info('DCSServerBot - Mission Statistics disabled.')
        dcsbot.mission_stats_enabled = false
    end
end

env.info("DCSServerBot - MissionStats: mission.lua loaded.")
