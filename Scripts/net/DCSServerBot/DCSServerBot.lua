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

dcsbot.sendEmbed = dcsbot.sendEmbed or function(title, description, img, fields, footer, channel)
	dcsbot.updateEmbed(nil, title, description, img, fields, footer, channel)
end

dcsbot.updateEmbed = dcsbot.updateEmbed or function (id, title, description, img, fields, footer, channel)
	local msg = {}
	msg.command = 'sendEmbed'
	msg.id = id
	msg.title = title
	msg.description = description
	msg.img = img
	msg.fields = fields
	msg.footer = footer
	dcsbot.sendBotTable(msg, channel)
end

dcsbot.callback = dcsbot.callback or function (msg, channel)
	local newmsg = msg
	newmsg.subcommand = msg.command
	newmsg.command = 'callback'
	dcsbot.sendBotTable(newmsg, channel)
end

dcsbot.startMission = dcsbot.startMission or function (id)
	local msg = {}
	msg.command = 'startMission'
	msg.id = id
	dcsbot.callback(msg)
end

dcsbot.shutdown = dcsbot.shutdown or function ()
	DCS.exitProcess()
end

dcsbot.restartMission = dcsbot.restartMission or function ()
	local msg = {}
	msg.command = 'restartMission'
	dcsbot.callback(msg)
end

dcsbot.disableUserStats = dcsbot.disableUserStats or function ()
	local msg = {}
	msg.command = 'disableUserStats'
	dcsbot.sendBotTable(msg, channel)
	env.info('User Statistics disabled.')
end

do
	if not base.mission_hook then
		-- MISSION HOOK REGISTRATION
		base.mission_hook = true
		local msg = {}
		msg.command = 'registerMissionHook'
		dcsbot.sendBotTable(msg)
		env.info('DCSServerBot - Mission Hook installed.')
	end
end