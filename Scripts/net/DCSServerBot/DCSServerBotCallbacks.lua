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

local JSON = require("JSON")

local dcsbotgui = {}

function dcsbotgui.onMissionLoadBegin()
	dcsbot.SlotsData['coalitions'] = nil
	msg = {}
	msg.command = 'onMissionLoadBegin'
	msg.current_mission = DCS.getMissionName()
	msg.current_map = DCS.getCurrentMission().mission.theatre
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onMissionLoadEnd()
	msg = {}
	msg.command = 'onMissionLoadEnd'
	msg.current_mission = DCS.getMissionName()
  msg.current_map = DCS.getCurrentMission().mission.theatre
	msg.mission_time = 0
	msg.num_players = 1
	if (dcsbot.updateSlots()['slots']['blue'] ~= nil) then
		msg.num_slots_blue = table.getn(dcsbot.updateSlots()['slots']['blue'])
	end
	if (dcsbot.updateSlots()['slots']['red'] ~= nil) then
		msg.num_slots_red = table.getn(dcsbot.updateSlots()['slots']['red'])
	end
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onSimulationFrame()
	-- idea from HypeMan
	if not dcsbotgui.UDPRecvSocket then
		local host, port = dcsbot.config.HOST, dcsbot.config.RECV_PORT
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
			elseif (json.command == 'loadMission') then
				dcsbot.loadMission(json)
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
			end
		end
	until err
end

function dcsbotgui.onGameEvent(eventName,arg1,arg2,arg3,arg4,arg5,arg6,arg7)
	msg = {}
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
	dcsbot.registerDCSServer()
	msg = {}
	msg.command = 'onPlayerConnect'
	msg.id = id
	msg.name = net.get_player_info(id, 'name')
	msg.ucid = net.get_player_info(id, 'ucid')
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onPlayerStart(id)
	dcsbot.registerDCSServer()
	msg = {}
	msg.command = 'onPlayerStart'
	msg.id = id
	msg.ucid = net.get_player_info(id, 'ucid')
	msg.name = net.get_player_info(id, 'name')
	dcsbot.sendBotTable(msg)
end

function dcsbotgui.onPlayerChangeSlot(id)
	dcsbot.registerDCSServer()
	msg = {}
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
	msg = {}
	msg.command = 'onChatMessage'
	msg.message = message
	msg.from_id = net.get_player_info(from, 'id')
	msg.from_name = net.get_player_info(from, 'name')
	dcsbot.sendBotTable(msg, dcsbot.config.CHAT_CHANNEL)
end

function dcsbotgui.onPlayerTryConnect(addr, name, ucid, playerID)
	return not dcsbot.isBanned(ucid)
end

if DCS.isServer() then
	DCS.setUserCallbacks(dcsbotgui)  -- here we set our callbacks
end
