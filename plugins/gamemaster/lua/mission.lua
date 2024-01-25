local base		= _G
dcsbot 			= base.dcsbot

function dcsbot.startCampaign(json)
    local msg = {
        command = 'startCampaign'
    }
    dcsbot.sendBotTable(msg)
end

function dcsbot.stopCampaign(json)
    local msg = {
        command = 'stopCampaign'
    }
    dcsbot.sendBotTable(msg)
end

function dcsbot.resetCampaign(json)
    local msg = {
        command = 'resetCampaign'
    }
    dcsbot.sendBotTable(msg)
end

function dcsbot.getFlag(flag, channel)
    env.info('DCSServerBot - Getting flag ' .. flag)
    local msg = {
        command = 'getFlag',
        value = trigger.misc.getUserFlag(flag)
    }
	dcsbot.sendBotTable(msg, channel)
end

function dcsbot.getVariable(name, channel)
    env.info('DCSServerBot - Getting variable ' .. name)
    local msg = {
        command = 'getVariable',
        value = _G[name]
    }
	dcsbot.sendBotTable(msg, channel)
end

function dcsbot.setVariable(name, value)
    env.info('DCSServerBot - Setting variable ' .. name .. ' to value ' .. value)
    _G[name] = value
end

function dcsbot.resetUserCoalitions(discord_roles)
    env.info('DCSServerBot - resetUserCoalitions')
    local msg = {
        command = 'resetUserCoalitions'
    }
    if discord_roles then
        msg.discord_roles = true
    end
	dcsbot.sendBotTable(msg, channel)
end

env.info("DCSServerBot - GameMaster: mission.lua loaded.")
