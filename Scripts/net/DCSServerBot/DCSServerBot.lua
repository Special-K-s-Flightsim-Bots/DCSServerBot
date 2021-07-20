-- DCSServerBot.lua
-----------------------------------------------------
-- This code can be loaded inside of DCS missions to
-- allow the communication with DCSServerBot.
-----------------------------------------------------

-- load the configuration
dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBotConfig.lua')
loadfile(lfs.writedir() .. 'Config/serverSettings.lua')()

local JSON = loadfile(lfs.currentdir() .. "Scripts\\JSON.lua")()

package.path  = package.path..";.\\LuaSocket\\?.lua;"
package.cpath = package.cpath..";.\\LuaSocket\\?.dll;"
local socket = require("socket")
dcsbot.UDPSendSocket = socket.udp()

function dcsbot.sendBotTable(tbl, channel)
	tbl.server_name = cfg.name
	tbl.channel = channel or "-1"
	local tbl_json_txt = JSON:encode(tbl)
	socket.try(dcsbot.UDPSendSocket:sendto(tbl_json_txt, dcsbot.config.BOT_HOST, dcsbot.config.BOT_PORT))
end

function dcsbot.sendBotMessage(msg, channel)
	messageTable = {}
	messageTable.command = 'sendMessage'
	messageTable.message = msg
	dcsbot.sendBotTable(messageTable, channel)
end
