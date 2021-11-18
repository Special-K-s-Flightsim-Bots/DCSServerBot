-- DCSServerBotCallbacks.lua
---------------------------------------------------------
-- Credits to the Authors of perun / HypeMan, where I got
-- some ideas or even took / amended some of the code.
-- Wouldn't have been possible or at least not that easy
-- without those, so please check these frameworks out,
-- they might do what you need already and even more than
-- what my little code does here.
---------------------------------------------------------
net.log('[DCSServerBot] Adding Hook ...')

local base = _G

local JSON      = base.require("JSON")
local Terrain   = base.require('terrain')
local UC        = base.require("utils_common")

local dcsbotgui = {}

function trim(s)
  return (string.gsub(s, "^%s*(.-)%s*$", "%1"))
end

function dcsbotgui.onMissionLoadBegin()
  log.write('DCSServerBot', log.DEBUG, '> onMissionLoadBegin()')
	dcsbot.SlotsData['coalitions'] = nil
	local msg = {}
	msg.command = 'onMissionLoadBegin'
	msg.current_mission = DCS.getMissionName()
	msg.current_map = DCS.getCurrentMission().mission.theatre
	msg.mission_time = 0
	msg.num_players = 0
	if (lotatc_inst ~= nil) then
		msg.lotAtcSettings = lotatc_inst.options
	end
	dcsbot.sendBotTable(msg)
  log.write('DCSServerBot', log.DEBUG, '< onMissionLoadBegin()')
end

function dcsbotgui.onMissionLoadEnd()
  log.write('DCSServerBot', log.DEBUG, '> onMissionLoadEnd()')
	local msg = {}
	msg.command = 'onMissionLoadEnd'
	msg.current_mission = DCS.getMissionName()
  msg.current_map = DCS.getCurrentMission().mission.theatre
	msg.mission_time = 0
	msg.num_players = 1
	msg.start_time = DCS.getCurrentMission().mission.start_time
	msg.date = DCS.getCurrentMission().mission.date
	msg.weather = DCS.getCurrentMission().mission.weather
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
      msg.weather.clouds = {}
      msg.weather.clouds.base = clouds.base
      msg.weather.clouds.preset = preset
    end
  else
    msg.weather.clouds = clouds
  end
  msg.airbases = {}
  for airdromeID, airdrome in pairs(Terrain.GetTerrainConfig("Airdromes")) do
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
      log.write('DCSServerBot', log.DEBUG, '### Hier 3')
      table.insert(msg.airbases, airbase)
    end
  end
	if (dcsbot.updateSlots()['slots']['blue'] ~= nil) then
		msg.num_slots_blue = table.getn(dcsbot.updateSlots()['slots']['blue'])
	end
	if (dcsbot.updateSlots()['slots']['red'] ~= nil) then
		msg.num_slots_red = table.getn(dcsbot.updateSlots()['slots']['red'])
	end
	dcsbot.sendBotTable(msg)
  log.write('DCSServerBot', log.DEBUG, '< onMissionLoadEnd()')
end

function dcsbotgui.onSimulationFrame()
	-- idea from HypeMan
	if not dcsbotgui.UDPRecvSocket then
		local host, port = dcsbot.config.DCS_HOST, dcsbot.config.DCS_PORT
		local ip = socket.dns.toip(host)
		dcsbotgui.UDPRecvSocket = socket.udp()
		dcsbotgui.UDPRecvSocket:setsockname(ip, port)
		dcsbotgui.UDPRecvSocket:settimeout(0.0001)
	end

	local msg, err
	repeat
		msg, err = dcsbotgui.UDPRecvSocket:receive()
		if not err then
			json = JSON:decode(msg)
			--assert(loadstring(json.command .. '(json)'))()
			if (json.command == 'sendChatMessage') then
				dcsbot.sendChatMessage(json)
			elseif (json.command == 'registerDCSServer') then
				dcsbot.registerDCSServer(json)
			elseif (json.command == 'getRunningMission') then
				dcsbot.getRunningMission(json)
			elseif (json.command == 'getMissionDetails') then
				dcsbot.getMissionDetails(json)
			elseif (json.command == 'getCurrentPlayers') then
				dcsbot.getCurrentPlayers(json)
			elseif (json.command == 'listMissions') then
				dcsbot.listMissions(json)
			elseif (json.command == 'startMission') then
				dcsbot.startMission(json)
			elseif (json.command == 'restartMission') then
				dcsbot.restartMission(json)
			elseif (json.command == 'addMission') then
				dcsbot.addMission(json)
			elseif (json.command == 'deleteMission') then
				dcsbot.deleteMission(json)
			elseif (json.command == 'ban') then
				dcsbot.ban(json)
			elseif (json.command == 'unban') then
				dcsbot.unban(json)
			elseif (json.command == 'pause') then
				dcsbot.pause(json)
			elseif (json.command == 'unpause') then
				dcsbot.unpause(json)
			elseif (json.command == 'listMizFiles') then
				dcsbot.listMizFiles(json)
			elseif (json.command == 'shutdown') then
				dcsbot.shutdown(json)
			elseif (json.command == 'getWeatherInfo') then
				dcsbot.getWeatherInfo(json)
			end
		end
	until err
