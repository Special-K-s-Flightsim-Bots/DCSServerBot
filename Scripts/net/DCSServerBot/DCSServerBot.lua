-- DCSServerBot.lua
-----------------------------------------------------
-- This code can be loaded inside of DCS missions to
-- allow the communication with DCSServerBot.
-----------------------------------------------------
local base  	= _G

-- Don't double load the lua
if base.dcsbot ~= nil then
	return
end

-- load the configuration
dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBotConfig.lua')
local config = require('DCSServerBotConfig')
loadfile(lfs.writedir() .. 'Config/serverSettings.lua')()
local JSON = loadfile(lfs.currentdir() .. "Scripts\\JSON.lua")()

dcsbot = base.dcsbot or {}
if dcsbot.UDPSendSocket == nil then
	package.path  = package.path..";.\\LuaSocket\\?.lua;"
	package.cpath = package.cpath..";.\\LuaSocket\\?.dll;"
	local socket = require("socket")
	dcsbot.UDPSendSocket = socket.udp()
	dcsbot.UDPSendSocket:setsockname("*", 0)
end

dcsbot.sendBotMessage = dcsbot.sendBotMessage or function (msg, channel)
	local messageTable = {}
	messageTable.command = 'sendMessage'
	messageTable.message = msg
	dcsbot.sendBotTable(messageTable, channel)
end

dcsbot.sendBotTable = dcsbot.sendBotTable or function (tbl, channel)
	tbl.server_name = cfg.name
	tbl.channel = channel or "-1"
	local tbl_json_txt = JSON:encode(tbl)
	socket.try(dcsbot.UDPSendSocket:sendto(tbl_json_txt, config.BOT_HOST, config.BOT_PORT))
end

do
	if not base.mission_hook then
		-- MISSION HOOK REGISTRATION
		base.mission_hook = true
		local msg = {
			command = 'registerMissionHook'
		}
		dcsbot.sendBotTable(msg)
		env.info('DCSServerBot - Mission Hook installed.')
	end
end