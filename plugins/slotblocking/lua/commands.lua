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

function dcsbot.startCampaign(json)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: startCampaign()')
    local msg = {}
    msg.command = 'startCampaign'
    dcsbot.sendBotTable(msg)
end

function dcsbot.stopCampaign(json)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: stopCampaign()')
    local msg = {}
    msg.command = 'stopCampaign'
    dcsbot.sendBotTable(msg)
end

function dcsbot.resetCampaign(json)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: resetCampaign()')
    local msg = {}
    msg.command = 'resetCampaign'
    dcsbot.sendBotTable(msg)
end

function dcsbot.addUserPoints(user, points)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: addUserPoints()')
    local msg = {}
    msg.command = 'addUserPoints'
    msg.name = user
    msg.points = points
    dcsbot.sendBotTable(msg)
end
