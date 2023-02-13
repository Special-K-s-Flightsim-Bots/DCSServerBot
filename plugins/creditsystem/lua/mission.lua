local base		= _G
dcsbot 			= base.dcsbot

--[[
    Initialize the ME points (credits) cache.
    It will be filled by the Hook environment, whenever the credits for a player change.

    You can access it with dcsbot.getUserPoints(playername) inside of your mission.
]]--
local _points = {}

function dcsbot.addUserPoints(user, points)
    local msg = {}
    msg.command = 'addUserPoints'
    msg.name = user
    msg.points = points
    dcsbot.sendBotTable(msg)
end

-- Don't call this function, it's for internal use only!
function dcsbot._setUserPoints(user, points)
    _points[user] = points
end

function dcsbot.getUserPoints(user)
    return _points[user]
end

env.info("DCSServerBot - CreditSystem: mission.lua loaded.")
