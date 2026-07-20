-- Sampling profiler for the DCS Lua VM (statistical, low-overhead).
--
-- Unlike chrome.lua / callgrind.lua (which trace EVERY call and return), this
-- profiler periodically freezes and records the current stack, then tallies.
-- Cost scales with the sample rate, NOT with call volume — so a mission doing
-- millions of cheap calls costs the same to sample as one doing thousands.
--
-- Mechanism:
-- a debug "count" hook that fires every N VM instructions.
-- No OS threads are involved, which is required inside the DCS mission sandbox.
--
-- Output: folded stacks (root;...;leaf <count>) written to
-- Saved Games/<instance>/Logs/profile.folded — load directly in speedscope
-- (https://www.speedscope.app/). Every frame carries a lua_/c_ prefix so you can
-- distinguish your Lua functions from base-game C called across the boundary.
--
-- CAVEAT: instruction-based sampling under-counts long C calls, because
-- time inside a C call advances few Lua instructions. C frames are still visible
-- (which engine calls are on the hot path), but their absolute weight is
-- under-reported.

-- avoid double loading
if profiler then
    return
end

profiler = true

require("debug")

package.path  = package.path .. ";.\\LuaSocket\\?.lua;"
package.cpath = package.cpath .. ";.\\LuaSocket\\?.dll;"
require("socket")

-- Identify internal functions to skip in the stack walk
local internal_functions = {}

local DEFAULT_INTERVAL = 10000     -- VM instructions between samples
local MAX_DEPTH        = 60        -- cap the stack walk


local function high_res_clock()
    return math.floor(socket.gettime() * 1e6)
end

-- The sampler aggregates into these tables; serialised once on stop.
local Sampler = {
    running       = false,
    interval      = DEFAULT_INTERVAL,
    lua_only      = true,
    file          = (lfs and lfs.writedir and lfs.writedir() or "./") .. "Logs/profile.folded",
    -- counts[key] = { name, src, line, cat, self, total }
    counts        = {},
    -- folded["a;b;c"] = <int>
    folded        = {},
    sample_count  = 0,
    start_wall_us = 0,
}

-- Stable per-function identity. Mirrors chrome.lua's func_name_from_info but
-- includes source+line so distinct functions with the same short name don't
-- collide, and prefixes with the category so lua vs c is visible in the viewer.
local function frame_key(info)
    local name = info.name
    local src  = info.short_src or info.source or "?"
    local line = info.linedefined or 0
    local cat  = (info.what == "Lua") and "lua" or "c"
    local label
    if name and #name > 0 then
        label = name
    else
        label = string.format("%s:%d", src, line)
    end
    -- key is unique per (label, src, line); display carries the category prefix
    local key = string.format("%s|%s|%d", label, src, line)
    return key, label, src, line, cat
end

local function record_frame(key, label, src, line, cat, is_leaf)
    local c = Sampler.counts[key]
    if not c then
        c = { name = label, src = src, line = line, cat = cat, self = 0, total = 0 }
        Sampler.counts[key] = c
    end
    c.total = c.total + 1
    if is_leaf then
        c.self = c.self + 1
    end
end

function Sampler:create_hook()
    return function(_event)
        self.sample_count = self.sample_count + 1

        -- Walk the stack from the caller of the hook upward. Build the frame
        -- list leaf-first, then reverse for a root..leaf folded key.
        local frames = {}    -- display labels, leaf-first
        local leaf_key = nil

        for level = 2, MAX_DEPTH + 1 do
            local info = debug.getinfo(level, "nSf")
            if not info then break end
            if not internal_functions[info.func] then
                local is_lua = info.what == "Lua"
                if not (self.lua_only and not is_lua) then
                    local key, label, src, line, cat = frame_key(info)
                    -- leaf = first non-internal frame we encounter
                    record_frame(key, label, src, line, cat, leaf_key == nil)
                    if leaf_key == nil then leaf_key = key end
                    -- folded frame gets a category prefix for viewer filtering
                    frames[#frames + 1] = (cat == "lua" and "lua:" or "c:") .. label
                end
            end
        end

        if #frames > 0 then
            -- reverse to root..leaf and fold
            local ordered = {}
            for i = #frames, 1, -1 do
                ordered[#ordered + 1] = frames[i]
            end
            local stackkey = table.concat(ordered, ";")
            self.folded[stackkey] = (self.folded[stackkey] or 0) + 1
        end
    end
end

function Sampler:start(interval, full)
    if self.running then return end
    self.interval      = (interval and interval > 0) and interval or DEFAULT_INTERVAL
    self.lua_only      = not full
    self.counts        = {}
    self.folded        = {}
    self.sample_count  = 0
    self.start_wall_us = high_res_clock()
    self.running       = true
    -- "" mask + count arg => count hook only (fires every `interval` instrs)
    debug.sethook(self:create_hook(), "", self.interval)
end

function Sampler:write_output()
    local dir = self.file:match("^(.*)[/\\][^/\\]+$") or "."
    if lfs and lfs.dir then pcall(function() lfs.mkdir(dir) end) end
    local fh, err = io.open(self.file, "w")
    if not fh then
        log.write('DCSServerBot', log.ERROR,
            "Profiler(sample): cannot open '" .. tostring(self.file) .. "': " .. tostring(err))
        return
    end
    -- Header comment lines (ignored by speedscope's folded importer, which reads
    -- only "stack count" lines). Kept minimal for compatibility.
    local elapsed_s = (high_res_clock() - self.start_wall_us) / 1e6
    fh:write(string.format("# samples=%d interval=%d elapsed_s=%.1f\n",
        self.sample_count, self.interval, elapsed_s))
    for stackkey, count in pairs(self.folded) do
        fh:write(stackkey .. " " .. count .. "\n")
    end
    fh:flush()
    fh:close()
end

function Sampler:stop()
    if not self.running then return end
    debug.sethook()
    self.running = false
    pcall(function() self:write_output() end)
end

function start_profiling(channel, full, _memory, interval)
    -- signature matches the shared bridge: (channel, full, memory, interval).
    -- `memory` is unused by the sampler (accepted for a uniform bridge call).
    pcall(function() Sampler:start(tonumber(interval), full) end)
    local msg = {
        command = 'onProfilingStart',
        profiler = 'sample'
    }
    dcsbot.sendBotTable(msg, channel)
end

function stop_profiling(channel)
    -- safe if called multiple times
    pcall(function() Sampler:stop() end)
    local msg = {
        command = 'onProfilingStop',
        profiler = 'sample'
    }
    dcsbot.sendBotTable(msg, channel)
end

-- Mark our own helpers so they never appear in the sampled stacks.
internal_functions[high_res_clock] = true
internal_functions[frame_key] = true
internal_functions[record_frame] = true
internal_functions[start_profiling] = true
internal_functions[stop_profiling] = true
internal_functions[net.lua2json] = true
for _, v in pairs(Sampler) do
    if type(v) == "function" then internal_functions[v] = true end
end
