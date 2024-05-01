local base 	= _G
local dcsbot= base.dcsbot

dcsbot.userInfo = dcsbot.userInfo or {}

function dcsbot.enableSRS(json)
    log.write('DCSServerBot', log.DEBUG, 'DCS-SRS: enableSRS()')
    dcsbot.userInfo[json.ucid] = dcsbot.userInfo[json.ucid] or {}
    dcsbot.userInfo[json.ucid].srs = true
end

function dcsbot.disableSRS(json)
    log.write('DCSServerBot', log.DEBUG, 'DCS-SRS: disableSRS()')
    dcsbot.userInfo[json.ucid] = dcsbot.userInfo[json.ucid] or {}
    dcsbot.userInfo[json.ucid].srs = false
end
