local base		= _G
local config	= require('DCSServerBotConfig')

dcsbot 			= base.dcsbot

function dcsbot.getFlag(flag, channel)
    msg = {}
    msg.command = 'getFlag'
    msg.value = trigger.misc.getUserFlag(flag)
	dcsbot.sendBotTable(msg, channel)
end
