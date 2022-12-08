local base = _G

local dcsbot    = base.dcsbot
local utils 	= base.require("DCSServerBotUtils")

function dcsbot.start_server(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: start_server()')
    net.start_server(utils.loadSettingsRaw())
end

function dcsbot.stop_server(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: stop_server()')
    net.stop_game()
end

function dcsbot.shutdown(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: shutdown()')
	DCS.exitProcess()
end
