local base 	= _G
local utils	= base.require("DCSServerBotUtils")
local dcsbot= base.dcsbot

dcsbot.squadronInfo = dcsbot.squadronInfo or {}

-- internal, do not use inside of missions unless you know what you are doing!
function dcsbot.updateUserPoints(json)
    log.write('DCSServerBot', log.DEBUG, 'CreditSystem: updateUserPoints()')
    dcsbot.userInfo[json.ucid].points = tonumber(json.points)

    local plist = net.get_player_list()
    for i = 2, #plist do
        if (net.get_player_info(plist[i], 'ucid') == json.ucid) then
            name = net.get_player_info(plist[i], 'name')
            break
        end
    end
    if name then
        local script = 'dcsbot._setUserPoints(' .. utils.basicSerialize(name) .. ', ' .. json.points .. ')'
        net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
    end
end

function dcsbot.updateSquadronPoints(json)
    log.write('DCSServerBot', log.DEBUG, 'CreditSystem: updateSquadronPoints()')
    dcsbot.squadronInfo[json.squadron].points = tonumber(json.points)

    local script = 'dcsbot._setSquadronPoints(' .. utils.basicSerialize(json.squadron) .. ', ' .. json.points .. ')'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end
