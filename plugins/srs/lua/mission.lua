local base  = _G
dcsbot      = base.dcsbot

function dcsbot.send_tts(message, frequency, coalition, volume, point)
    local msg = {
        command = 'onTTSMessage',
        message = message,
        frequency = frequency,
        coalition = coalition,
    }
    if volume then
        msg['volume'] = volume
    end
    if point then
        local lat, lon, alt = coord.LOtoLL(point)
        msg['lat'] = lat
        msg['lon'] = lon
        msg['alt'] = alt
    end
    dcsbot.sendBotTable(msg)
end
