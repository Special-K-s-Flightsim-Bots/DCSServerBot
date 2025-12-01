--
-- Copyright 2007 Jan Kneschke (jan@kneschke.de)
--           2013 Markus Stenberg (fingon@iki.fi)
--
-- Licensed under the same license as Lua 5.1
--
-- $ lua -lcallgrind <whatever>
-- => lua-callgrind.txt is created when the program exits in current directory

-- avoid double loading
if profiler then
    return
end

profiler = true

local TRACEFILENAME = (lfs and lfs.writedir and lfs.writedir() or "./") .. "Logs/callgrind.out"
local callstack = {}
local instr_count = 0
local last_line_instr_count = 0
local mainfunc = nil

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

        if internal_functions[f.func] then return end

        if not functions[f.func] then
            functions[f.func] = {
                meta = f,
                lines = {}
            }
        end
        local lines = functions[f.func].lines
        lines[#lines + 1] = ("%d %d"):format(f.currentline, instr_count - last_line_instr_count)
        functions[f.func].last_line = f.currentline

        if not mainfunc then mainfunc = f.func end

        last_line_instr_count = instr_count
    elseif class == "call" then
        -- add the function info to the stack
        --
        local f = debug.getinfo(2, "lSfn")

        if internal_functions[f.func] then return end
        if not full and f.what ~= 'Lua' then return end

        callstack[#callstack + 1] = {
            short_src   = f.short_src,
            func        = f.func,
            linedefined = f.linedefined,
            name        = f.name,
            instr_count = instr_count
        }

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
    elseif class == "return" then
        if #callstack > 0 then
            -- pop the function from the stack and
            -- add the instr-count to the its caller
            local ret = table.remove(callstack)

            local f = debug.getinfo(2, "lSfn")

            if internal_functions[f.func] then return end
            if not full and f.what ~= 'Lua' then return end

            -- if lua wants to return from a pcall() after a assert(),
            -- error() or runtime-error we have to cleanup our stack
            if ret.func ~= f.func then
                -- print("handling error()")
                -- the error() is already removed
                -- removed every thing up to pcall()
                while callstack[#callstack].func ~= f.func do
                    table.remove(callstack)

                    call_indent = call_indent - 1
                end
                -- remove the pcall() too
                ret = table.remove(callstack)
                call_indent = call_indent - 1
            end

            local prev

            if #callstack > 0 then
                prev = callstack[#callstack].func
            else
                prev = mainfunc
            end

            local lines = functions[prev].lines
            local last_line = functions[prev].last_line

            call_indent = call_indent - 1

            -- in case the assert below fails, enable this print and the one in the "call" handling
            -- print((" "):rep(call_indent).."<<"..tostring(ret.func).." "..tostring(f.func).. " =? " .. tostring(f.func == ret.func))
            assert(ret.func == f.func)

            lines[#lines + 1] = ("cfl=%s"):format(ret.short_src)
            lines[#lines + 1] = ("cfn=%s"):format(tostring(ret.func))
            lines[#lines + 1] = ("calls=1 %d"):format(ret.linedefined)
            lines[#lines + 1] = ("%d %d"):format(last_line and last_line or -1, instr_count - ret.instr_count)
        end
        -- tracefile:write("# --callstack: " .. #callstack .. "\n")
    else
        -- print("class = " .. class)
    end
end

local function start(f)
    full = f
    if not full then
        debug.sethook(trace, "cr")
    else
        debug.sethook(trace, "crl", 1)
    end
end

local function done()
    debug.sethook()

    local tracefile = io.open(TRACEFILENAME, "w")
    tracefile:write("events: Instructions\n")


    -- try to build a reverse mapping of all functions pointers
    -- string.sub() should not just be sub(), but the full name
    --
    -- scan all tables in _G for functions

    local function func2name(m, o, prefix, n, visited)
        local visited = visited or {}
        if visited[o]
        then
            return
        end
        visited[o] = true
        if type(o) == 'function'
        then
            -- remove the package.loaded. prefix from the loaded methods
            local n = prefix and prefix .. '.' .. n or n
            n = n:gsub("^package\.loaded\.", "")
            m[o] = { name = n, id = method_id }
            method_id = method_id + 1
        end
        if type(o) == 'table'
        then
            local n = prefix and prefix .. '.' .. n or n
            for n2, o2 in pairs(o)
            do
                func2name(m, o2, n, n2, visited)
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
