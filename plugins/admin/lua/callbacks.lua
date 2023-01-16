local base   		= _G

local dcsbot	= base.dcsbot
local utils 	= base.require("DCSServerBotUtils")
local config	= base.require("DCSServerBotConfig")

local default_names = { 'Player', 'Spieler', 'Jugador', 'Joueur', 'Игрок' }

local function locate(table, value)
    for i = 1, #table do
        if table[i]:lower() == value:lower() then return true end
    end
    return false
end

local function isBanned(ucid)
	return dcsbot.banList[ucid] ~= nil
end

local admin = admin or {}

function admin.onPlayerTryConnect(addr, name, ucid, playerID)
    log.write('DCSServerBot', log.DEBUG, 'Admin: onPlayerTryConnect()')
	local msg = {}
    if locate(default_names, name) then
        return false, config.MESSAGE_PLAYER_DEFAULT_USERNAME
    end
    name2 = name:gsub("[\r\n%z]", "")
    if name ~= name2 then
        return false, config.MESSAGE_PLAYER_USERNAME
    end
	if isBanned(ucid) then
        msg.command = 'sendMessage'
        msg.message = 'Banned user ' .. name .. ' (ucid=' .. ucid .. ') rejected.'
    	utils.sendBotTable(msg, config.ADMIN_CHANNEL)
	    return false, string.gsub(config.MESSAGE_BAN, "{}", dcsbot.banList[ucid])
	end
end

DCS.setUserCallbacks(admin)
