local punishment = punishment or {}

function punishment.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'Punishment: onMissionLoadEnd()')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/DCSServerBot.lua' .. '\\")")')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/punishment/mission.lua' .. '\\")")')
end

DCS.setUserCallbacks(punishment)
