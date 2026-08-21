-- Based on the original MHF profiler, by Martin Helmut Fieber with small improvements
--
-- Chrome Trace Event profiler for the DCS Lua VM.
--
-- Design notes (see plugin review):
--   * The Chrome Trace viewer (Perfetto / DevTools / speedscope) rebuilds the
--     full call tree from the nested B/E (begin/end) events keyed on (pid,tid,ts).
--     We therefore do NOT emit a `sf`/`stackFrames` table — that machinery was
--     redundant and cost up to ~18 debug.getinfo calls per event (#9).
--   * We do NOT call debug.traceback() in the hook — it formats the entire stack
--     into a string on every event and was only used for a broken depth key (#8).
--   * Memory pairing uses an explicit per-coroutine LIFO stack (push on call,
--     pop on return) instead of the old depth-indexed table (#2).
--   * Heap sampling is opt-in and throttled, off by default, because the CPU
--     goals (which Lua / which C-from-Lua is hot) don't need per-call heap (#10).
--   * Every event carries cat = "lua" | "c" so you can filter/colour the
--     Lua->C boundary spans in the viewer (goal b: which base-game C called
--     from Lua is expensive).

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
local internal_functions = {}
local PID                = 1


local function high_res_clock()
    return math.floor(socket.gettime() * 1e6)
end

-- Chrome Trace writer with buffered append and periodic flush
local Profile = {
    file = (lfs and lfs.writedir and lfs.writedir() or "./") .. "Logs/profile.json",
    fh = nil,
    opened = false,
    event_count = 0,
    last_flush_us = 0,
    flush_interval_us = 2e6,       -- flush every 2 seconds
    max_buffered_events = 2000,    -- flush if many events buffered
    force_gc = false,
    lua_only = true,
    track_memory = false,          -- emit lua_heap counter events (opt-in, #10)
    mem_sample_us = 50000,         -- >= 50 ms between heap samples when enabled
    last_mem_us = 0,
}

function Profile:new(file, full, track_memory)
    local obj = {
        file = file or self.file,
        fh = nil,
        opened = false,
        event_count = 0,
        last_flush_us = 0,
        flush_interval_us = self.flush_interval_us,
        max_buffered_events = self.max_buffered_events,
        force_gc = full,
        lua_only = not full,
        track_memory = track_memory or false,
        mem_sample_us = self.mem_sample_us,
        last_mem_us = 0,
    }
    self.__index = self
    setmetatable(obj, self)
    return obj
end

function Profile:is_force_gc()
    return self.force_gc
end

function Profile:is_lua_only()
    return self.lua_only
end

-- Throttle heap sampling: return true at most once per mem_sample_us.
function Profile:want_memory(now_us)
    if not self.track_memory then return false end
    if (now_us - self.last_mem_us) < self.mem_sample_us then return false end
    self.last_mem_us = now_us
    return true
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
    self.fh:write("]")                          -- close the traceEvents array
    self.fh:write(',"displayTimeUnit":"ms"}')
    self.fh:flush()
    self.fh:close()
    self.fh = nil
    self.opened = false
end

-- Instrumentor with per-coroutine call stacks.  Only calls observed after the
-- hook was installed may produce an end event; returns from the pre-existing
-- Lua stack must be ignored.
local Instrumentator = {
    profile = nil,
    call_stacks = setmetatable({}, { __mode = "k" }),
    accepting_events = false,
}

local function push_frame(self, co, frame)
    local stack = self.call_stacks[co]
    if not stack then stack = {}; self.call_stacks[co] = stack end
    stack[#stack + 1] = frame
end

local function pop_frame(self, co)
    local stack = self.call_stacks[co]
    if not stack or #stack == 0 then return nil end
    local frame = stack[#stack]
    stack[#stack] = nil
    if #stack == 0 then self.call_stacks[co] = nil end
    return frame
end

-- Map coroutine to a stable thread id (tid) for GUIs
local co_tid = setmetatable({}, { __mode = "k" })
local next_tid = 1
local function get_thread_context()
    local co
    if coroutine and coroutine.running then
        co = coroutine.running()
    end
    if not co then
        return false, 1
    end
    local tid = co_tid[co]
    if not tid then
        next_tid = next_tid + 1
        tid = next_tid
        co_tid[co] = tid
    end
    return co, tid
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
        if not self.accepting_events then return end

        local co, tid = get_thread_context()

        -- A return can belong to a function which was already active when the
        -- profiler started.  Pair it only with a call seen by this hook.  Lua
        -- 5.1 reports the synthetic unwind of a tail-calling function as
        -- "tail return", which must consume a frame as well.
        if event == "return" or event == "tail return" then
            local frame = pop_frame(self, co)
            if not frame or not frame.tracked then return end

            local args = {}
            if frame.mem_start then
                local mem_end = current_mem_bytes(self.profile:is_force_gc())
                args = {
                    mem_start = frame.mem_start,
                    mem_end = mem_end,
                    mem_delta = mem_end - frame.mem_start,
                }
            end
            self.profile:write_event(net.lua2json({
                name = frame.name,
                cat  = frame.cat,
                ph   = "E",
                ts   = high_res_clock(),
                pid  = PID,
                tid  = tid,
                args = args,
            }, 2))
            return
        end

        if event ~= "call" then return end

        -- "n" name, "S" source/what, "f" func. We deliberately drop "l"
        -- (currentline) — nothing uses it now, so we don't pay to fetch it.
        local info = debug.getinfo(2, "nSf")
        if not info then
            push_frame(self, co, { tracked = false })
            return
        end
        local func = info.func
        if internal_functions[func] then
            push_frame(self, co, { tracked = false })
            return
        end

        local is_lua = info.what == 'Lua'
        if self.profile:is_lua_only() and not is_lua then
            push_frame(self, co, { tracked = false })
            return
        end

        local ts  = high_res_clock()
        -- cat marks Lua vs base-game C boundaries (goal b: filter C in viewer).
        local cat = is_lua and "lua" or "c"

        local name = func_name_from_info(info)
        local frame = { tracked = true, name = name, cat = cat, tid = tid }
        if is_lua and self.profile.track_memory then
            frame.mem_start = current_mem_bytes(self.profile:is_force_gc())
        end
        push_frame(self, co, frame)

        self.profile:write_event(net.lua2json({
                name = name,
                cat  = cat,
                ph   = "B",                 -- begin
                ts   = ts,
                pid  = PID,
                tid  = tid,
            }, 2))

        -- Heap tracking is opt-in and Lua-only.
        if is_lua and self.profile.track_memory then
            if self.profile:want_memory(ts) then
                self.profile:write_event(net.lua2json({
                    name = "lua_heap",
                    cat  = "memory",
                    ph   = "C",         -- counter
                    ts   = ts,
                    pid  = PID,
                    tid  = tid,
                    args = { memory = frame.mem_start },
                }, 2))
            end
        end
    end
end

function Instrumentator:begin_session(file, full, track_memory)
    if self.profile then
        error(
            string.format("Instrumentator:begin_session('%s') while session '%s' is open.", tostring(file or ""),
                tostring(self.profile.file)), 2)
    end
    self.profile = Profile:new(file, full, track_memory)
    self.profile:open()
    self.call_stacks = setmetatable({}, { __mode = "k" })
    self.accepting_events = true

    -- Use "call" and "return" explicitly. Avoid line hooks to keep overhead low.
    debug.sethook(self:create_hook(), "cr")
end

function Instrumentator:end_session()
    -- Stop accepting before debug.sethook() itself generates a call event.
    self.accepting_events = false
    debug.sethook()
    if self.profile then
        -- The stop command and any longer-lived calls cannot naturally return
        -- through the disabled hook. Close their observed spans in LIFO order.
        local ts = high_res_clock()
        for _, stack in pairs(self.call_stacks) do
            for i = #stack, 1, -1 do
                local frame = stack[i]
                if frame.tracked then
                    self.profile:write_event(net.lua2json({
                        name = frame.name,
                        cat  = frame.cat,
                        ph   = "E",
                        ts   = ts,
                        pid  = PID,
                        tid  = frame.tid,
                    }, 2))
                end
            end
        end
        self.profile:close()
    end
    self.profile = nil
    self.call_stacks = setmetatable({}, { __mode = "k" })
end

-- Utility to mark internal functions from a table
local function collect_function(from, into)
    for _, v in pairs(from) do
        if type(v) == "function" then
            into[v] = true
        end
    end
end

function start_profiling(channel, full, track_memory)
    local default_output = (lfs and lfs.writedir and lfs.writedir() or "./") .. "Logs/profile.json"
    pcall(function() Instrumentator:begin_session(default_output, full, track_memory) end)
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
internal_functions[high_res_clock] = true
internal_functions[get_thread_context] = true
internal_functions[push_frame] = true
internal_functions[pop_frame] = true
internal_functions[func_name_from_info] = true
internal_functions[current_mem_bytes] = true
internal_functions[start_profiling] = true
internal_functions[stop_profiling] = true
internal_functions[net.lua2json] = true
