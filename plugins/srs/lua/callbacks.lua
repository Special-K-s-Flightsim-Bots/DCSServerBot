local base      = _G
local dcsbot    = base.dcsbot
local dcs_srs   = dcs_srs or {}

dcsbot.userInfo = dcsbot.userInfo or {}

function dcs_srs.onPlayerTryChangeSlot(playerID, side, slotID)
    log.write('DCSServerBot', log.DEBUG, 'DCS-SRS: onPlayerTryChangeSlot()')
    local ucid = net.get_player_info(playerID, 'ucid')
    local srs = dcsbot.userInfo[ucid].srs
    if srs == nil then
        return
    end
    if side == 1 or side == 2 and srs == false then
        net.send_chat_to("You need to enable SRS to use any slot on this server!", playerID)
        return false
    end
end

DCS.setUserCallbacks(dcs_srs)
