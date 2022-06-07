local base 	= _G
local UC   	= base.require("utils_common")
local dcsbot= base.dcsbot
local utils = base.require("DCSServerBotUtils")

local mod_dictionary= require('dictionary')

function dcsbot.getMissionDetails(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getMissionDetails()')
	local msg = {}
	msg.command = 'getMissionDetails'
	msg.current_mission = DCS.getMissionName()
	msg.mission_time = DCS.getModelTime()
  	msg.real_time = DCS.getRealTime()
    msg.briefing = mod_dictionary.getBriefingData(DCS.getMissionFilename(), 'EN')
    msg.results = {}
    msg.results['Blue'] = DCS.getMissionResult("blue")
    msg.results['Red'] = DCS.getMissionResult("red")
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.getMissionUpdate(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getMissionUpdate()')
	local msg = {}
	msg.command = 'getMissionUpdate'
	msg.pause = DCS.getPause()
	msg.mission_time = DCS.getModelTime()
  	msg.real_time = DCS.getRealTime()
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.listMissions(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: listMissions()')
	local msg = net.missionlist_get()
	msg.command = 'listMissions'
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.startMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: startMission()')
	net.missionlist_run(json.id)
	local mission_list = net.missionlist_get()
	utils.saveSettings({
		missionList=mission_list["missionList"],
		listStartIndex=json.id
	})
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
		x = json.x,
		y = json.y,
		z = json.z
	}
	local temp, pressure = Weather.getTemperatureAndPressureAtPoint({position = position})
	local weather = DCS.getCurrentMission().mission.weather
	msg.temp = temp
	local pressureQFE = pressure / 100
	msg.qfe = {
		pressureHPA = pressureQFE,
		pressureMM = pressureQFE * 0.7500637554192,
		pressureIN = pressureQFE * 0.0295300586467
	}
	local pressureQNH = pressureQFE + pressureQFE * (10^(position.y / (18429.1 + 67.53 * temp + 0.003 * position.y)) - 1)
	msg.qnh = {
		pressureHPA = pressureQNH,
		pressureMM = pressureQNH * 0.7500637554192,
		pressureIN = pressureQNH * 0.0295300586467
	}
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
		net.dostring_in('mission', 'a_out_text_delay_u('.. to ..', ' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
	elseif to == 'all' then
		net.dostring_in('mission', 'a_out_text_delay(' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
	elseif to == 'red' then
		net.dostring_in('mission', 'a_out_text_delay_s(\'red\', ' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
	elseif to == 'blue' then
		net.dostring_in('mission', 'a_out_text_delay_s(\'blue\', ' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
	end
end

function dcsbot.do_script(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: do_script()')
    net.dostring_in('mission', 'a_do_script(' .. basicSerialize(json.script) .. ')')
end

function dcsbot.do_script_file(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: do_script_file()')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. json.file .. '\\")")')
end
