local base 	= _G
local UC   	= base.require("utils_common")
local dcsbot= base.dcsbot
local utils = base.require("DCSServerBotUtils")

dcsbot.SlotsData = {}

-- from perun
function dcsbot.updateSlots()
	if dcsbot.SlotsData['coalitions'] == nil then
		dcsbot.SlotsData['coalitions'] = DCS.getAvailableCoalitions()
		dcsbot.SlotsData['slots'] = {}

		-- Build up slot table
		for _j, _i in pairs(dcsbot.SlotsData['coalitions']) do
			dcsbot.SlotsData['slots'][_j]=DCS.getAvailableSlots(_j)

			for _sj, _si in pairs(dcsbot.SlotsData['slots'][_j]) do
				dcsbot.SlotsData['slots'][_j][_sj]['countryName']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['onboard_num']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['groupSize']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['groupName']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['callsign']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['task']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['airdromeId']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['helipadName']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['multicrew_place']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['role']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['helipadUnitType']= nil
				dcsbot.SlotsData['slots'][_j][_sj]['action']= nil
			end
		end
	end
	return dcsbot.SlotsData
end

function dcsbot.getRunningMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getRunningMission()')
	local msg = {}
	msg.command = 'getRunningMission'
	msg.current_mission = DCS.getMissionName()
  	msg.current_map = DCS.getCurrentMission().mission.theatre
	msg.mission_time = DCS.getModelTime()
  	msg.real_time = DCS.getRealTime()
	msg.num_players = table.getn(net.get_player_list())
	msg.start_time = DCS.getCurrentMission().mission.start_time
	msg.date = DCS.getCurrentMission().mission.date
	local weather = DCS.getCurrentMission().mission.weather
	msg.weather = weather
	local clouds = weather.clouds
	if clouds.preset ~= nil then
		local presets = nil
		local func, err = loadfile(lfs.currentdir() .. '/Config/Effects/clouds.lua')

		local env = {
		  type = _G.type,
		  next = _G.next,
		  setmetatable = _G.setmetatable,
		  getmetatable = _G.getmetatable,
		  _ = _,
		}
		setfenv(func, env)
		func()
		local preset = env.clouds and env.clouds.presets and env.clouds.presets[clouds.preset]
		if preset ~= nil then
		  msg.clouds = {}
		  msg.clouds.base = clouds.base
		  msg.clouds.preset = preset
		end
	else
		msg.clouds = clouds
	end
	msg.pause = DCS.getPause()
	if (dcsbot.updateSlots()['slots']['blue'] ~= nil) then
		msg.num_slots_blue = table.getn(dcsbot.updateSlots()['slots']['blue'])
	end
	if (dcsbot.updateSlots()['slots']['red'] ~= nil) then
		msg.num_slots_red = table.getn(dcsbot.updateSlots()['slots']['red'])
	end
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.getMissionDetails(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getMissionDetails()')
	local msg = {}
	msg.command = 'getMissionDetails'
	msg.current_mission = DCS.getMissionName()
	msg.mission_description = DCS.getMissionDescription()
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.getCurrentPlayers(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getCurrentPlayers()')
	local msg = {}
	msg.command = 'getCurrentPlayers'
	plist = net.get_player_list()
	if (table.getn(plist) > 0) then
		msg.players = {}
		for i = 1, table.getn(plist) do
			msg.players[i] = net.get_player_info(plist[i])
			msg.players[i].unit_type, msg.players[i].slot, msg.players[i].sub_slot = utils.getMulticrewAllParameters(plist[i])
			msg.players[i].unit_name = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_NAME)
			msg.players[i].group_name = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_GROUPNAME)
			msg.players[i].group_id = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_GROUP_MISSION_ID)
			msg.players[i].unit_callsign = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_CALLSIGN)
			-- server user is never active
			if (msg.players[i].id == 1) then
				msg.players[i].active = false
			else
				msg.players[i].active = true
			end
		end
		utils.sendBotTable(msg, json.channel)
	end
end

