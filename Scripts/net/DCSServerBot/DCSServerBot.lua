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

local MAX_CHUNK   = 65000          -- safe UDP payload size
local HEADER_SEP  = '|'            -- separator in the header
local HEADER_FMT = '%s'..HEADER_SEP..'%d'..HEADER_SEP..'%d'..HEADER_SEP..'%d'..HEADER_SEP

-- load the configuration
dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBotConfig.lua')
local config = require('DCSServerBotConfig')
loadfile(lfs.writedir() .. 'Config/serverSettings.lua')()

dcsbot = base.dcsbot or {}
if dcsbot.UDPSendSocket == nil then
	package.path  = package.path..";.\\LuaSocket\\?.lua;"
	package.cpath = package.cpath..";.\\LuaSocket\\?.dll;"
	local socket = require("socket")
	dcsbot.UDPSendSocket = socket.udp()
	dcsbot.UDPSendSocket:setsockname("*", 0)
end

dcsbot.sendBotMessage = dcsbot.sendBotMessage or function (msg, channel, raw)
	local messageTable = {}
	messageTable.command = 'sendMessage'
	messageTable.message = msg
	messageTable.raw = raw or false
	dcsbot.sendBotTable(messageTable, channel)
end

dcsbot.sendBotTable = dcsbot.sendBotTable or function (tbl, channel)
	tbl.server_name = cfg.name
    tbl.channel = tostring(channel or "-1")

    local msg = net.lua2json(tbl)

    if #msg <= MAX_CHUNK then
        socket.try(dcsbot.UDPSendSocket:sendto(msg, config.BOT_HOST, config.BOT_PORT))
        return
    end

    local msg_id      = tostring(math.floor(socket.gettime() * 1e6))
    local total_parts = math.ceil(#msg / MAX_CHUNK)

    for part = 1, total_parts do
        local start_idx = (part-1) * MAX_CHUNK + 1
        local end_idx   = math.min(start_idx + MAX_CHUNK - 1, #msg)
        local payload   = msg:sub(start_idx, end_idx)

        local header = string.format(HEADER_FMT, msg_id, config.DCS_PORT, total_parts, part)
        local packet = header .. payload
        socket.try(dcsbot.UDPSendSocket:sendto(packet, config.BOT_HOST, config.BOT_PORT))
    end
end

dcsbot.enableExtension = dcsbot.enableExtension or function (extension, config)
    local msg = {
        command = 'enableExtension',
        extension = extension,
        config = config
    }
	dcsbot.sendBotTable(msg, "-1")
end

dcsbot.disableExtension = dcsbot.disableExtension or function (extension)
    local msg = {
        command = 'disableExtension',
        extension = extension
    }
	dcsbot.sendBotTable(msg, "-1")
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