end

function dcsbotgui.onGameEvent(eventName,arg1,arg2,arg3,arg4,arg5,arg6,arg7)
	local msg = {}
	msg.command = 'onGameEvent'
	msg.eventName = eventName
	msg.arg1 = arg1
	msg.arg2 = arg2
	msg.arg3 = arg3
	msg.arg4 = arg4
	msg.arg5 = arg5
	msg.arg6 = arg6
	msg.arg7 = arg7
	if (msg.eventName == 'kill') then
		msg.victimCategory = dcsbot.getCategory(arg5)
	end
	if (msg.eventName == 'kill') then
		msg.killerCategory = dcsbot.getCategory(arg2)
	end
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onPlayerConnect(id)
	if (dcsbot.registered == false) then
		dcsbot.registerDCSServer()
	end
	local msg = {}
	msg.command = 'onPlayerConnect'
	msg.id = id
	msg.name = net.get_player_info(id, 'name')
	msg.ucid = net.get_player_info(id, 'ucid')
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onPlayerStart(id)
	if (dcsbot.registered == false) then
		dcsbot.registerDCSServer()
	end
	local msg = {}
	msg.command = 'onPlayerStart'
	msg.id = id
	msg.ucid = net.get_player_info(id, 'ucid')
	msg.name = net.get_player_info(id, 'name')
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onPlayerStop(id)
  local msg = {}
	msg.command = 'onPlayerStop'
	msg.id = id
	msg.ucid = net.get_player_info(id, 'ucid')
	msg.name = net.get_player_info(id, 'name')
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onPlayerChangeSlot(id)
	local msg = {}
	msg.command = 'onPlayerChangeSlot'
	msg.id = id
	msg.ucid = net.get_player_info(id, 'ucid')
	msg.name = net.get_player_info(id, 'name')
	msg.side = net.get_player_info(id, 'side')
	msg.unit_type, msg.slot, msg.sub_slot = dcsbot.GetMulticrewAllParameters(id)
	msg.unit_name = DCS.getUnitProperty(msg.slot, DCS.UNIT_NAME)
	msg.unit_callsign = DCS.getUnitProperty(msg.slot, DCS.UNIT_CALLSIGN)
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onChatMessage(message, from)
	local msg = {}
	msg.command = 'onChatMessage'
	msg.message = message
	msg.from_id = net.get_player_info(from, 'id')
	msg.from_name = net.get_player_info(from, 'name')
	dcsbot.sendBotTable(msg, dcsbot.config.CHAT_CHANNEL)
end

function dcsbotgui.onPlayerTryConnect(addr, name, ucid, playerID)
	-- we don't accept empty player IDs
	if playerID == nil or trim(playerID) == '' then
		return false
	end
	return not dcsbot.isBanned(ucid)
end

function dcsbotgui.onSimulationStart()
  log.write('DCSServerBot', log.DEBUG, '> onSimulationStart()')
	local msg = {}
	msg.command = 'onSimulationStart'
	dcsbot.sendBotTable(msg)
  log.write('DCSServerBot', log.DEBUG, '< onSimulationStart()')
end

function dcsbotgui.onSimulationStop()
  log.write('DCSServerBot', log.DEBUG, '> onSimulationStop()')
	local msg = {}
	msg.command = 'onSimulationStop'
	dcsbot.sendBotTable(msg)
	-- re-register the DCS server after a new start (as parameters might have changed)
	dcsbot.registered = false
  log.write('DCSServerBot', log.DEBUG, '< onSimulationStop()')
end

function dcsbotgui.onSimulationPause()
	local msg = {}
	msg.command = 'onSimulationPause'
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onSimulationResume()
	local msg = {}
	msg.command = 'onSimulationResume'
	dcsbot.sendBotTable(msg)
end

if DCS.isServer() then
	DCS.setUserCallbacks(dcsbotgui)  -- here we set our callbacks
	if dcsbot.config.SERVER_USER ~= nil then
		net.set_name(dcsbot.config.SERVER_USER)
	end
end
