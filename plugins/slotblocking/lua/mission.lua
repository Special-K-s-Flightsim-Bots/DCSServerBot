local base		= _G
dcsbot 			= base.dcsbot

function dcsbot.addUserPoints(user, points)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: addUserPoints()')
    local msg = {}
    msg.command = 'addUserPoints'
    msg.name = user
    msg.points = points
    dcsbot.sendBotTable(msg)
end
