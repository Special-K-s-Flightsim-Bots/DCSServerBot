local config = require('DCSServerBotConfig')

dcsbot = dcsbot or {}

local GROUP_CATEGORY = {
	[Group.Category.AIRPLANE] = 'Airplanes',
	[Group.Category.HELICOPTER] = 'Helicopters',
	[Group.Category.GROUND] = 'Ground Units',
	[Group.Category.SHIP] = 'Ships'
}

dcsbot.eventHandler = {}
function dcsbot.eventHandler:onEvent(event)
	if event then
		local msg = {}
		msg.command = 'onMissionEvent'
		msg.id = event.id
		if event.id == world.event.S_EVENT_BASE_CAPTURED then
			msg.eventName = 'BaseCaptured'
		elseif event.id == world.event.S_EVENT_DEAD or event.id == world.event.S_EVENT_PILOT_DEAD then
			msg.eventName = 'death'
		elseif event.id == world.event.S_EVENT_BIRTH then
			msg.eventName = 'birth'
		elseif event.id == world.event.S_EVENT_KILL then
			msg.eventName = 'kill'
		elseif event.id == world.event.S_EVENT_PLAYER_ENTER_UNIT then
			msg.eventName = 'join'
		elseif event.id == world.event.S_EVENT_PLAYER_LEAVE_UNIT then
			msg.eventName = 'dismiss'
		elseif event.id == world.event.S_EVENT_UNIT_LOST then
			msg.eventName = 'lost'
		else
			return -- ignore other events
		end
		msg.time = event.time
		if event.initiator then
			msg.initiator = {}
			category = event.initiator:getCategory()
			-- only gather events for units for now
			if category == Object.Category.UNIT then
				msg.initiator.unit = event.initiator
				msg.initiator.type = 'UNIT'
				msg.initiator.unit_name = msg.initiator.unit:getName()
				msg.initiator.group = msg.initiator.unit:getGroup()
				if msg.initiator.group and msg.initiator.group:isExist() then
					msg.initiator.group_name = msg.initiator.group:getName()
				end
				msg.initiator.name = msg.initiator.unit:getPlayerName()
				msg.initiator.coalition = msg.initiator.unit:getCoalition()
				msg.initiator.unit_type = msg.initiator.unit:getTypeName()
				msg.initiator.category = msg.initiator.unit:getDesc().category
			elseif category == Object.Category.STATIC then
				-- TODO: ejected pilot, might be useful in the future for possible SAR events
				--if event.id == 31 then
				--end
				msg.initiator.type = 'STATIC'
				msg.initiator.unit = event.initiator
				msg.initiator.unit_name = msg.initiator.unit:getName()
				msg.initiator.coalition = msg.initiator.unit:getCoalition()
				msg.initiator.unit_type = msg.initiator.unit:getTypeName()
				msg.initiator.category = msg.initiator.unit:getDesc().category
			end
		end
		if event.target then
			msg.target = {}
			category = event.target:getCategory()
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
			elseif category == Object.Category.STATIC then
				msg.target.type = 'STATIC'
				msg.target.unit = event.target
				if event.id ~= 33 then
					msg.target.unit_name = msg.target.unit:getName()
					msg.target.coalition = msg.target.unit:getCoalition()
					msg.target.unit_type = msg.target.unit:getTypeName()
					msg.target.category = msg.target.unit:getDesc().category
				end
			end
		end
		if event.place then
			msg.place = {}
			msg.place.id = event.place.id_
			msg.place.name = event.place:getName()
		end
		msg.subPlace = event.subPlace
		if event.weapon then
			msg.weapon = {}
			msg.weapon.name = event.weapon:getTypeName()
		end
		dcsbot.sendBotTable(msg)
	end
end

function dcsbot.enableMissionStats()
	dcsbot.eventHandler = world.addEventHandler(dcsbot.eventHandler)
	local msg = {}
	msg.command = 'enableMissionStats'
	msg.coalitions = {}
	msg.coalitions['Blue'] = {}
	msg.coalitions['Red'] = {}

	msg.coalitions['Blue'].airbases = {}
	for id, airbase in pairs(coalition.getAirbases(coalition.side.BLUE)) do
		table.insert(msg.coalitions['Blue'].airbases, airbase:getName())
	end
	msg.coalitions['Red'].airbases = {}
	for id, airbase in pairs(coalition.getAirbases(coalition.side.RED)) do
		table.insert(msg.coalitions['Red'].airbases, airbase:getName())
	end
	msg.coalitions['Blue'].units = {}
	for i, group in pairs(coalition.getGroups(coalition.side.BLUE)) do
		category = GROUP_CATEGORY[group:getCategory()]
		if (msg.coalitions['Blue'].units[category] == nil) then
			msg.coalitions['Blue'].units[category] = {}
		end
		for j, unit in pairs(Group.getUnits(group)) do
			if unit:isActive() then
				table.insert(msg.coalitions['Blue'].units[category], unit:getName())
			end
		end
	end
	msg.coalitions['Red'].units = {}
	for i, group in pairs(coalition.getGroups(coalition.side.RED)) do
		category = GROUP_CATEGORY[group:getCategory()]
		if (msg.coalitions['Red'].units[category] == nil) then
			msg.coalitions['Red'].units[category] = {}
		end
		for j, unit in pairs(Group.getUnits(group)) do
			if unit:isActive() then
				table.insert(msg.coalitions['Red'].units[category], unit:getName())
			end
		end
	end
	msg.coalitions['Blue'].statics = {}
	for id, static in pairs(coalition.getStaticObjects(coalition.side.BLUE)) do
		table.insert(msg.coalitions['Blue'].statics, static:getName())
	end
	msg.coalitions['Red'].statics = {}
	for id, static in pairs(coalition.getStaticObjects(coalition.side.RED)) do
		table.insert(msg.coalitions['Red'].statics, static:getName())
	end
	dcsbot.sendBotTable(msg)
	env.info('DCSServerBot - Mission Statistics enabled.')
end

function dcsbot.disableMissionStats()
	dcsbot.eventHandler = world.removeEventHandler(dcsbot.eventHandler)
	env.info('DCSServerBot - Mission Statistics disabled.')
end

do
	if config.MISSION_STATISTICS then
		dcsbot.enableMissionStats()
	end
end
