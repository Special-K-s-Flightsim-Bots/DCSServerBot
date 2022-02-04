local base		= _G
dcsbot 			= base.dcsbot

--[[
    eventName, the event according to the penalties table
    initiator, the player name to be punished
    target, the victim name (might be nil or -1 for AI)
]]--
function dcsbot.punish(eventName, initiator, target)
    msg = {}
    msg.command = 'punish'
    msg.eventName = eventName
    msg.initiator = initiator
    msg.target = target
    dcsbot.sendBotTable(msg)
end
