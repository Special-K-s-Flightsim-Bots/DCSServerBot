-- DCSServerBotCommands.lua
---------------------------------------------------------
-- Credits to the Authors of perun / HypeMan, where I got
-- some ideas or even took / amended some of the code.
-- Wouldn't have been possible or at least not that easy
-- without those, so please check these frameworks out,
-- they might do what you need already and even more than
-- what my little code does here.
---------------------------------------------------------
dcsbot = dcsbot or {}

dcsbot.SERVER_NAME = 'initializing'
dcsbot.registered = false
dcsbot.SlotsData = {}
dcsbot.banList = {}

local JSON = require("JSON")

package.path  = package.path..";.\\LuaSocket\\?.lua;"
package.cpath = package.cpath..";.\\LuaSocket\\?.dll;"
local socket = require("socket")
dcsbot.UDPSendSocket = socket.udp()

function dcsbot.sendBotTable(tbl, channel)
	tbl.server_name = dcsbot.config.SERVER_NAME
	tbl.channel = channel or -1
	local tbl_json_txt = JSON:encode(tbl)
	socket.try(dcsbot.UDPSendSocket:sendto(tbl_json_txt, dcsbot.config.HOST, dcsbot.config.SEND_PORT))
end

function dcsbot.sendBotMessage(msg, channel)
	messageTable = {}
	messageTable.command = 'sendMessage'
	messageTable.message = msg
	dcsbot.sendBotTable(messageTable, channel)
end

function dcsbot.registerDCSServer(json)
  if (dcsbot.registered == false) then
		dcsbot.config.SERVER_NAME = net.get_server_settings().name
		msg = {}
		msg.command = 'registerDCSServer'
		msg.hook_version = dcsbot.config.VERSION
	  msg.host = dcsbot.config.HOST
		msg.port = dcsbot.config.RECV_PORT
		msg.chat_channel = dcsbot.config.CHAT_CHANNEL
		msg.status_channel = dcsbot.config.STATUS_CHANNEL
		msg.admin_channel = dcsbot.config.ADMIN_CHANNEL
		dcsbot.sendBotTable(msg)
		dcsbot.registered = true
	end
end

function dcsbot.sendChatMessage(json)
	message = json.message
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
	msg = {}
	msg.command = 'getRunningMission'
	msg.current_mission = DCS.getMissionName()
  msg.current_map = DCS.getCurrentMission().mission.theatre
	msg.mission_time = DCS.getModelTime()
	msg.num_players = table.getn(net.get_player_list())
	if (dcsbot.updateSlots()['slots']['blue'] ~= nil) then
		msg.num_slots_blue = table.getn(dcsbot.updateSlots()['slots']['blue'])
	end
	if (dcsbot.updateSlots()['slots']['red'] ~= nil) then
		msg.num_slots_red = table.getn(dcsbot.updateSlots()['slots']['red'])
	end
	dcsbot.sendBotTable(msg, json.channel)
end

function dcsbot.getMissionDetails(json)
	msg = {}
	msg.command = 'getMissionDetails'
	msg.current_mission = DCS.getMissionName()
	msg.mission_description = DCS.getMissionDescription()
	dcsbot.sendBotTable(msg, json.channel)
end

function dcsbot.getCurrentPlayers(json)
	msg = {}
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
	msg = net.missionlist_get()
	msg.command = 'listMissions'
	dcsbot.sendBotTable(msg, json.channel)
end

function dcsbot.loadMission(json)
	net.missionlist_run(json.id)
end

function dcsbot.restartMission(json)
	net.load_mission(DCS.getMissionFilename())
end

function dcsbot.addMission(json)
	net.missionlist_append(lfs.writedir() .. 'Missions\\' .. json.path)
	dcsbot.listMissions(json)
end

function dcsbot.deleteMission(json)
	net.missionlist_delete(json.id)
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
