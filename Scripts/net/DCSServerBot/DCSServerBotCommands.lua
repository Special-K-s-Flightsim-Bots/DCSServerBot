-- DCSServerBotCommands.lua
---------------------------------------------------------
-- Credits to the Authors of perun / HypeMan, where I got
-- some ideas or even took / amended some of the code.
-- Wouldn't have been possible or at least not that easy
-- without those, so please check these frameworks out,
-- they might do what you need already and even more than
-- what my little code does here.
---------------------------------------------------------
local base = _G

local Tools     = base.require("tools")
local TableUtils= base.require("TableUtils")
local Terrain   = base.require('terrain')
local U         = base.require("me_utilities")
local UC        = base.require("utils_common")

dcsbot = dcsbot or {}

dcsbot.VERSION = '1.2'
dcsbot.registered = false
dcsbot.SlotsData = {}
dcsbot.banList = {}

local JSON = loadfile(lfs.currentdir() .. "Scripts\\JSON.lua")()
loadfile(lfs.writedir() .. 'Config\\serverSettings.lua')()

package.path  = package.path..";.\\LuaSocket\\?.lua;"
package.cpath = package.cpath..";.\\LuaSocket\\?.dll;"
local socket = require("socket")
dcsbot.UDPSendSocket = socket.udp()

-- load server settings
local defaultSettingsServer = net.get_default_server_settings()

local function loadSettingsRaw()
    local tbl = Tools.safeDoFile(lfs.writedir() .. "Config/serverSettings.lua", false)
    if (tbl and tbl.cfg) then
        return TableUtils.mergeTables(defaultSettingsServer, tbl.cfg)
    else
        return defaultSettingsServer
    end
end

function mergeGuiSettings(new_settings)
    local settings = loadSettingsRaw()
    for k, v in pairs(new_settings) do
        settings[k] = v
    end
    return settings
end

function saveSettings(settings)
    mergedSettings = mergeGuiSettings(settings)
    U.saveInFile(mergedSettings, "cfg", lfs.writedir() .. "Config/serverSettings.lua")
    return true
end

function dcsbot.sendBotTable(tbl, channel)
	tbl.server_name = cfg.name
	tbl.channel = channel or "-1"
	local tbl_json_txt = JSON:encode(tbl)
	socket.try(dcsbot.UDPSendSocket:sendto(tbl_json_txt, dcsbot.config.BOT_HOST, dcsbot.config.BOT_PORT))
end

function dcsbot.sendBotMessage(msg, channel)
	local messageTable = {}
	messageTable.command = 'sendMessage'
	messageTable.message = msg
	dcsbot.sendBotTable(messageTable, channel)
end

function dcsbot.registerDCSServer(json)
  log.write('DCSServerBot', log.DEBUG, '> registerDCSServer()')
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
	msg.hook_version = dcsbot.VERSION
	msg.dcs_version = Export.LoGetVersionInfo().ProductVersion[1] .. '.' .. Export.LoGetVersionInfo().ProductVersion[2] .. '.' .. Export.LoGetVersionInfo().ProductVersion[3] .. '.' .. Export.LoGetVersionInfo().ProductVersion[4]
  msg.host = dcsbot.config.DCS_HOST
	msg.port = dcsbot.config.DCS_PORT
	msg.chat_channel = dcsbot.config.CHAT_CHANNEL
	msg.status_channel = dcsbot.config.STATUS_CHANNEL
	msg.admin_channel = dcsbot.config.ADMIN_CHANNEL
	-- backwards compatibility
	if (dcsbot.config.STATISTICS ~= nil) then
		msg.statistics = dcsbot.config.STATISTICS
	else
		msg.statistics = true
	end
	msg.serverSettings = loadSettingsRaw()
	msg.options = DCS.getUserOptions()
	msg.SRSSettings = SRSAuto
	if (lotatc_inst ~= nil) then
		msg.lotAtcSettings = lotatc_inst.options
	end
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
	if (json ~= nil) then
		dcsbot.sendBotTable(msg, json.channel)
	else
		dcsbot.sendBotTable(msg)
	end
	dcsbot.registered = true
  log.write('DCSServerBot', log.DEBUG, '< registerDCSServer()')
end

function dcsbot.sendChatMessage(json)
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

-- from perun
function dcsbot.updateSlots()
	if dcsbot.SlotsData['coalitions'] == nil then
		dcsbot.SlotsData['coalitions']=DCS.getAvailableCoalitions()
		dcsbot.SlotsData['slots']={}

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

-- from perun (slightly changed)
function dcsbot.GetMulticrewAllParameters(PlayerId)
	-- Gets all multicrew parameters
	local _result = ""
	local _master_type= "?"
	local _master_slot = nil
	local _sub_slot = nil

	local _player_slot = net.get_player_info(PlayerId, 'slot')

	if _player_slot and _player_slot ~= '' then
		if not(string.find(_player_slot, 'red') or string.find(_player_slot, 'blue')) then
			-- Player took model
			_master_slot = _player_slot
			_sub_slot = 0

			if (not tonumber(_player_slot)) then
				-- If this is multiseat slot parse master slot and look for seat number
				_t_start, _t_end = string.find(_player_slot, '_%d+')

				if _t_start then
					-- This is co-player
					_master_slot = string.sub(_player_slot, 0 , _t_start -1 )
					_sub_slot = string.sub(_player_slot, _t_start + 1, _t_end )
				end
			end
			_master_type = DCS.getUnitType(_master_slot)

		else
			-- Deal with the special slots addded by Combined Arms and Spectators
			if string.find(_player_slot, 'artillery_commander') then
				_master_type = "artillery_commander"
			elseif string.find(_player_slot, 'instructor') then
				_master_type = "instructor"
			elseif string.find(_player_slot, 'forward_observer') then
				_master_type = "forward_observer"
			elseif string.find(_player_slot, 'observer') then
				_master_type = "observer"
			end
			_master_slot = -1
			_sub_slot = 0
		end
	else
		_master_slot = -1
		_sub_slot = -1
	end
	return _master_type,_master_slot,_sub_slot
