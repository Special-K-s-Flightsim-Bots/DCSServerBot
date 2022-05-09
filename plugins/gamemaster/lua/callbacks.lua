local gamemaster = gamemaster or {}

function gamemaster.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: onMissionLoadEnd()')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/DCSServerBot.lua' .. '\\")")')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/gamemaster/mission.lua' .. '\\")")')
end

DCS.setUserCallbacks(gamemaster)
