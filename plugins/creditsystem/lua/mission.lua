local base		= _G
dcsbot 			= base.dcsbot

--[[
    Initialize the MSE points (credits) cache.
    It will be filled by the Hook environment, whenever the credits for a player change.

    You can access it with dcsbot.getUserPoints(playername) inside of your mission.
]]--
local _points = {}
--[[
    Initialize the MSE squadron points (credits) cache.
    It will be filled by the Hook environment, whenever the credits for a squadron changes.

    You can access it with dcsbot.getSquadronPoints(squadron_name) inside of your mission.
    You can access all squadrons with dcsbot.listSquadrons().
]]--
local _squadron_points = {}

function dcsbot.addUserPoints(user, points, reason)
    local msg = {
        command = 'addUserPoints',
        name = user,
        points = points,
        reason = reason or 'Unknown mission achievement'
    }
    dcsbot.sendBotTable(msg)
end

-- Don't call this function, it's for internal use only!
function dcsbot._setUserPoints(user, points)
    _points[user] = points
end

function dcsbot.getUserPoints(user)
    return _points[user]
end

function dcsbot.addSquadronPoints(squadron, points, reason)
    local msg = {
        command = 'addSquadronPoints',
        squadron = squadron,
        points = points,
        reason = reason or 'Unknown mission achievement'
    }
    dcsbot.sendBotTable(msg)
end

-- Don't call this function, it's for internal use only!
function dcsbot._setSquadronPoints(squadron, points)
    _squadron_points[squadron] = points
end

function dcsbot.getSquadronPoints(squadron)
    return _squadron_points[squadron]
end

function dcsbot.listSquadrons()
    local squadrons = {}
    for squadron_name, _ in pairs(_squadron_points) do
        table.insert(squadrons, squadron_name)
    end
    return squadrons
end

env.info("DCSServerBot - CreditSystem: mission.lua loaded.")
