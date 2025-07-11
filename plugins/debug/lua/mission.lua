local base	= _G
dcsbot 		= base.dcsbot
local JSON 	= loadfile(lfs.currentdir() .. "Scripts\\JSON.lua")()


local event_by_id = {}
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

dcsbot.debugEventHandler = {}
function dcsbot.debugEventHandler:onEvent(event)
	status, err = pcall(onDebugEvent, event)
	if not status then
		env.warning("DCSServerBot - Error during Debug:onEvent(): " .. err)
	end
end

function onDebugEvent(event)
    if not event then
        return
    end
    log.write('EVENT DEBUGGER', log.DEBUG, tostring(event_by_id[event.id]) .. '(' .. JSON:encode(sanitizer(event)) .. ')')
end


if not dcsbot.debug_enabled then
    for k, v in pairs(world.event) do
        event_by_id[v] = k
    end

    world.addEventHandler(dcsbot.debugEventHandler)
    env.info('DCSServerBot - Event-Debugger enabled.')
    dcsbot.debug_enabled = true
end
