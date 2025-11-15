local base  = _G
local dcsbot= base.dcsbot
local utils = base.require("DCSServerBotUtils")

function dcsbot.loadProfiler(json)
    log.write('DCSServerBot', log.DEBUG, 'Profiler: loadProfiler(\"' .. json.profiler ..'\")')
    utils.loadScript('profiler/impl/' .. json.profiler .. '.lua')
end

function dcsbot.startProfiling(json)
    log.write('DCSServerBot', log.DEBUG, 'Profiler: startProfiling()')
    if json.channel then
        net.dostring_in('mission', 'a_do_script("start_profiling(\\"' .. json.channel .. '\\")")')
    else
        net.dostring_in('mission', 'a_do_script("start_profiling()")')
    end
end

function dcsbot.stopProfiling(json)
    log.write('DCSServerBot', log.DEBUG, 'Profiler: stopProfiling()')
    if json.channel then
        net.dostring_in('mission', 'a_do_script("stop_profiling(\\"' .. json.channel .. '\\")")')
    else
        net.dostring_in('mission', 'a_do_script("stop_profiling()")')
    end
end
