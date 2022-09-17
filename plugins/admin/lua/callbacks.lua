local base   		= _G

local dcsbot	= base.dcsbot
local utils 	= base.require("DCSServerBotUtils")
local config	= base.require("DCSServerBotConfig")

local default_names = { 'Player', 'Spieler', 'Jugador', 'Joueur' }

local function trim(s)
  return (string.gsub(s, "^%s*(.-)%s*$", "%1"))
end

local function locate( table, value )
    for i = 1, #table do
        if table[i] == value then return true end
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
        return false, config.MESSAGE_PLAYER_USERNAME
    end
	if isBanned(ucid) then
        msg.command = 'sendMessage'
        msg.message = 'Banned user ' .. name .. ' (ucid=' .. ucid .. ') rejected.'
    	utils.sendBotTable(msg, config.ADMIN_CHANNEL)
	    return false, string.gsub(config.MESSAGE_BAN, "{}", dcsbot.banList[ucid])
	end
    plist = net.get_player_list()
    num_players = table.getn(plist)
    ipaddr = string.sub(addr, 0, string.find(addr, ':') - 1)
    if num_players > 1 then
        for i = 2, num_players do
            pucid = net.get_player_info(plist[i], 'ucid')
            pipaddr = net.get_player_info(plist[i], 'ipaddr')
            pipaddr = string.sub(pipaddr, 0, string.find(pipaddr, ':') - 1)
            if pucid == ucid and pipaddr ~= ipaddr then
                msg.command = 'sendMessage'
                msg.message = 'User ' .. name .. ' (ucid=' .. ucid .. ') rejected due to account sharing.'
                utils.sendBotTable(msg, config.ADMIN_CHANNEL)
                return false, config.MESSAGE_ACCOUNT_SHARING
            end
        end
    end
end

function admin.onMissionLoadBegin(id)
    log.write('DCSServerBot', log.DEBUG, 'Admin: onMissionLoadBegin()')
	if dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
end

function admin.onPlayerConnect(id)
    log.write('DCSServerBot', log.DEBUG, 'Admin: onPlayerConnect()')
	if id == 1 and dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
end

function admin.onPlayerStart(id)
    log.write('DCSServerBot', log.DEBUG, 'Admin: onPlayerStart()')
	if id == 1 and dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
end

function admin.onSimulationStop()
    log.write('DCSServerBot', log.DEBUG, 'Admin: onSimulationStop()')
    -- re-register the DCS server after a new start (as parameters might have changed)
    dcsbot.registered = false
end

DCS.setUserCallbacks(admin)
