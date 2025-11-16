-- Based on the original MHF profiler, by Martin Helmut Fieber with small improvements

-- avoid double loading
if profiler then
    return
end

profiler = true

require("debug")

package.path  = package.path .. ";.\\LuaSocket\\?.lua;"
package.cpath = package.cpath .. ";.\\LuaSocket\\?.dll;"
require("socket")

-- Identify internal functions to skip in hooks
local internal_functions    = {}
local PID                   = 1
local stackFrames           = {}          -- ID → { name, file, line, parent }
local nextStackFrameId      = 1
local frameKeyById          = {}          -- ID → key
local idByKey               = {}          -- key → ID


local function high_res_clock()
    return math.floor(socket.gettime() * 1e6)
end

-- Minimal JSON escaping for strings used in our output
local function json_escape(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\\"):gsub("\"", "\\\""):gsub("\b", "\\b")
        :gsub("\f", "\\f"):gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t")
    return s
end

-- stack_frames_to_json: serialise the global `stackFrames` table with net.lua2json
local function stack_frames_to_json()
    -- Build a plain Lua table that matches the desired JSON structure
    local frames = {}

    for id, frame in pairs(stackFrames) do
        -- `id` must be a string key – Chrome expects `"5"`, `"7"`, …
        local key = tostring(id)

        -- Build the frame object
        local entry = {
            name     = frame.name,
            category = "lua", -- you can change this if you need a different category
        }

        -- Optional parent field
        if frame.parent then
            entry.parent = frame.parent
        end

        -- Store the frame under its string id
        frames[key] = entry
    end

    return net.lua2json(frames, 2)
end

-- Chrome Trace writer with buffered append and periodic flush
local Profile = {
    file = (lfs and lfs.writedir and lfs.writedir() or "./") .. "Logs/profile.json",
    fh = nil,
    opened = false,
    event_count = 0,
    last_flush_us = 0,
    flush_interval_us = 2e6,   -- flush every 2 seconds
    max_buffered_events = 2000, -- flush if many events buffered
    force_gc = false,
    lua_only = true
}

function Profile:new(file, full)
    local obj = {
        file = file or self.file,
        fh = nil,
        opened = false,
        event_count = 0,
        last_flush_us = 0,
        flush_interval_us = self.flush_interval_us,
        max_buffered_events = self.max_buffered_events,
        force_gc = full,
        lua_only = not full
    }
    self.__index = self
    setmetatable(obj, self)
    return obj
end

function Profile:is_force_gc()
    return self.get_force_gc
end

function Profile:is_lua_only()
    return self.lua_only
end

function Profile:open()
    if self.opened then return end
    local dir = self.file:match("^(.*)[/\\][^/\\]+$") or "."
    if lfs and lfs.dir then pcall(function() lfs.mkdir(dir) end) end
    local fh, err = io.open(self.file, "w")
    if not fh then error("Profiler: cannot open file '" .. tostring(self.file) .. "': " .. tostring(err), 2) end
    self.fh = fh
    -- Chrome Trace Event top-level. Declare microseconds to match ts/dur units.
    self.fh:write("{\"traceEvents\":[")
    self.opened = true
    self.event_count = 0
    self.last_flush_us = high_res_clock()
end

function Profile:write_event(ev_json_fragment)
    if not self.opened then return end
    if self.event_count > 0 then
        self.fh:write(",")
    end
    self.fh:write(ev_json_fragment)
    self.event_count = self.event_count + 1
    -- periodic / size-based flush to mitigate data loss
    local t = high_res_clock()
    if self.event_count % self.max_buffered_events == 0 or (t - self.last_flush_us) >= self.flush_interval_us then
        self.fh:flush()
        self.last_flush_us = t
    end
end

function Profile:close()
    if not self.opened then return end
    -- add displayTimeUnit to match microsecond values
    self.fh:write("]") -- close the traceEvents array
    self.fh:write(',"stackFrames":' .. stack_frames_to_json())
    self.fh:write(',"displayTimeUnit":"ms"}')
    self.fh:flush()
    self.fh:close()
    self.fh = nil
    self.opened = false
