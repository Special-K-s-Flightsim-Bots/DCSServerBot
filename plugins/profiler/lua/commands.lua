local base  = _G
local dcsbot= base.dcsbot
local utils = base.require("DCSServerBotUtils")

function dcsbot.loadProfiler(json)
    log.write('DCSServerBot', log.DEBUG, 'Profiler: loadProfiler(\"' .. json.profiler ..'\")')
    utils.loadScript('profiler/impl/' .. json.profiler .. '.lua')
end

function dcsbot.startProfiling(json)
    log.write('DCSServerBot', log.DEBUG, 'Profiler: startProfiling()')
    net.dostring_in('mission', 'a_do_script("start_profiling(\\"' .. (json.channel or -1) .. '\\", ' .. tostring(json.verbose or false) .. ')")')
end

function dcsbot.stopProfiling(json)
    log.write('DCSServerBot', log.DEBUG, 'Profiler: stopProfiling()')
    net.dostring_in('mission', 'a_do_script("stop_profiling(\\"' .. (json.channel or -1) .. '\\")")')
end
