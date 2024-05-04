local base 	= _G
local dcsbot= base.dcsbot

dcsbot.srs = dcsbot.srs or {}

function dcsbot.enableSRS(json)
    log.write('DCSServerBot', log.DEBUG, 'DCS-SRS: enableSRS()')
    dcsbot.srs[json.name] = true
end

function dcsbot.disableSRS(json)
    log.write('DCSServerBot', log.DEBUG, 'DCS-SRS: disableSRS()')
    dcsbot.srs[json.name] = false
end
