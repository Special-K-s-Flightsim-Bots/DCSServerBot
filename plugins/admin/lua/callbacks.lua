local base   		= _G

local dcsbot	= base.dcsbot
local utils 	= base.require("DCSServerBotUtils")
local config	= base.require("DCSServerBotConfig")

function trim(s)
  return (string.gsub(s, "^%s*(.-)%s*$", "%1"))
end

function isBanned(ucid)
	return dcsbot.banList[ucid] ~= nil
end

local admin = admin or {}

function admin.onPlayerTryConnect(addr, name, ucid, playerID)
    log.write('DCSServerBot', log.DEBUG, 'Admin: onPlayerTryConnect()')
	local msg = {}
	-- we don't accept empty player IDs
	if name == nil or trim(name) == '' then
        msg.command = 'sendMessage'
        msg.message = 'User with empty user name (ucid=' .. ucid .. ') rejected.'
    	utils.sendBotTable(msg, config.ADMIN_CHANNEL)
		return false, 'Rejected due to empty username.'
	end
	if isBanned(ucid) then
        msg.command = 'sendMessage'
        msg.message = 'Banned user ' .. name .. ' (ucid=' .. ucid .. ') rejected.'
    	utils.sendBotTable(msg, config.ADMIN_CHANNEL)
	    return false, 'You are banned from this server.'
	end
	return true
end

function admin.onPlayerConnect(id)
    log.write('DCSServerBot', log.DEBUG, 'Admin: onPlayerConnect()')
	if (dcsbot.registered == false) then
		dcsbot.registerDCSServer()
	end
end

function admin.onPlayerStart(id)
    log.write('DCSServerBot', log.DEBUG, 'Admin: onPlayerStart()')
	if (dcsbot.registered == false) then
		dcsbot.registerDCSServer()
	end
end

function admin.onSimulationStop()
    log.write('DCSServerBot', log.DEBUG, 'Admin: onSimulationStop()')
    -- re-register the DCS server after a new start (as parameters might have changed)
    dcsbot.registered = false
end

DCS.setUserCallbacks(admin)