end

local function add_frame(name, file, line, parent_id)
    local key = name .. "|" .. file .. "|" .. line
    local existing_id = idByKey[key]

    if existing_id then
        -- Frame already exists – we reuse it.  The caller may still
        -- want to set a different parent; you can decide whether to
        -- ignore that or maintain a “most recent parent” policy.
        return existing_id
    end

    local id = tostring(nextStackFrameId)
    nextStackFrameId = nextStackFrameId + 1

    stackFrames[id] = { name = name, file = file, line = line, parent = parent_id, category = "lua" }
    frameKeyById[id] = key
    idByKey[key] = id

    return id
end

local function build_stack_frame_id()
    local trace = {}
    for level = 3, 20 do
        local si = debug.getinfo(level, "nSlfu")
        if not si then break end
        table.insert(trace, {
            name = si.name or "?",
            file = tostring(si.source or ""),
            line = si.linedefined or 0,
            type = "lua"
        })
    end
    local parent_id = nil
    local leaf_id = nil
    for i = 1, #trace do
        local f = trace[i]
        local id = add_frame(f.name, f.file, f.line, parent_id)
        parent_id = id
        leaf_id = id
    end
    return leaf_id
end

-- Instrumentor with per-coroutine stacks
local Instrumentator = {
    profile = nil,
    -- function_stack[co][func] = { [depth] = memory ... }
    function_stack = setmetatable({}, { __mode = "k" }),
    -- base timestamp to stabilize ts values
    t0 = high_res_clock(),
}

-- Map coroutine to a stable thread id (tid) for GUIs
local co_tid = setmetatable({}, { __mode = "k" })
local next_tid = 1
local function get_tid()
    local co
    if coroutine and coroutine.running then
        co = coroutine.running()
    end
    if not co then
        -- differentiate main vs others by using debug.getinfo of a higher stack frame
        return 1
    end
    local tid = co_tid[co]
    if not tid then
        next_tid = next_tid + 1
        tid = next_tid
        co_tid[co] = tid
    end
    return tid
end

local function ensure_tables(self, co, func)
    if not self.function_stack[co] then
        self.function_stack[co] = {}
    end
    if not self.function_stack[co][func] then
        self.function_stack[co][func] = {}
    end
end

local function current_mem_bytes(force_gc)
    if force_gc then collectgarbage("collect") end
    local kb = collectgarbage("count")
    return math.floor(kb * 1024)
end

local function func_name_from_info(info)
    local name = info.name
    if name and #name > 0 then return name end
    local src = info.short_src or info.source or "?"
    local line = info.linedefined or 0
    return string.format("%s:%d", src, line)
end

