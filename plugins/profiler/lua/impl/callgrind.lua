--
-- Copyright 2007 Jan Kneschke (jan@kneschke.de)
--           2013 Markus Stenberg (fingon@iki.fi)
--
-- Licensed under the same license as Lua 5.1
--
-- => lua-callgrind.txt is created when the program exits in current directory

-- avoid double loading
if profiler then
    return
else
    profiler = true
end

require("debug")

local TRACEFILENAME = (lfs and lfs.writedir and lfs.writedir() or "./") .. "Logs/callgrind.out"
local callstack = {}
local instr_count = 0
local last_line_instr_count = 0
local running = false

local functions = {}
local methods = {}
local method_id = 1
local call_indent = 0

local full = false      -- profile Lua only

-- Identify internal functions to skip in hooks
local internal_functions = {}

local function trace(class)
    -- print("calling tracer: "..class)
    if class == "count" then
        instr_count = instr_count + 1
    elseif class == "line" then
        -- check if we know this function already
        local f = debug.getinfo(2, "lSf")

        if not f or internal_functions[f.func] then
            last_line_instr_count = instr_count
            return
        end

        if not functions[f.func] then
            functions[f.func] = {
                meta = f,
                lines = {}
            }
        end
        local lines = functions[f.func].lines
        lines[#lines + 1] = ("%d %d"):format(f.currentline, instr_count - last_line_instr_count)
        functions[f.func].last_line = f.currentline

        last_line_instr_count = instr_count
    elseif class == "call" then
        -- add the function info to the stack
        --
        local f = debug.getinfo(2, "lSfn")

        if not f then
            callstack[#callstack + 1] = { tracked = false }
            return
        end

        local tracked = not internal_functions[f.func] and (full or f.what == 'Lua')

        callstack[#callstack + 1] = {
            tracked     = tracked,
            short_src   = f.short_src,
            func        = f.func,
            linedefined = f.linedefined,
            name        = f.name,
            instr_count = instr_count
        }

        if not tracked then return end

        if not functions[f.func] then
            functions[f.func] = {
                meta = f,
                lines = {}
            }
        end

        if not functions[f.func].meta.name then
            functions[f.func].meta.name = f.name
        end

        -- print((" "):rep(call_indent)..">>"..tostring(f.func).." (".. tostring(f.name)..")")
        call_indent = call_indent + 1
    elseif class == "return" or class == "tail return" then
        -- Returns from functions which were active before start() have no
        -- matching call. Errors may also skip return hooks, so search down to
        -- the matching frame and discard any unwound frames above it. A Lua
        -- 5.1 "tail return" has no reliable caller info because that frame has
        -- already been removed from the VM stack, so it consumes our top frame.
        local idx
        if class == "tail return" then
            if #callstack > 0 then idx = #callstack end
        else
            local f = debug.getinfo(2, "f")
            if not f then return end
            for i = #callstack, 1, -1 do
                if callstack[i].func == f.func then
                    idx = i
                    break
                end
            end
        end
        if not idx then return end

        local ret = callstack[idx]
        for i = #callstack, idx, -1 do
            callstack[i] = nil
        end
        if ret.tracked then call_indent = math.max(0, call_indent - 1) end
        if not ret.tracked then return end

        local caller
        for i = #callstack, 1, -1 do
            if callstack[i].tracked then
                caller = callstack[i]
                break
            end
        end
        if not caller or not functions[caller.func] then return end

        local lines = functions[caller.func].lines
        local last_line = functions[caller.func].last_line or caller.linedefined or -1
        lines[#lines + 1] = ("cfl=%s"):format(ret.short_src or "?")
        lines[#lines + 1] = ("cfn=%s"):format(tostring(ret.func))
        lines[#lines + 1] = ("calls=1 %d"):format(ret.linedefined or 0)
        lines[#lines + 1] = ("%d %d"):format(last_line, instr_count - ret.instr_count)
        -- tracefile:write("# --callstack: " .. #callstack .. "\n")
    else
        -- print("class = " .. class)
    end
end

local function start(f)
    if running then return end
    full = f
    callstack = {}
    instr_count = 0
    last_line_instr_count = 0
    functions = {}
    methods = {}
    method_id = 1
    call_indent = 0
    running = true
    -- Callgrind needs instruction and line events in both modes. `full` only
    -- controls whether C boundary calls are included in the call graph.
    debug.sethook(trace, "crl", 1)
end

local function done()
    if not running then return end
    debug.sethook()
    running = false

    local tracefile, err = io.open(TRACEFILENAME, "w")
    if not tracefile then
        log.write('DCSServerBot', log.ERROR,
            "Profiler(callgrind): cannot open '" .. tostring(TRACEFILENAME) .. "': " .. tostring(err))
        return
    end
    tracefile:write("events: Instructions\n")


    -- try to build a reverse mapping of all functions pointers
    -- string.sub() should not just be sub(), but the full name
    --
    -- scan all tables in _G for functions

    local function func2name(m, o, prefix, n, visited)
        local v = visited or {}
        if v[o]
        then
            return
        end
        v[o] = true
        if type(o) == 'function'
        then
            -- remove the package.loaded. prefix from the loaded methods
            local full_name = prefix and prefix .. '.' .. tostring(n) or tostring(n or "_G")
            full_name = full_name:gsub("^package%.loaded%.", "")
            m[o] = { name = full_name, id = method_id }
            method_id = method_id + 1
        end
        if type(o) == 'table'
        then
            local full_name = prefix and prefix .. '.' .. tostring(n) or tostring(n or "_G")
            for n2, o2 in pairs(o)
            do
                func2name(m, o2, full_name, n2, v)
            end
        end
    end

    -- resolve the function pointers
    func2name(methods, _G)

    local funcstring2func = {}
    for func, _ in pairs(functions)
    do
        funcstring2func[tostring(func)] = func
    end

    local function pretty_name(func)
        -- given typical function name (e.g. function:0x...),
        -- try to get a pretty name for it.
        -- alternatives are:
        -- - method table
        -- - metadata in the functions
        local method = methods[func]
        if method
        then
            --print('pretty_name method override', func, method.name)
            return method.name
        end
        local o = functions[func]
        local fname = tostring(func)
        if o and o.meta.name
        then
            local n = '[' .. o.meta.name .. '] ' .. fname
            --print('pretty_name function override', func, n)
            return n
        end
        return fname
    end

    local function pretty_name_for_string(s)
        local func = funcstring2func[s]
        return pretty_name(func)
    end

    for func, o in pairs(functions) do
        local f = o.meta
        local func_name = pretty_name(func)

        tracefile:write("fl=" .. f.short_src .. "\n")
        tracefile:write("fn=" .. func_name .. "\n")

        for i, line in ipairs(o.lines) do
            if line:sub(1, 4) == "cfn=" then
                tracefile:write("cfn=" .. pretty_name_for_string(line:sub(5)) .. "\n")
            else
                tracefile:write(line .. "\n")
            end
        end
        tracefile:write("\n")
    end

    tracefile:close()
end

function start_profiling(channel, f)
    start(f)
    local msg = {
        command = 'onProfilingStart',
        profiler = 'callgrind'
    }
    dcsbot.sendBotTable(msg, channel)
end

function stop_profiling(channel)
    -- safe if called multiple times
    done()
    local msg = {
        command = 'onProfilingStop',
        profiler = 'callgrind'
    }
    dcsbot.sendBotTable(msg, channel)
end

internal_functions[trace] = true
internal_functions[start] = true
internal_functions[done] = true
internal_functions[start_profiling] = true
internal_functions[stop_profiling] = true
