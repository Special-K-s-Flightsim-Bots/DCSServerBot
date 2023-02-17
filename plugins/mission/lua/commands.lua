local base 		= _G
local Terrain   = base.require('terrain')
local UC   		= base.require("utils_common")
local dcsbot	= base.dcsbot
local config	= base.require("DCSServerBotConfig")
local utils 	= base.require("DCSServerBotUtils")

local mod_dictionary= require('dictionary')

dcsbot.registered = false
dcsbot.userInfo = dcsbot.userInfo or {}

function dcsbot.loadParams(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: loadParams(' .. json.plugin ..')')
    dcsbot.params = dcsbot.params or {}
    dcsbot.params[json.plugin] = json.params
end

function dcsbot.registerDCSServer(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: registerDCSServer()')
	local msg = {}
	msg.command = 'registerDCSServer'
	msg.hook_version = config.VERSION
	msg.dcs_version = Export.LoGetVersionInfo().ProductVersion[1] .. '.' .. Export.LoGetVersionInfo().ProductVersion[2] .. '.' .. Export.LoGetVersionInfo().ProductVersion[3] .. '.' .. Export.LoGetVersionInfo().ProductVersion[4]
    msg.host = config.DCS_HOST
	msg.port = config.DCS_PORT
	msg.chat_channel = config.CHAT_CHANNEL
	msg.status_channel = config.STATUS_CHANNEL
	msg.admin_channel = config.ADMIN_CHANNEL
	-- backwards compatibility
	if (config.STATISTICS ~= nil) then
		msg.statistics = config.STATISTICS
	else
		msg.statistics = true
	end
    -- airbases
    msg.airbases = {}
    local airdromes = Terrain.GetTerrainConfig("Airdromes")
    if (airdromes ~= nil) then
        for airdromeID, airdrome in pairs(airdromes) do
            if (airdrome.reference_point) and (airdrome.abandoned ~= true)  then
                local airbase = {}
                airbase.code = airdrome.code
                if airdrome.display_name then
                    airbase.name = airdrome.display_name
                else
                    airbase.name = airdrome.names['en']
                end
                airbase.id = airdrome.id
                airbase.lat, airbase.lng = Terrain.convertMetersToLatLon(airdrome.reference_point.x, airdrome.reference_point.y)
                airbase.alt = Terrain.GetHeight(airdrome.reference_point.x, airdrome.reference_point.y)
                airbase.position = {}
                airbase.position.x = airdrome.reference_point.x
                airbase.position.y = airbase.alt
                airbase.position.z = airdrome.reference_point.y

                local frequencyList = {}
                if airdrome.frequency then
                    frequencyList	= airdrome.frequency
                else
                    if airdrome.radio then
                        for k, radioId in pairs(airdrome.radio) do
                            local frequencies = DCS.getATCradiosData(radioId)
                            if frequencies then
                                for kk,vv in pairs(frequencies) do
                                    table.insert(frequencyList, vv)
                                end
                            end
                        end
                    end
                end
                airbase.frequencyList = frequencyList
                airbase.runwayList = {}
                if (airdrome.runwayName ~= nil) then
                    for r, runwayName in pairs(airdrome.runwayName) do
                        table.insert(airbase.runwayList, runwayName)
                    end
                end
                heading = UC.toDegrees(Terrain.getRunwayHeading(airdrome.roadnet))
                if (heading < 0) then
                    heading = 360 + heading
                end
                airbase.rwy_heading = heading
                table.insert(msg.airbases, airbase)
            end
        end
    end
    -- mission
    if DCS.getCurrentMission() then
        msg.filename = DCS.getMissionFilename()
        msg.current_mission = DCS.getMissionName()
        msg.current_map = DCS.getCurrentMission().mission.theatre
        msg.mission_time = DCS.getModelTime()
        msg.real_time = DCS.getRealTime()
        msg.start_time = DCS.getCurrentMission().mission.start_time
        msg.date = DCS.getCurrentMission().mission.date
        msg.pause = DCS.getPause()
        -- weather
        local weather = DCS.getCurrentMission().mission.weather
        msg.weather = weather
        local clouds = weather.clouds
        if clouds.preset ~= nil then
            local presets
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
        -- slots
        msg.num_slots_blue = table.getn(DCS.getAvailableSlots('blue'))
        msg.num_slots_red = table.getn(DCS.getAvailableSlots('red'))
        -- players
        plist = net.get_player_list()
        num_players = table.getn(plist)
        if num_players > 0 then
            msg.players = {}
            for i = 1, num_players do
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
        end
    end
    -- check if DSMC is enabled
    msg.dsmc_enabled = (base.HOOK ~= nil)
    if (json ~= nil) then
        utils.sendBotTable(msg, json.channel)
    else
        utils.sendBotTable(msg)
    end
    dcsbot.registered = true
end

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
	if not string.find(json.path, '\\') then
		path = lfs.writedir() .. 'Missions\\' .. json.path
	else
		path = json.path
	end
	net.missionlist_append(path)
	local current_missions = net.missionlist_get()
	result = utils.saveSettings({missionList = current_missions["missionList"]})
	dcsbot.listMissions(json)
end

function dcsbot.deleteMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: deleteMission()')
	net.missionlist_delete(json.id)
	local current_missions = net.missionlist_get()
	--result = utils.saveSettings({missionList = current_missions["missionList"]})
	utils.saveSettings({
		missionList=current_missions["missionList"],
		listStartIndex=current_missions["listStartIndex"]
	})
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
	local pressureQNH = pressureQFE + position.y * 0.12017
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

function dcsbot.sendChatMessage(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: sendChatMessage()')
	local message = json.message
	if (json.from) then
		message = json.from .. ': ' .. message
	end
	if json.to then
		if net.get_player_info(json.to) then
			net.send_chat_to(message, json.to)
		end
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
	net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize('dcsbot.sendPopupMessage("' .. to .. '", ' .. utils.basicSerialize(message) .. ', ' .. tostring(time) ..')') .. ')')
end

function dcsbot.uploadUserRoles(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: uploadUserRoles()')
    dcsbot.userInfo[json.ucid].roles = json.roles
end
