local base		= _G
dcsbot 			= base.dcsbot

function dcsbot.startCampaign(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: startCampaign()')
    local msg = {}
    msg.command = 'startCampaign'
    dcsbot.sendBotTable(msg)
end

function dcsbot.stopCampaign(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: stopCampaign()')
    local msg = {}
    msg.command = 'stopCampaign'
    dcsbot.sendBotTable(msg)
end

function dcsbot.resetCampaign(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: resetCampaign()')
    local msg = {}
    msg.command = 'resetCampaign'
    dcsbot.sendBotTable(msg)
end

function dcsbot.getFlag(flag, channel)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: getFlag()')
    msg = {}
    msg.command = 'getFlag'
    msg.value = trigger.misc.getUserFlag(flag)
	dcsbot.sendBotTable(msg, channel)
end

function dcsbot.getVariable(name, channel)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: getVariable()')
    msg = {}
    msg.command = 'getVariable'
    msg.value = _G[name]
	dcsbot.sendBotTable(msg, channel)
end

function dcsbot.setVariable(name, value)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: setVariable()')
    _G[name] = value
end