function Instrumentator:create_hook()
    return function(event, _line)
        local info = debug.getinfo(2, "nSlf")
        if not info then return end
        local func = info.func
        if internal_functions[func] then return end

        if self.profile:is_lua_only() and info.what ~= 'Lua' then
            return
        end

        local co = (coroutine and coroutine.running and (coroutine.running() or false)) or false
        ensure_tables(self, co, func)

        local _, stack_depth = debug.traceback():gsub("\n", "\n")

        if event == "call" then
            local name     = func_name_from_info(info)
            local src      = tostring(info.source or "")
            local tid      = get_tid()
            local ts       = high_res_clock()
            local sf_id    = build_stack_frame_id()

            local args     = {
                source = src,
                linedefined = tonumber(info.linedefined) or -1,
                lastlinedefined = tonumber(info.lastlinedefined) or -1,
                what = tostring(info.what or ""),
                namewhat = tostring(info.namewhat or "")
            }

            local ev = {
                name = name,
                cat  = "function",
                ph   = "B",                 -- phase: begin of function
                ts   = ts,                  -- timestamp (µs)
                pid  = PID,                 -- process id (global)
                tid  = tid,                 -- thread id (aka coroutine id)
                sf   = sf_id,               -- stack frame ID
                args = args                 -- arguments
            }
            self.profile:write_event(net.lua2json(ev, 2))

            -- we only measure memory consumption of lua
            if info.what == 'Lua' then
                local mem    = current_mem_bytes(self.profile:is_force_gc())
                local mem_ev = {
                    name = "lua_heap",
                    cat  = "memory",
                    ph   = "C",                 -- counter
                    ts   = ts,                  -- timestamp (µs)
                    pid  = PID,                 -- process id (global)
                    tid  = tid,                 -- thread id (aka coroutine id)
                    args = {
                        memory = mem            -- heapsize at that point
                    }
                }
                self.profile:write_event(net.lua2json(mem_ev, 2))
                self.function_stack[co][func][stack_depth] = mem
            end

            return

        elseif event == "return" then
            local name  = func_name_from_info(info)
            local tid   = get_tid()
            local ts    = high_res_clock()
            local args  = {}

            if info.what == 'Lua' then
                local mem_end = current_mem_bytes(self.profile:is_force_gc())

                local mem_ev = {
                    name = "lua_heap",
                    cat  = "memory",
                    ph   = "C",                -- counter
                    ts   = ts,                 -- timestamp (µs)
                    pid  = PID,                -- process id (global)
                    tid  = tid,                -- thread id (aka coroutine id)
                    args = {
                        memory = mem_end
                    }
                }
                self.profile:write_event(net.lua2json(mem_ev, 2))
                mem_start = self.function_stack[co][func][depth]

                if mem_start then
                    args = {
                        mem_start = mem_start,
                        mem_end = mem_end,
                        mem_delta = mem_start - mem_end
                    }
                    self.function_stack[co][func][depth] = nil
                    if next(self.function_stack[co][func]) == nil then
                        self.function_stack[co][func] = nil
                    end
                end
            end

            local ev = {
                name = name,
                cat  = "function",
                ph   = "E",                -- phase: end of function
                ts   = ts,                 -- timestamp (µs)
                pid  = PID,                -- process id (global)
                tid  = tid,                -- thread id (aka coroutine id)
                args = args
            }
            self.profile:write_event(net.lua2json(ev, 2))
        end
    end
end

function Instrumentator:begin_session(file, full)
    if self.profile then
        error(
            string.format("Instrumentator:begin_session('%s') while session '%s' is open.", tostring(file or ""),
                tostring(self.profile.file)), 2)
    end
    self.profile = Profile:new(file, full)
    self.profile:open()

    -- Use "call" and "return" explicitly. Avoid line hooks to keep overhead low.
    debug.sethook(self:create_hook(), "cr")
end

function Instrumentator:end_session()
    debug.sethook()
    if self.profile then
        self.profile:close()
    end
    self.profile = nil
end

-- Utility to mark internal functions from a table
local function collect_function(from, into)
    for _, v in pairs(from) do
        if type(v) == "function" then
            into[v] = true
        end
    end
end

function start_profiling(channel, full)
    local default_output = (lfs and lfs.writedir and lfs.writedir() or "./") .. "Logs/profile.json"
    pcall(function() Instrumentator:begin_session(default_output, full) end)
    local msg = {
        command = 'onProfilingStart',
        profiler = 'chrome'
    }
    dcsbot.sendBotTable(msg, channel)
end

function stop_profiling(channel)
    -- safe if called multiple times
    pcall(function() Instrumentator:end_session() end)
    local msg = {
        command = 'onProfilingStop',
        profiler = 'chrome'
    }
    dcsbot.sendBotTable(msg, channel)
end

internal_functions[collect_function] = true
collect_function(Instrumentator, internal_functions)
collect_function(Profile, internal_functions)
internal_functions[json_escape] = true
internal_functions[high_res_clock] = true
internal_functions[get_tid] = true
internal_functions[ensure_tables] = true
internal_functions[func_name_from_info] = true
internal_functions[current_mem_bytes] = true
internal_functions[start_profiling] = true
internal_functions[stop_profiling] = true
internal_functions[net.lua2json] = true