end

function dcsbot.getRunningMission(json)
	local msg = {}
	msg.command = 'getRunningMission'
	msg.current_mission = DCS.getMissionName()
  msg.current_map = DCS.getCurrentMission().mission.theatre
	msg.mission_time = DCS.getModelTime()
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
	dcsbot.sendBotTable(msg, json.channel)
end

function dcsbot.getMissionDetails(json)
	local msg = {}
	msg.command = 'getMissionDetails'
	msg.current_mission = DCS.getMissionName()
	msg.mission_description = DCS.getMissionDescription()
	dcsbot.sendBotTable(msg, json.channel)
end

function dcsbot.getCurrentPlayers(json)
	local msg = {}
	msg.command = 'getCurrentPlayers'
	plist = net.get_player_list()
	if (table.getn(plist) > 0) then
		msg.players = {}
		for i = 1, table.getn(plist) do
			msg.players[i] = net.get_player_info(plist[i])
			msg.players[i].unit_type, msg.players[i].slot, msg.players[i].sub_slot = dcsbot.GetMulticrewAllParameters(plist[i])
			msg.players[i].unit_name = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_NAME)
			msg.players[i].unit_callsign = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_CALLSIGN)
		end
		dcsbot.sendBotTable(msg, json.channel)
	end
end

function dcsbot.listMissions(json)
	local msg = net.missionlist_get()
	msg.command = 'listMissions'
	dcsbot.sendBotTable(msg, json.channel)
end

function dcsbot.startMission(json)
	local result = net.missionlist_run(json.id)
	local mission_list = net.missionlist_get()
	saveSettings({
			missionList=mission_list["missionList"],
			listStartIndex=mission_list["listStartIndex"]
	})
end

function dcsbot.shutdown(json)
	DCS.exitProcess()
end

function dcsbot.restartMission(json)
	net.load_mission(DCS.getMissionFilename())
end

function dcsbot.addMission(json)
	net.missionlist_append(lfs.writedir() .. 'Missions\\' .. json.path)
	local current_missions = net.missionlist_get()
	result = saveSettings({missionList = current_missions["missionList"]})
	dcsbot.listMissions(json)
end

function dcsbot.deleteMission(json)
	net.missionlist_delete(json.id)
	local current_missions = net.missionlist_get()
	result = saveSettings({missionList = current_missions["missionList"]})
	dcsbot.listMissions(json)
end

function dcsbot.ban(json)
	dcsbot.banList[json.ucid] = true
end

function dcsbot.unban(json)
	dcsbot.banList[json.ucid] = nil
end

function dcsbot.isBanned(ucid)
	return dcsbot.banList[ucid] ~= nil
end

function dcsbot.pause(json)
	DCS.setPause(true)
end

function dcsbot.unpause(json)
	DCS.setPause(false)
end

function dcsbot.getCategory(id)
	-- from perun
  -- Helper function returns object category basing on https://pastebin.com/GUAXrd2U
  local _killed_target_category = "Other"

	-- Sometimes we get empty object id (seems like DCS API bug)
	if id ~= nil and id ~= "" then
		_killed_target_category = DCS.getUnitTypeAttribute(id, "category")

		-- Below, simple hack to get the propper category when DCS API is not returning correct value
		if _killed_target_category == nil then
			local _killed_target_cat_check_ship = DCS.getUnitTypeAttribute(id, "DeckLevel")
			local _killed_target_cat_check_plane = DCS.getUnitTypeAttribute(id, "WingSpan")
			if _killed_target_cat_check_ship ~= nil and _killed_target_cat_check_plane == nil then
				_killed_target_category = "Ships"
			elseif _killed_target_cat_check_ship == nil and _killed_target_cat_check_plane ~= nil then
				_killed_target_category = "Planes"
			else
				_killed_target_category = "Helicopters"
			end
		end
	end

  return _killed_target_category
end

function dcsbot.listMizFiles(json)
	local msg = {}
	msg.command = 'listMizFiles'
	msg.missions = {}
	for file in lfs.dir(lfs.writedir() .. 'Missions') do
		if ((lfs.attributes(file, 'mode') ~= 'directory') and (file:sub(-4) == '.miz')) then
			table.insert(msg.missions, file)
		end
	end
	dcsbot.sendBotTable(msg, json.channel)
end

function dcsbot.getWeatherInfo(json)
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
  msg.turbulence = UC.composeTurbulenceString(weather)
  msg.wind = UC.composeWindString(weather, position)
  dcsbot.sendBotTable(msg, json.channel)
end
