local creditsystem = creditsystem or {}

function creditsystem.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'CreditSystem: onMissionLoadEnd()')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/DCSServerBot.lua' .. '\\")")')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/creditsystem/mission.lua' .. '\\")")')
end

DCS.setUserCallbacks(creditsystem)
