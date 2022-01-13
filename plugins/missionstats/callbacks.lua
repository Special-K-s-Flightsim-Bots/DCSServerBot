local missionstats = missionstats or {}

function missionstats.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onMissionLoadEnd()')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/DCSServerBot.lua' .. '\\")")')
end

DCS.setUserCallbacks(missionstats)