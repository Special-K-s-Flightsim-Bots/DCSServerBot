local base 	= _G
local dcsbot= base.dcsbot

-- internal, do not use inside of missions unless you know what you are doing!
function dcsbot.updateUserPoints(json)
    log.write('DCSServerBot', log.DEBUG, 'CreditSystem: updateUserPoints()')
    dcsbot.userInfo[json.ucid].points = tonumber(json.points)
end
