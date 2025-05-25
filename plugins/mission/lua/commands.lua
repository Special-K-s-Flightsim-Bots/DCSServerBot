local base 		= _G
local Terrain   = base.require('terrain')
local UC   		= base.require("utils_common")
local Weather   = base.require('Weather')
local dcsbot	= base.dcsbot
local config	= base.require("DCSServerBotConfig")
local utils 	= base.require("DCSServerBotUtils")

local mod_dictionary= require('dictionary')

dcsbot.registered = false
dcsbot.server_locked = false
dcsbot.banList = dcsbot.banList or {}
dcsbot.locked = dcsbot.locked or {}
dcsbot.userInfo = dcsbot.userInfo or {}
dcsbot.red_slots = dcsbot.red_slots or {}
dcsbot.blue_slots = dcsbot.blue_slots or {}

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
    -- airbases
    msg.airbases = {}
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
        msg.weather = {}
        -- slots
        num_slots_red = 0
        local availableSlots = DCS.getAvailableSlots("red")
        dcsbot.red_slots = {}
        if availableSlots ~= nil then
            for k,v in pairs(availableSlots) do
                dcsbot.red_slots[v.unitId] = v
                num_slots_red = num_slots_red + 1
            end
        end

        num_slots_blue = 0
        availableSlots = DCS.getAvailableSlots("blue")
        dcsbot.blue_slots = {}
        if availableSlots ~= nil then
            for k,v in pairs(availableSlots) do
                dcsbot.blue_slots[v.unitId] = v
                num_slots_blue = num_slots_blue + 1
            end
        end

        msg.num_slots_blue = num_slots_blue
        msg.num_slots_red = num_slots_red
        -- players
        msg.players = {}
        plist = net.get_player_list()
        for i = 1, #plist do
            msg.players[i] = net.get_player_info(plist[i])
            msg.players[i].ipaddr = utils.getIP(msg.players[i].ipaddr)
            msg.players[i].unit_type, msg.players[i].slot, msg.players[i].sub_slot = utils.getMulticrewAllParameters(plist[i])
            msg.players[i].unit_name = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_NAME)
            msg.players[i].unit_display_name = DCS.getUnitTypeAttribute(DCS.getUnitType(msg.players[i].slot), "DisplayName")
            msg.players[i].group_name = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_GROUPNAME)
            msg.players[i].group_id = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_GROUP_MISSION_ID)
            msg.players[i].unit_callsign = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_CALLSIGN)
            -- DCS MC bug workaround
            if msg.players[i].sub_slot > 0 and msg.players[i].side == 0 then
                if dcsbot.blue_slots[msg.players[i].slot] ~= nil then
                    msg.players[i].side = 2
                elseif dcsbot.red_slots[msg.players[i].slot] ~= nil then
                    msg.players[i].side = 1
                end
            end
            -- server user is never active
            if (msg.players[i].id == 1) then
                msg.players[i].active = false
            else
                msg.players[i].active = true
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
	local msg = {
        command = 'getMissionDetails',
        current_mission = DCS.getMissionName(),
        mission_time = DCS.getModelTime(),
        real_time = DCS.getRealTime(),
        briefing = mod_dictionary.getBriefingData(DCS.getMissionFilename(), 'EN'),
        results = {
            blue = DCS.getMissionResult("blue"),
            red = DCS.getMissionResult("red"),
            neutrals = DCS.getMissionResult("neutrals"),
        }
    }
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.getMissionUpdate(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getMissionUpdate()')
	local msg = {
        command = 'getMissionUpdate',
        pause = DCS.getPause(),
        mission_time = DCS.getModelTime(),
        real_time = DCS.getRealTime()
    }
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.getAirbases(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getAirbases()')
    local msg = {
        command = 'getAirbases',
        airbases = {}
    }
    local airdromes = Terrain.GetTerrainConfig("Airdromes")
    if (airdromes == nil) then
    	utils.sendBotTable(msg, json.channel)
    end
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
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.listMissions(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: listMissions()')
	local msg = net.missionlist_get()
	msg.command = json.command
	utils.sendBotTable(msg, json.channel)
end

function dcsbot.startMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: startMission()')
    if json.id ~= nil then
        json.result = net.missionlist_run(json.id)
        if json.result == true then
            utils.saveSettings({
                listStartIndex=json.id
            })
        end
    else
        json.result = net.load_mission(json.filename)
    end
	utils.sendBotTable(json, json.channel)
end

function dcsbot.startNextMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: startNextMission()')
	json.result = net.load_next_mission()
	if json.result == false then
		json.result = net.missionlist_run(1)
	end
	if json.result == true then
        local mission_list = net.missionlist_get()
		utils.saveSettings({
			listStartIndex=mission_list["listStartIndex"]
		})
	end
	utils.sendBotTable(json, json.channel)
end

function dcsbot.restartMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: restartMission()')
	json.result = net.load_mission(DCS.getMissionFilename())
	utils.sendBotTable(json, json.channel)
end

function dcsbot.pauseMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: pauseMission()')
	DCS.setPause(true)
end

function dcsbot.unpauseMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: unpauseMission()')
	DCS.setPause(false)
end

function dcsbot.setStartIndex(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: setStartIndex()')
	utils.saveSettings({
		listStartIndex = json.id
    })
end

function dcsbot.addMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: addMission()')
    local path
    if not string.find(json.path, '\\') then
		path = lfs.writedir() .. 'Missions\\' .. json.path
	else
		path = json.path
	end
	net.missionlist_append(path)
	if json.index ~= nil and tonumber(json.index) > 0 then
	    net.missionlist_move(#current_missions["missionList"], tonumber(json.index))
	end
	local current_missions = net.missionlist_get()
	local listStartIndex = current_missions["listStartIndex"]
    if json.autostart == true then
        listStartIndex = #current_missions['missionList']
    -- workaround DCS bug
    elseif #current_missions['missionList'] < listStartIndex then
        listStartIndex = 1
    end
	utils.saveSettings({
        missionList = current_missions["missionList"],
		listStartIndex = listStartIndex
    })
	dcsbot.listMissions(json)
end

function dcsbot.deleteMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: deleteMission()')
	net.missionlist_delete(json.id)
	local current_missions = net.missionlist_get()
    -- workaround DCS bug
	local listStartIndex = current_missions["listStartIndex"]
    if #current_missions['missionList'] < listStartIndex then
        listStartIndex = 1
    end
	utils.saveSettings({
		missionList = current_missions["missionList"],
		listStartIndex = listStartIndex
	})
	dcsbot.listMissions(json)
end

function dcsbot.replaceMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: replaceMission()')
	local current_missions = net.missionlist_get()
    net.missionlist_delete(tonumber(json.index))
    net.missionlist_append(json.path)
    net.missionlist_move(#current_missions["missionList"], tonumber(json.index))
	current_missions = net.missionlist_get()
    -- workaround DCS bug
	local listStartIndex = current_missions["listStartIndex"]
    if #current_missions['missionList'] < listStartIndex then
        listStartIndex = 1
    end
	utils.saveSettings({
		missionList = current_missions["missionList"],
		listStartIndex = listStartIndex
    })
	dcsbot.listMissions(json)
end

function dcsbot.listMizFiles(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: listMizFiles()')
	local msg = {
        command = 'listMizFiles'
    }
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
	local msg = {
        command = 'getWeatherInfo'
    }
	local weather = DCS.getCurrentMission().mission.weather
    if json.x then
        local position = {
            x = json.x,
            y = json.y,
            z = json.z
        }
    	Weather.initAtmospere(weather)
	    local temp, pressure = Weather.getTemperatureAndPressureAtPoint({position = position})
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
        msg.turbulence = UC.composeTurbulenceString(weather)
        local wind = Weather.getGroundWindAtPoint({position = position})
        msg.wind = {
            speed = wind.v,
            dir = UC.toPositiveDegrees(wind.a + math.pi)
        }
    end
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
			msg.clouds = {
                base = clouds.base,
                preset = preset
            }
        end
	else
		msg.clouds = clouds
	end
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
	local time = json.time or 10
	net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize('dcsbot.sendPopupMessage2("' .. json.to .. '", "' .. json.id ..'", ' .. utils.basicSerialize(message) .. ', ' .. tostring(time) ..')') .. ')')
end

function dcsbot.playSound(json)
	log.write('DCSServerBot', log.DEBUG, 'Mission: playSound()')
	net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize('dcsbot.playSound2("' .. json.to .. '", "' .. json.id .. '", ' .. utils.basicSerialize(json.sound) .. ')') .. ')')
end

local function setUserRoles(json)
    dcsbot.userInfo[json.ucid] = dcsbot.userInfo[json.ucid] or {}
    dcsbot.userInfo[json.ucid].roles = json.roles
    local plist = net.get_player_list()

    for i = 2, #plist do
        if (net.get_player_info(plist[i], 'ucid') == json.ucid) then
            name = net.get_player_info(plist[i], 'name')
            break
        end
    end
    if name then
        local script = 'dcsbot._setUserRoles(' .. utils.basicSerialize(name) .. ', ' .. utils.basicSerialize(net.lua2json(json.roles)) .. ')'
        net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
    end
end

function dcsbot.uploadUserRoles(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: uploadUserRoles()')
    if json.batch then
        for _, user_role in ipairs(json.batch) do
            setUserRoles(user_role)
        end
    else
        setUserRoles(json)
    end
end

function dcsbot.kick(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: kick()')
    if json.id then
        net.kick(json.id, json.reason)
        return
    end
    local plist = net.get_player_list()
    for i = 2, #plist do
        if ((json.ucid and net.get_player_info(plist[i], 'ucid') == json.ucid) or
                (json.name and net.get_player_info(plist[i], 'name') == json.name)) then
            net.kick(plist[i], json.reason)
            break
        end
    end
end

function dcsbot.force_player_slot(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: force_player_slot()')
    net.force_player_slot(json.playerID, json.sideID or 0, json.slotID or '')
    if json.reason ~= 'n/a' then
        net.send_chat_to(reason, json.playerID)
    end
end

local function single_ban(json)
    local banned_until = json.banned_until or 'never'
    local reason = json.reason .. '.\nExpires ' .. banned_until
    dcsbot.banList[json.ucid] = reason
    local plist = net.get_player_list()
    for i = 2, #plist do
        if net.get_player_info(plist[i], 'ucid') == json.ucid then
            net.kick(plist[i], reason)
            ipaddr = utils.getIP(net.get_player_info(plist[i], 'ipaddr'))
            dcsbot.banList[ipaddr] = json.ucid
            break
        end
    end
end

function dcsbot.ban(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: ban()')
    -- do we run a batch upload?
    if json.batch then
        for _, ban in ipairs(json.batch) do
            single_ban(ban)
        end
    else
        single_ban(json)
    end
end

function dcsbot.unban(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: unban()')
	dcsbot.banList[json.ucid] = nil
end

function dcsbot.lock_player(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: lock_player()')
	dcsbot.locked[json.ucid] = true
end

function dcsbot.unlock_player(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: unlock_player()')
	dcsbot.locked[json.ucid] = nil
end

function dcsbot.lock_server(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: lock_server()')
	dcsbot.server_locked = true
	if json.message then
	    messages = dcsbot.params['mission']['messages']
	    messages['message_server_locked_old'] = messages['message_server_locked']
        messages['message_server_locked'] = json.message
    end
end

function dcsbot.unlock_server(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: unlock_server()')
	dcsbot.server_locked = false
    -- reset the message to default
    messages = dcsbot.params['mission']['messages']
    messages['message_server_locked'] = messages['message_server_locked_old']
end

function dcsbot.makeScreenshot(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: makeScreenshot()')
    net.screenshot_request(json.id)
end

function dcsbot.getScreenshots(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getScreenshots()')
    local msg = {
        command = "getScreenshots",
        screens = net.get_player_info(json.id, 'screens')
    }
    utils.sendBotTable(msg, json.channel)
end

function dcsbot.deleteScreenshot(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: deleteScreenshot()')
    net.screenshot_del(json.id, json.key)
end

function dcsbot.setFog(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: setFog()')
	net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize('dcsbot.setFog(' .. json.visibility .. ',' .. json.thickness .. ',"' .. json.channel .. '")') .. ')')
end

function dcsbot.getFog(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: getFog()')
	net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize('dcsbot.getFog("' .. json.channel .. '")') .. ')')
end

function dcsbot.setFogAnimation(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: setFogAnimation()')
    local animation = '{'
    for i, value in pairs(json.values) do
        animation = animation .. '{' .. value[1] .. ',' .. value[2] .. ',' .. value[3] .. '},'
    end
    animation = animation .. '}'
	net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize('dcsbot.setFogAnimation(' .. animation .. ',"' .. json.channel .. '")') .. ')')
end

function dcsbot.createMenu(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: createMenu()')
	net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize('dcsbot.createMenu(' .. json.playerID .. ',' .. json.groupID .. ',' .. utils.basicSerialize(net.lua2json(json.menu)) .. ')') .. ')')
end

function dcsbot.deleteMenu(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: deleteMenu()')
	net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize('dcsbot.deleteMenu(' .. json.groupID .. ')') .. ')')
end

function dcsbot.endMission(json)
    log.write('DCSServerBot', log.DEBUG, 'Mission: endMission()')
	net.dostring_in('mission', 'a_end_mission(' .. utils.basicSerialize(json.winner or '') .. ',' .. utils.basicSerialize(json.message or '') .. ',' .. (json.time or 0) .. ')')
end
