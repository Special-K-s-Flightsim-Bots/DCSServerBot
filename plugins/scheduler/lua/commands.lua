local base = _G

local dcsbot    = base.dcsbot
local utils 	= base.require("DCSServerBotUtils")

function dcsbot.start_server(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: start_server()')
    utils.server_name = nil
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

function dcsbot.setCoalitionPassword(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: setCoalitionPassword()')
    settings = utils.loadSettingsRaw()
    if json.bluePassword then
        if json.bluePassword == '' then
            settings['advanced']['bluePasswordHash'] = nil
        else
            settings['advanced']['bluePasswordHash'] = net.hash_password(json.bluePassword)
        end
    end
    if json.redPassword then
        if json.redPassword == '' then
            settings['advanced']['redPasswordHash'] = nil
        else
            settings['advanced']['redPasswordHash'] = net.hash_password(json.redPassword)
        end
    end
    utils.saveSettings(settings)
end

function dcsbot.reloadScripts(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: reloadScripts()')
    DCS.reloadUserScripts()
end
