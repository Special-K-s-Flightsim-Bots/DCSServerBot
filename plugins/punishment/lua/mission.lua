local base		= _G
dcsbot 			= base.dcsbot

--[[
    eventName, the event according to the penalties table
    initiator, the player name to be punished
    target, the victim name (might be nil or -1 for AI)
]]--
function dcsbot.punish(eventName, initiator, target)
    local msg = {
        command = 'punish',
        eventName = eventName,
        initiator = initiator,
        target = target
    }
    dcsbot.sendBotTable(msg)
end

function dcsbot.disablePunishments()
	local msg = {
		command = 'disablePunishments'
	}
	dcsbot.sendBotTable(msg, channel)
	env.info('Punishments disabled.')
end
