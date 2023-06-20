local base   		= _G

local dcsbot	= base.dcsbot
local utils 	= base.require("DCSServerBotUtils")
local config	= base.require("DCSServerBotConfig")

local default_names = {
    'Player',
    'Joueur',
    'Spieler',
    'Игрок',
    'Jugador',
    '玩家',
    'Hráč',
    '플레이어'
}

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

admin.last_change_slot = {}
admin.num_change_slots = {}


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

function admin.onPlayerConnect(playerID)
    log.write('DCSServerBot', log.DEBUG, 'Admin: onPlayerConnect()')
	admin.last_change_slot[playerID] = nil
	admin.num_change_slots[playerID] = 0
end

function admin.onPlayerTryChangeSlot(playerID, side, slotID)
    log.write('DCSServerBot', log.DEBUG, 'Admin: onPlayerTryChangeSlot()')
    -- ignore slot requests that have been done when the player was kicked already
    if admin.num_change_slots[playerID] == -1 then
        return false
    end
	if admin.last_change_slot[playerID] and admin.last_change_slot[playerID] > (os.clock() - 2) then
		admin.num_change_slots[playerID] = admin.num_change_slots[playerID] + 1
		if admin.num_change_slots[playerID] > 5 then
            admin.num_change_slots[playerID] = -1
			net.kick(playerID, config.MESSAGE_SLOT_SPAMMING)
			return false
        end
	else
		admin.last_change_slot[playerID] = os.clock()
    	admin.num_change_slots[playerID] = 0
	end
end

DCS.setUserCallbacks(admin)
