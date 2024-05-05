local base      = _G
local dcsbot    = base.dcsbot
local dcs_srs   = dcs_srs or {}

dcsbot.srs = dcsbot.srs or {}

function dcs_srs.onPlayerTryChangeSlot(playerID, side, slotID)
    log.write('DCSServerBot', log.DEBUG, 'DCS-SRS: onPlayerTryChangeSlot()')
    local name = net.get_player_info(playerID, 'name')
    local srs = dcsbot.srs[name]
    if srs == nil then
        log.write('DCSServerBot', log.DEBUG, 'No player found with name ' .. name .. ' in the SRS table.')
        return
    end
    log.write('DCSServerBot', log.DEBUG, 'Player found in the SRS table, status is ' .. tostring(srs))
    if (side == 1 or side == 2) and srs == false then
        net.send_chat_to("You need to use SRS to play on this server!", playerID)
        return false
    end
end

DCS.setUserCallbacks(dcs_srs)
