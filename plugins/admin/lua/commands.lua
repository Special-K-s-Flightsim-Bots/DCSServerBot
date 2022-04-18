local base = _G

local Terrain   = base.require('terrain')
local UC        = base.require("utils_common")

local dcsbot    = base.dcsbot
local utils 	= base.require("DCSServerBotUtils")
local config	= base.require("DCSServerBotConfig")

dcsbot.registered = false
dcsbot.banList = {}
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

function dcsbot.registerDCSServer(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: registerDCSServer()')
	-- load the servers configuration (SRS, et al)
	local f = io.open(lfs.writedir() .. 'Scripts\\Hooks\\DCS-SRS-AutoConnectGameGUI.lua', 'r')
	if f then
		local content = f:read("*all")
		data = string.gsub(content, 'local SRSAuto = {}', 'SRSAuto = {}')
		data = string.gsub(data, '-- DO NOT EDIT BELOW HERE --(.*)$', '')
		loadstring(data)()
		f:close()
	end
	if (SRSAuto ~= nil) then
		local config_path = string.gsub(SRSAuto.SRS_NUDGE_PATH, 'clients.list.json', 'server.cfg')
		local f = io.open(config_path, 'r')
		if f then
			for line in f:lines() do
				k,v = line:match('^([^=]+)=(.+)$')
			  if k ~= nil then
						if (string.upper(v) == 'FALSE') then
							v = false
						elseif (string.upper(v) == 'TRUE') then
							v = true
						end
				SRSAuto[k] = v
				end
			  end
			f:close()
		end
	end
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
    -- settings
	msg.serverSettings = utils.loadSettingsRaw()
	msg.options = DCS.getUserOptions()
	msg.SRSSettings = SRSAuto
	if (lotatc_inst ~= nil) then
		msg.lotAtcSettings = lotatc_inst.options
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
    -- slots
	if (dcsbot.updateSlots()['slots']['blue'] ~= nil) then
		msg.num_slots_blue = table.getn(dcsbot.updateSlots()['slots']['blue'])
	end
	if (dcsbot.updateSlots()['slots']['red'] ~= nil) then
		msg.num_slots_red = table.getn(dcsbot.updateSlots()['slots']['red'])
	end
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
    if (json ~= nil) then
        utils.sendBotTable(msg, json.channel)
    else
        utils.sendBotTable(msg)
    end
    dcsbot.registered = true
end

function dcsbot.shutdown(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: shutdown()')
	DCS.exitProcess()
end

function dcsbot.kick(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: kick()')
    plist = net.get_player_list()
    for i = 1, table.getn(plist) do
        if ((json.ucid and net.get_player_info(plist[i], 'ucid') == json.ucid) or (json.name and net.get_player_info(plist[i], 'name') == json.name)) then
            net.kick(plist[i], json.reason)
            break
        end
    end
end

function dcsbot.ban(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: ban()')
    dcsbot.banList[json.ucid] = true
    dcsbot.kick(json)
end

function dcsbot.unban(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: unban()')
	dcsbot.banList[json.ucid] = nil
end

function dcsbot.force_player_slot(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: force_player_slot()')
    net.force_player_slot(json.playerID, json.sideID or 0, json.slotID or '')
end

function dcsbot.loadParams(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: loadParams()')
    dcsbot.params = dcsbot.params or {}
    dcsbot.params[json.plugin] = json.params
end

function dcsbot.setCoalitionPassword(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: setCoalitionPassword()')
    settings = utils.loadSettingsRaw()
    if json.bluePassword then
        if json.bluePassword == '' then
            settings['advanced']['bluePasswordHash'] = nil
        else
            settings['advanced']['bluePasswordHash'] = net.hash_password(json.bluePassword)
        end
    end
    if json.redPassword then
        if json.redPassword == '' then
            settings['advanced']['redPasswordHash'] = nil
        else
            settings['advanced']['redPasswordHash'] = net.hash_password(json.redPassword)
        end
    end
    utils.saveSettings(settings)
end
