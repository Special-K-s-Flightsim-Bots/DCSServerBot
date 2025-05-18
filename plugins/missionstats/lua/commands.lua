local base 	= _G
local utils = base.require("DCSServerBotUtils")
local dcsbot= base.dcsbot

function dcsbot.getMissionSituation(json)
    log.write('DCSServerBot', log.DEBUG, 'Missionstats: getMissionSituation()')
    net.dostring_in('mission', 'a_do_script("dcsbot.getMissionSituation(\\"' .. json.channel .. '\\")")')
end

function dcsbot.enableMissionStats(json)
    log.write('DCSServerBot', log.DEBUG, 'Missionstats: enableMissionStats()')
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize('dcsbot.enableMissionStats(' .. utils.basicSerialize(net.lua2json(json.filter)) .. ')') .. ')')
end

function dcsbot.disableMissionStats()
    log.write('DCSServerBot', log.DEBUG, 'Missionstats: disableMissionStats()')
    net.dostring_in('mission', 'a_do_script("dcsbot.disableMissionStats()")')
end
