local base 	= _G
local utils = base.require("DCSServerBotUtils")
local JSON  = loadfile(lfs.currentdir() .. "Scripts\\JSON.lua")()
local debug = debug or {}


local function sanitizer(t)
    local visitedTable = {}
    local function sanitizeTable(ta)
        local t = {}
        for k, v in pairs(ta) do
            if type(v) == "number" then
                if v == math.huge or v == -math.huge then
                    v = nil
                end
                if v ~= v then
                    v = "NAN"
                end
            end
            if type(k) == "string" then
            else
                k = tostring(k)
            end
            t[k] = k
            if type(v) == "function" then
                t[k] = "function"
            elseif type(v) == "userdata" then
                t[k] = "userdata"
            elseif type(v) == "table" then
                if not visitedTable[tostring(v)] then
                    visitedTable[tostring(v)] = 1
                    t[k] = sanitizeTable(v)
                else
                    t[k] = "visited " .. tostring(v)
                end
            else
                t[k] = v
            end
        end
        return t
    end

    return sanitizeTable(t)
end

debug.mt = {
    __index = function(t, key)
        if rawget(t,"killed") ~= nil or key == 'onSimulationFrame' or key == 'RPC' then
            return
        end
        if key == 'onMissionLoadEnd' then
            utils.loadScript('DCSServerBot.lua')
            utils.loadScript('debug/mission.lua')
        end
        return function(...)
            js = JSON:encode(sanitizer(arg))
            log.write('EVENT DEBUGGER', log.DEBUG, key .. '(' .. js .. ')')
            visitedTable = {}
        end
    end
}

debug.kill = function(o)
    rawset(o,"killed", true)
    setmetatable(o,{})
    return o
end

debug.new = function(o)
    setmetatable(o, debug.mt)
    return o
end

local h = debug.new({})

DCS.setUserCallbacks(h)
