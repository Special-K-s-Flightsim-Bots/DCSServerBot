local base 	= _G
local dcsbot= base.dcsbot

dcsbot.userInfo = dcsbot.userInfo or {}

function dcsbot.enableSRS(json)
    log.write('DCSServerBot', log.DEBUG, 'DCS-SRS: enableSRS()')
    dcsbot.userInfo[json.name] = dcsbot.userInfo[json.name] or {}
    dcsbot.userInfo[json.name].srs = true
end

function dcsbot.disableSRS(json)
    log.write('DCSServerBot', log.DEBUG, 'DCS-SRS: disableSRS()')
    dcsbot.userInfo[json.name] = dcsbot.userInfo[json.name] or {}
    dcsbot.userInfo[json.name].srs = false
end
