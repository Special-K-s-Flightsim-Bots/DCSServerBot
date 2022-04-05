local base		= _G
dcsbot 			= base.dcsbot

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
