local base  = _G
dcsbot 		= base.dcsbot

function dcsbot.disableUserStats()
	local msg = {
		command = 'disableUserStats'
	}
	dcsbot.sendBotTable(msg, channel)
	env.info('User Statistics disabled.')
end
