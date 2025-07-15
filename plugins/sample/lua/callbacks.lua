local utils 	= base.require("DCSServerBotUtils")
local config	= base.require("DCSServerBotConfig")

local sample = {}

-- Overwrite any Hook unction in here that you want to handle.
-- Make sure that sending a message back to the bot has to be unique!
function sample.onChatMessage(message, from)
    log.write('DCSServerBot', log.DEBUG, 'Sample: onChatMessage()')
	local msg = {}
	msg.command = 'sample'
	msg.message = message
	msg.from_id = net.get_player_info(from, 'id')
	msg.from_name = net.get_player_info(from, 'name')
	utils.sendBotTable(msg, config.CHAT_CHANNEL)
end

DCS.setUserCallbacks(sample)