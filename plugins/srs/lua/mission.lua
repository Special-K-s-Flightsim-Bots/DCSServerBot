local base  = _G
dcsbot      = base.dcsbot

local function send_hound_tts(message, frequency, coalition, volume, point)
    HoundTTS.Transmit(message,
        { freqs = frequency, coalition = coalition, name = "GCI", point = point },
        { provider = "piper", voice = "en_US-lessac-low", volume = volume }
    )
end

local function send_srs_tts(message, frequency, coalition, volume, point)
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

function dcsbot.send_tts(message, frequency, coalition, volume, point)
    if HoundTTS ~= nil then
        send_hound_tts(message, frequency, coalition, volume, point)
    else
        send_srs_tts(message, frequency, coalition, volume, point)
    end
end
