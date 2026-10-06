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
	dcsbot.sendBotTable(msg)
end

local marker = 0
local function getMarkID()
    marker = marker + 1
    return marker
end

function dcsbot.setMarker(to, id, lat, lon, text, readonly, message)
    env.info('DCSServerBot - setMarker')
    local mark_id = getMarkID()
    local x,y = Terrain.convertLatLonToMeters(lat, lon)
    local point = {
        x = x,
        y = y
    }
    if to == 'all' then
        trigger.action.markToAll(mark_id, text, point, readonly, message)
    elseif to == 'coalition' then
        if id == 'all' then
            trigger.action.markToAll(mark_id, text, point, readonly, message)
        elseif id == 'red' then
            trigger.action.markToCoalition(mark_id, text, point, coalition.side.RED, readonly, message)
        elseif id == 'blue' then
            trigger.action.markToCoalition(mark_id, text, point, coalition.side.BLUE, readonly, message)
        elseif id == 'neutrals' then
            trigger.action.markToCoalition(mark_id, text, point, coalition.side.NEUTRALS, readonly, message)
        end
    elseif to == 'group' then
        local group = Group.getByName(id)
        if group and group:isExist() then
            trigger.action.markToGroup(mark_id, text, point, group:getID(), readonly, message)
        end
    end
    local msg = {
        command = 'setMarker',
        id = mark_id
    }
	dcsbot.sendBotTable(msg)
end

env.info("DCSServerBot - GameMaster: mission.lua loaded.")
