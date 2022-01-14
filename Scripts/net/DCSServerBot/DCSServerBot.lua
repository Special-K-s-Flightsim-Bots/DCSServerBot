-- DCSServerBot.lua
-----------------------------------------------------
-- This code can be loaded inside of DCS missions to
-- allow the communication with DCSServerBot.
-----------------------------------------------------
local base  	= _G

-- load the configuration
dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBotConfig.lua')
local config = require('DCSServerBotConfig')
loadfile(lfs.writedir() .. 'Config/serverSettings.lua')()

local JSON = loadfile(lfs.currentdir() .. "Scripts\\JSON.lua")()

dcsbot = dcsbot or {}

package.path  = package.path..";.\\LuaSocket\\?.lua;"
package.cpath = package.cpath..";.\\LuaSocket\\?.dll;"
local socket = require("socket")
dcsbot.UDPSendSocket = socket.udp()

function dcsbot.sendBotMessage(msg, channel)
	local messageTable = {}
	messageTable.command = 'sendMessage'
	messageTable.message = msg
	dcsbot.sendBotTable(messageTable, channel)
end

function dcsbot.sendBotTable(tbl, channel)
	tbl.server_name = cfg.name
	tbl.channel = channel or "-1"
	local tbl_json_txt = JSON:encode(tbl)
	socket.try(dcsbot.UDPSendSocket:sendto(tbl_json_txt, config.BOT_HOST, config.BOT_PORT))
end

function dcsbot.sendEmbed(title, description, img, fields, footer, channel)
	dcsbot.updateEmbed(nil, title, description, img, fields, footer, channel)
end

function dcsbot.updateEmbed(id, title, description, img, fields, footer, channel)
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

function dcsbot.callback(msg, channel)
	local newmsg = msg
	newmsg.subcommand = msg.command
	newmsg.command = 'callback'
	dcsbot.sendBotTable(newmsg, channel)
end

function dcsbot.startMission(id)
	local msg = {}
	msg.command = 'startMission'
	msg.id = id
	dcsbot.callback(msg)
end

function dcsbot.shutdown()
	DCS.exitProcess()
end

function dcsbot.restartMission()
	local msg = {}
	msg.command = 'restartMission'
	dcsbot.callback(msg)
end

function dcsbot.disableMissionStats()
	local msg = {}
	msg.command = 'disableMissionStats'
	dcsbot.sendBotTable(msg, channel)
	env.info('Mission Statistics disabled.')
end

function dcsbot.disableUserStats()
	local msg = {}
	msg.command = 'disableUserStats'
	dcsbot.sendBotTable(msg, channel)
	env.info('User Statistics disabled.')
end

do
	-- MISSION HOOK REGISTRATION
	env.info('DCSServerBot - Mission Hook installed.')
	local msg = {}
	msg.command = 'registerMissionHook'
	dcsbot.sendBotTable(msg)
end