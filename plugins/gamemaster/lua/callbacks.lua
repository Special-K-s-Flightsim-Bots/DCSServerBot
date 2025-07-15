local base          = _G
local dcsbot        = base.dcsbot
local config	    = base.require("DCSServerBotConfig")
local utils         = base.require("DCSServerBotUtils")
local gamemaster    = gamemaster or {}

dcsbot.userInfo = dcsbot.userInfo or {}

function gamemaster.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: onMissionLoadEnd()')
    utils.loadScript('DCSServerBot.lua')
    utils.loadScript('gamemaster/mission.lua')
end

function gamemaster.onPlayerTryChangeSlot(playerID, side, slotID)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: onPlayerTryChangeSlot()')
    if config.COALITIONS == false or side == 0 then
        return
    end
    local player = net.get_player_info(playerID, 'ucid')
    local coalition = dcsbot.userInfo[player].coalition
    if not coalition then
        if side == 1 then
            s = "red"
        elseif side == 2 then
            s = "blue"
        end
        net.send_chat_to("Use " .. config.CHAT_COMMAND_PREFIX .. "join " .. s .. " to join the " .. s .. " coalition first!", playerID)
        return false
    end
    -- allow GameMaster and DCS Admin in any slot
    if coalition == -1 then
        return
    elseif coalition ~= side then
        net.send_chat_to("You are not a member of this coalition!", playerID)
        return false
    end
end

DCS.setUserCallbacks(gamemaster)