function dcsbot.listMissions(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: listMissions()')
	local msg = net.missionlist_get()
	msg.command = 'listMissions'
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.startMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: startMission()')
	if (net.missionlist_run(json.id) == true) then
		local mission_list = net.missionlist_get()
		utils.saveSettings({
			missionList=mission_list["missionList"],
			listStartIndex=mission_list["listStartIndex"]
		})
	end
end

function dcsbot.startNextMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: startNextMission()')
	local result = net.load_next_mission()
	if (result == false) then
		result = net.missionlist_run(1)
	end
	if (result == true) then
		local mission_list = net.missionlist_get()
		utils.saveSettings({
			missionList=mission_list["missionList"],
			listStartIndex=mission_list["listStartIndex"]
		})
	end
end

function dcsbot.restartMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: restartMission()')
	net.load_mission(DCS.getMissionFilename())
end

function dcsbot.pauseMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: pauseMission()')
	DCS.setPause(true)
end

function dcsbot.unpauseMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: unpauseMission()')
	DCS.setPause(false)
end

function dcsbot.addMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: addMission()')
	net.missionlist_append(lfs.writedir() .. 'Missions\\' .. json.path)
	local current_missions = net.missionlist_get()
	result = utils.saveSettings({missionList = current_missions["missionList"]})
	dcsbot.listMissions(json)
end

function dcsbot.deleteMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: deleteMission()')
	net.missionlist_delete(json.id)
	local current_missions = net.missionlist_get()
	result = utils.saveSettings({missionList = current_missions["missionList"]})
	dcsbot.listMissions(json)
end

function dcsbot.listMizFiles(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: listMizFiles()')
	local msg = {}
	msg.command = 'listMizFiles'
	msg.missions = {}
	for file in lfs.dir(lfs.writedir() .. 'Missions') do
		if ((lfs.attributes(file, 'mode') ~= 'directory') and (file:sub(-4) == '.miz')) then
			table.insert(msg.missions, file)
		end
	end
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.getWeatherInfo(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getWeatherInfo()')
	local msg = {}
	msg.command = 'getWeatherInfo'
	local position = {
		x = json.lat,
		y = json.alt,
		z = json.lng,
	}
	local temp, pressure = Weather.getTemperatureAndPressureAtPoint({position = position})
	local weather = DCS.getCurrentMission().mission.weather
	msg.temp = temp
	msg.pressureHPA = pressure/100
	msg.pressureMM = pressure * 0.007500637554192
	msg.pressureIN = pressure * 0.000295300586467
	msg.weather = weather
	local clouds = msg.weather.clouds
	if clouds.preset ~= nil then
		local func, err = loadfile(lfs.currentdir() .. '/Config/Effects/clouds.lua')

		local env = {
			type = _G.type,
			next = _G.next,
			setmetatable = _G.setmetatable,
			getmetatable = _G.getmetatable,
			_ = _,
		}
		setfenv(func, env)
		func()
		local preset = env.clouds and env.clouds.presets and env.clouds.presets[clouds.preset]
		if preset ~= nil then
			msg.clouds = {}
			msg.clouds.base = clouds.base
			msg.clouds.preset = preset
		end
	else
		msg.clouds = clouds
	end
	msg.turbulence = UC.composeTurbulenceString(weather)
	msg.wind = UC.composeWindString(weather, position)
	utils.sendBotTable(msg, json.channel)
end

function basicSerialize(s)
	if s == nil then
		return "\"\""
	else
		if ((type(s) == 'number') or (type(s) == 'boolean') or (type(s) == 'function') or (type(s) == 'table') or (type(s) == 'userdata') ) then
			return tostring(s)
		elseif type(s) == 'string' then
			return string.format('%q', s)
		end
  end
end

function dcsbot.sendChatMessage(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: sendChatMessage()')
	local message = json.message
	if (json.from) then
		message = json.from .. ': ' .. message
	end
	if (json.to) then
		net.send_chat_to(message, json.to)
	else
		net.send_chat(message, true)
	end
end

function dcsbot.sendPopupMessage(json)
	log.write('DCSServerBot', log.DEBUG, 'Mission: sendPopupMessage()')
	local message = json.message
	if (json.from) then
		message = json.from .. ': ' .. message
	end
	time = json.time or 10
	to = json.to or 'all'
	if tonumber(to) then
		net.dostring_in('mission', 'a_out_text_delay_g('.. to ..', ' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
	elseif to == 'all' then
		net.dostring_in('mission', 'a_out_text_delay(' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
	elseif to == 'red' then
		net.dostring_in('mission', 'a_out_text_delay_s(\'red\', ' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
	elseif to == 'blue' then
		net.dostring_in('mission', 'a_out_text_delay_s(\'blue\', ' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
	end
end
