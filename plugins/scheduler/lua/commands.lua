local base = _G

local dcsbot    = base.dcsbot
local utils 	= base.require("DCSServerBotUtils")

function dcsbot.start_server(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: start_server()')
    utils.server_name = nil
    json.result = net.start_server(utils.loadSettingsRaw())
    utils.sendBotTable(json, json.channel)
end

function dcsbot.stop_server(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: stop_server()')
    net.stop_game()
end

function dcsbot.shutdown(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: shutdown()')
	Sim.exitProcess()
end

function handlePassword(json, settings, passwordKey, passwordHashKey)
    local password = json[passwordKey]
    if password then
        if password == '' then
            settings['advanced'][passwordHashKey] = nil
        else
            settings['advanced'][passwordHashKey] = net.hash_password(password)
        end
    end
end

function dcsbot.setCoalitionPassword(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: setCoalitionPassword()')
    local settings = utils.loadSettingsRaw()

    handlePassword(json, settings, 'bluePassword', 'bluePasswordHash')
    handlePassword(json, settings, 'redPassword', 'redPasswordHash')

    utils.saveSettings(settings)
end

function dcsbot.reloadScripts(json)
    log.write('DCSServerBot', log.DEBUG, 'Scheduler: reloadScripts()')
    Sim.reloadUserScripts()
end
