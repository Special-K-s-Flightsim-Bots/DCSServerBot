local base 	= _G
local dcsbot= base.dcsbot

dcsbot.userInfo = dcsbot.userInfo or {}

-- internal, do not use inside of missions unless you know what you are doing!
function dcsbot.uploadUserInfo(json)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: uploadUserInfo()')
    local points = tonumber(json.points)
    dcsbot.userInfo[json.ucid] = {}
    dcsbot.userInfo[json.ucid].points = points or 0
    dcsbot.userInfo[json.ucid].roles = json.roles
    if points > 0 then
        net.send_chat_to(net.get_player_info(json.id, 'name') .. ', you currently have ' .. points .. ' credit points!', json.id)
    end
end

-- internal, do not use inside of missions unless you know what you are doing!
function dcsbot.updateUserPoints(json)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: updateUserPoints()')
    dcsbot.userInfo[json.ucid].points = tonumber(json.points)
end
