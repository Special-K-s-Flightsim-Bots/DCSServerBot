local base          = _G
local dcsbot        = base.dcsbot
--local config	    = base.require("DCSServerBotConfig")
local utils         = base.require("DCSServerBotUtils")
local gamemaster    = gamemaster or {}

--dcsbot.userInfo = dcsbot.userInfo or {}

function gamemaster.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: onMissionLoadEnd()')
    utils.loadScript('DCSServerBot.lua')
    utils.loadScript('gamemaster/mission.lua')
end

--[[
function gamemaster.onPlayerTryChangeCoalition(playerID, side)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: onPlayerTryChangeCoalition()')
    if config.COALITIONS == false or side == 0 then
        return
    end
    local player = net.get_player_info(playerID, 'ucid')
    local coalition = dcsbot.userInfo[player].coalition
    if not coalition or coalition <= 0 then
        return
    elseif coalition ~= side then
        message = "You are not a member of this coalition!"
        net.send_chat_to(message, playerID)
        return false, message
    end
end
]]--

function gamemaster.onPlayerChangeCoalition(playerID, side)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: onPlayerChangeCoalition()')
    if side == 0 then
        return
    end
    local msg = {
        command = 'onPlayerChangeCoalition',
        id = playerID,
        side = side
    }
    utils.sendBotTable(msg)
end

Sim.setUserCallbacks(gamemaster)
