local base		= _G
dcsbot 			= base.dcsbot

function dcsbot.startCampaign(json)
    local msg = {}
    msg.command = 'startCampaign'
    dcsbot.sendBotTable(msg)
end

function dcsbot.stopCampaign(json)
    local msg = {}
    msg.command = 'stopCampaign'
    dcsbot.sendBotTable(msg)
end

function dcsbot.resetCampaign(json)
    local msg = {}
    msg.command = 'resetCampaign'
    dcsbot.sendBotTable(msg)
end

function dcsbot.getFlag(flag, channel)
    msg = {}
    msg.command = 'getFlag'
    msg.value = trigger.misc.getUserFlag(flag)
	dcsbot.sendBotTable(msg, channel)
end

function dcsbot.getVariable(name, channel)
    env.info('DCSServerBot - Getting variable ' .. name)
    msg = {}
    msg.command = 'getVariable'
    msg.value = _G[name]
	dcsbot.sendBotTable(msg, channel)
end

function dcsbot.setVariable(name, value)
    env.info('DCSServerBot - Setting variable ' .. name .. ' to value ' .. value)
    _G[name] = value
end

env.info("DCSServerBot - GameMaster: mission.lua loaded.")
