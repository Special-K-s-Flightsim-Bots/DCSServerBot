local base 	= _G
local dcsbot= base.dcsbot

dcsbot.userInfo = dcsbot.userInfo or {}

function dcsbot.uploadUserInfo(json)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: uploadUserInfo()')
    dcsbot.userInfo[json.ucid] = {}
    dcsbot.userInfo[json.ucid].points = tonumber(json.points) or 0
    dcsbot.userInfo[json.ucid].roles = json.roles
    net.send_chat_to('You currently have ' .. json.points .. ' points!', json.id)
end

function dcsbot.updateUserPoints(json)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: updateUserPoints()')
    dcsbot.userInfo[json.ucid].points = tonumber(json.points)
end

function dcsbot.force_player_slot(json)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: force_player_slot()')
    net.force_player_slot(json.playerID, json.sideID or 0, json.slotID or '')
end
