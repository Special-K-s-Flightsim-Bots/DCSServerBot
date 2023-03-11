local base  = _G
local dcsbot= base.dcsbot
local utils = base.require("DCSServerBotUtils")

dcsbot.banList = {}

function dcsbot.kick(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: kick()')
    if json.id then
        net.kick(json.id, json.reason)
        return
    end
    plist = net.get_player_list()
    for i = 2, table.getn(plist) do
        if ((json.ucid and net.get_player_info(plist[i], 'ucid') == json.ucid) or
                (json.name and net.get_player_info(plist[i], 'name') == json.name)) then
            net.kick(plist[i], json.reason)
            break
        end
    end
end

function dcsbot.ban(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: ban()')
    dcsbot.banList[json.ucid] = json.reason
    dcsbot.kick(json)
end

function dcsbot.unban(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: unban()')
	dcsbot.banList[json.ucid] = nil
end

function dcsbot.force_player_slot(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: force_player_slot()')
    net.force_player_slot(json.playerID, json.sideID or 0, json.slotID or '')
    if json.slotID == 0 and json.reason ~= 'n/a' then
        net.send_chat_to("You have been moved to spectators because of " .. reason, json.playerID)
    else
        net.send_chat_to("You have been moved to spectators by an admin", json.playerID)
    end
end

function dcsbot.setCoalitionPassword(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: setCoalitionPassword()')
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
