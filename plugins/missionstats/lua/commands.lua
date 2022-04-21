local base 	= _G
local dcsbot= base.dcsbot

function dcsbot.getMissionSituation(json)
    log.write('DCSServerBot', log.DEBUG, 'Missionstats: getMissionSituation()')
    net.dostring_in('mission', 'a_do_script("dcsbot.getMissionSituation(\\"' .. json.channel .. '\\")")')
end

function dcsbot.enableMissionStats()
    net.dostring_in('mission', 'a_do_script("dcsbot.enableMissionStats()")')
end

function dcsbot.disableMissionStats()
    net.dostring_in('mission', 'a_do_script("dcsbot.disableMissionStats()")')
end
