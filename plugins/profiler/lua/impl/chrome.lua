-- Based on the original MHF profiler, by Martin Helmut Fieber with small improvements

-- avoid double loading
if profiler then
    return
end

profiler = true

require("debug")

package.path          = package.path .. ";.\\LuaSocket\\?.lua;"
package.cpath         = package.cpath .. ";.\\LuaSocket\\?.dll;"
require ("socket")

local function high_res_clock()
    return math.floor(socket.gettime() * 1e6)
end

-- Identify internal functions to skip in hooks
local internal_functions = {}

-- Minimal JSON escaping for strings used in our output
local function json_escape(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\\"):gsub("\"", "\\\""):gsub("\b", "\\b")
        :gsub("\f", "\\f"):gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t")
    return s
end

-- Chrome Trace writer with buffered append and periodic flush
local Profile = {
    file = (lfs and lfs.writedir and lfs.writedir() or "./") .. "Logs/profile.json",
    fh = nil,
    opened = false,
    event_count = 0,
    last_flush_us = 0,
    flush_interval_us = 2e6, -- flush every 2 seconds
    max_buffered_events = 2000 -- flush if many events buffered
}

function Profile:new(file)
    local obj = {
        file = file or self.file,
        fh = nil,
        opened = false,
        event_count = 0,
        last_flush_us = 0,
        flush_interval_us = self.flush_interval_us,
        max_buffered_events = self.max_buffered_events
    }
    self.__index = self
    setmetatable(obj, self)
    return obj
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
    self.fh:write("],\"displayTimeUnit\":\"ms\"}")
    self.fh:flush()
    self.fh:close()
    self.fh = nil
    self.opened = false
end

local PID        = 1
local MIN_DUR_US = 1     -- never emit 0

-- Build a complete event JSON line for Chrome Trace
local function make_complete_event(name, ts_us, dur_us, tid, args_tbl, cat)
    -- clamp / normalise numeric inputs
    ts_us = math.max(0, math.floor(ts_us or 0))
    dur_us = math.max(0, math.floor(dur_us or 0))
    tid    = math.max(1, math.floor(tid  or 1))

    -- build the event as a plain Lua table
    local event = {
        name = name or "unknown",        -- string
        cat  = cat  or "function",       -- string
        ph   = "X",                      -- phase: completed duration
        ts   = ts_us,                    -- timestamp (µs)
        dur  = dur_us,                   -- duration (µs)
        pid  = PID,                      -- process id (global)
        tid  = tid,                      -- thread id (aka coroutine id)
        args = args_tbl or {},           -- optional arguments
    }

    -- delegate JSON serialization to the helper
    return net.lua2json(event)
end

-- Instrumentor with per-coroutine stacks
local Instrumentator = {
    profile = nil,
    -- function_stack[co][func] = { [depth] = start_ts_us, ... }
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

function Instrumentator:create_hook()
    return function(event, _line)
        local info = debug.getinfo(2, "nSlfu")
        if not info then return end
        local func = info.func
        if internal_functions[func] then return end

--        env.info("### Event Type: " .. event .. ", func: " .. tostring(func) .. ", data: " .. net.lua2json(info))

        local co = (coroutine and coroutine.running and (coroutine.running() or false)) or false
        ensure_tables(self, co, func)

        local _, stack_depth = debug.traceback():gsub("\n", "\n")

        if event == "call" then
            self.function_stack[co][func][stack_depth] = high_res_clock()
            return

        elseif event == "return" then
            if not self.function_stack[co][func] then return end

            local function _write(id, depth)
                local start_ts = self.function_stack[co][func][depth]
                if not start_ts then return end

                local duration = high_res_clock() - start_ts
                local tid      = get_tid()

                local name = info.name or string.format(id, info.short_src, info.linedefined)
                local src = tostring(info.source or "")
                local args = {
                    source = src,
                    linedefined = tonumber(info.linedefined) or -1,
                    lastlinedefined = tonumber(info.lastlinedefined) or -1,
                    what = tostring(info.what or ""),
                    namewhat = tostring(info.namewhat or "")
                }
                local ev = make_complete_event(name, start_ts - self.t0, duration, tid, args, "function")
                self.profile:write_event(ev)
                self.function_stack[co][func][depth] = nil
            end

            _write("unknown", stack_depth)

            if next(self.function_stack[co][func]) == nil then
                self.function_stack[co][func] = nil
            end
        end
    end
end

function Instrumentator:begin_session(file)
    if self.profile then
        error(
        string.format("Instrumentator:begin_session('%s') while session '%s' is open.", tostring(file or ""),
            tostring(self.profile.file)), 2)
    end
    self.profile = Profile:new(file)
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

internal_functions[collect_function] = true
collect_function(Instrumentator, internal_functions)
collect_function(Profile, internal_functions)
internal_functions[json_escape] = true
internal_functions[make_complete_event] = true
internal_functions[high_res_clock] = true
internal_functions[get_tid] = true
internal_functions[ensure_tables] = true

function start_profiling()
    local default_output = (lfs and lfs.writedir and lfs.writedir() or "./") .. "Logs/profile.json"
    pcall(function() Instrumentator:begin_session(default_output) end)
    local msg = {
        command = 'onProfilingStart',
        profiler = 'chrome'
    }
    dcsbot.sendBotTable(msg)
end

function stop_profiling()
    -- safe if called multiple times
    pcall(function() Instrumentator:end_session() end)
    local msg = {
        command = 'onProfilingStop',
        profiler = 'chrome'
    }
    dcsbot.sendBotTable(msg)
end
