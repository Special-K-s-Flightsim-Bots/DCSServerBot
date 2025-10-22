local base      = _G
local dcsbot    = base.dcsbot
local utils 	= base.require("DCSServerBotUtils")

local dcs_srs   = dcs_srs or {}
dcsbot.srs = dcsbot.srs or {}

function dcs_srs.onPlayerTryChangeSlot(playerID, side, slotID)
    if not dcsbot.params or not dcsbot.params['srs'] or not dcsbot.params['srs']['enforce_srs'] then
        return
    end
    log.write('DCSServerBot', log.DEBUG, 'DCS-SRS: onPlayerTryChangeSlot()')
    local name = net.get_player_info(playerID, 'name')
    local srs = dcsbot.srs[name]
    if srs == nil then
        log.write('DCSServerBot', log.DEBUG, 'No player found with name ' .. name .. ' in the SRS table.')
        return
    end
    if (side == 1 or side == 2) and srs == false then
        net.send_chat_to(dcsbot.params['srs']['message_no_srs'], playerID)
        return false
    end
end

Sim.setUserCallbacks(dcs_srs)
