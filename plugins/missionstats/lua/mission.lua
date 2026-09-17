local base	    = _G
local Terrain   = base.require('terrain')

dcsbot 		    = base.dcsbot

local GROUP_CATEGORY = {
	[Group.Category.AIRPLANE] = 'Airplanes',
	[Group.Category.HELICOPTER] = 'Helicopters',
	[Group.Category.GROUND] = 'Ground Units',
	[Group.Category.SHIP] = 'Ships'
}

-- --------------------------------------------------------------
--  Air‑base / runway definitions
-- --------------------------------------------------------------
local MISSING_RUNWAYS = {
    ["Tbilisi-Lochini"] = {
        course   = -2.181661564992912,
        name     = "13L",
        position = { y = 479.7552, x = -315401, z = 896638 },
        length   = 2463.16,
        width    = 54
    },

    ["Bilbeis Air Base"] = {
        course   = -2.967,
        name     = "17R",
        position = { y = 28.04, x = 39326, z = 34977 },
        length   = 1524,
        width    = 42
    }
}

dcsbot.mission_stats_enabled = false
dcsbot.eventHandler = dcsbot.eventHandler or {}

local event_by_id = {}
--local marker_num = 1

local function get_distance(point1, point2)
    local y1, y2

    if point1.z ~= nil then y1 = point1.z else y1 = point1.y end
    if point2.z ~= nil then y2 = point2.z else y2 = point2.y end

    local dx = point2.x - point1.x
    local dy = y2 - y1

    return math.sqrt(dx * dx + dy * dy)
end

local function is_on_runway(runway, pos, velocity)
    -- ignore rubber banding
    if get_distance(runway.position, pos) > 3500 then
        return true
    end

    local dx = pos.x - runway.position.x
    local dz = pos.z - runway.position.z

    -- Convert DCS runway.course to a "heading" used for x/z rotation
    local heading = -runway.course

    local function is_inside_runway(px, pz)
        local proj    = px * math.cos(heading) + pz * math.sin(heading)
        local lateral = -px * math.sin(heading) + pz * math.cos(heading)

        local length_threshold = runway.length * 1.25 / 2.0 -- add 25% to the runway length as threshold
        local width_threshold = runway.width * 1.5 / 2.0  -- add 50% to the runway width as threshold
        return math.abs(proj) <= length_threshold and math.abs(lateral) <= width_threshold
    end

    -- direct hit: aircraft is on the runway rectangle
    if is_inside_runway(dx, dz) then
        return true
    end

    -- If the event is received late and the aircraft is already airborne,
    -- project the position backwards using the velocity vector to estimate
    -- where it crossed the runway plane.
    if velocity ~= nil and pos.y ~= nil and runway.position.y ~= nil then
        local altitude = pos.y - runway.position.y
        local vy = velocity.y or 0

        -- only project if it is clearly airborne and climbing
        if altitude > 20 and vy > 0 then
            local t = altitude / vy
            dx = dx - (velocity.x or 0) * t
            dz = dz - (velocity.z or 0) * t

            return is_inside_runway(dx, dz)
        end
    end

    return false
end

-- Detect whether a velocity vector is a vertical or normal take‑off.
-- velocity   : table {x = …, y = …, z = …}  (m/s)
-- threshold  : horizontal‑speed threshold in m/s (default 15)
-- returns    : true  if vertical, false if normal
local function is_vertical_takeoff(velocity, threshold)
    threshold = threshold or 15

    -- horizontal speed (ground‑plane component)
    local vh = math.sqrt(velocity.x * velocity.x + velocity.z * velocity.z)

    -- also check the ratio (vertical / horizontal)
    if vh == 0 then
        -- no horizontal motion at all – definitely vertical
        return true
    end

    local ratio = velocity.y / vh

    return vh < threshold or ratio > 2
end

----------------------------------------------------------------
-- AAR fuel tracking
--
-- Problem: in MULTIPLAYER DCS never fires S_EVENT_REFUELING_START for the
-- receiver (long-standing ED bug, forum topics 297833 + 341025). Only
-- S_EVENT_REFUELING_STOP (id 14) reaches the server, and its initiator is the
-- unit that RECEIVED the fuel. So the "start" half has to be reconstructed:
-- poll getFuel() and remember the sample from just before the fuel starts rising.
--
-- Credits to [.ID] EagleEye who built the original aar.lua script my code is based on.
----------------------------------------------------------------

local AAR = {
    INTERVAL  = 2,       -- poll interval
    THRESHOLD = 0.001,   -- min getFuel() rise that counts as a transfer
    GIVE_UP   = 4,       -- ticks without a further rise -> drop unconfirmed baseline
    timer_id  = nil,
    tasks     = {},      -- [unit_id] = { unit, fuel_prev, base, t_base, ticks, confirmed }
    real      = {}       -- [unit_id] = true while DCS itself reported the START
}

-- --------------------------------------------------------------
-- 1) one sample per player per tick
-- --------------------------------------------------------------
local function aar_sample(uid, unit)
    local t = AAR.tasks[uid]
    local fuel = unit:getFuel() or 0

    if not t then
        -- first sight: we only need the previous value, nothing else
        AAR.tasks[uid] = { unit = unit, fuel_prev = fuel }
        return
    end

    local rose = fuel > (t.fuel_prev or fuel) + AAR.THRESHOLD

    if t.base then
        if fuel < t.base - AAR.THRESHOLD then
            -- fell below the baseline => that was engine burn, never a transfer
            t.base, t.confirmed, t.ticks = nil, false, 0
        elseif rose then
            t.confirmed = true                      -- rise continues => real transfer
        elseif not t.confirmed then
            t.ticks = (t.ticks or 0) + 1
            if t.ticks >= AAR.GIVE_UP then
                t.base, t.ticks = nil, 0            -- one-off jump (spawn/script), drop
            end
        end
    elseif rose then
        -- >>> THE REPLACEMENT FOR THE MISSING START EVENT <<<
        -- baseline = the sample taken BEFORE the rise
        t.base   = t.fuel_prev
        t.t_base = timer.getTime()
        t.ticks  = 0
        AAR.emitStart(unit, t)   -- the single emit site: first positive rise
    end

    t.fuel_prev = fuel
end

local function aar_tick()
    for _, side in ipairs({coalition.side.BLUE, coalition.side.RED}) do
        for _, group in ipairs(coalition.getGroups(side, Group.Category.AIRPLANE) or {}) do
            for _, unit in ipairs(group:getUnits()) do
                if unit and unit:isExist() then
                    local uid = unit:getID()
                    if unit:inAir() then
                        aar_sample(uid, unit)
                    else
                        AAR.tasks[uid] = nil      -- on the ground (airbase refuel!) => no session
                        AAR.real[uid]  = nil
                    end
                end
            end
        end
    end
    -- MP quits do not always raise DEAD/CRASH/PLAYER_LEAVE_UNIT: the unit just
    -- disappears, so garbage-collect on the tick as well.
    for uid, t in pairs(AAR.tasks) do
        if not t.unit or not t.unit:isExist() then
            AAR.tasks[uid] = nil
            AAR.real[uid]  = nil
        end
    end
    return timer.getTime() + AAR.INTERVAL
end

-- --------------------------------------------------------------
-- 2) the closer: S_EVENT_REFUELING_STOP builds the comment
-- --------------------------------------------------------------
local function aar_onStop(unit)
    local uid = unit:getID()
    local t   = AAR.tasks[uid]
    AAR.tasks[uid] = nil                  -- next boom/basket contact re-baselines
    AAR.real[uid]  = nil
    if not t or not t.base then
        return nil                        -- nothing known -> comment stays empty
    end

    local gained = (unit:getFuel() or 0) - t.base
    if gained <= AAR.THRESHOLD then
        return nil                        -- touched the basket, took nothing
    end

    local desc = unit:getDesc()
    local lbs  = gained * ((desc and desc.fuelMassMax) or 0) * 2.20462   -- kg -> lbs

    local r4 = function(x) return math.floor(x * 10000 + 0.5) / 10000 end
    return net.lua2json({
        gained   = r4(gained),                                              -- fraction of internal capacity
        lbs      = math.floor(lbs + 0.5),
        secs     = r4(timer.getTime() - t.t_base),
        gauge    = { from = r4(t.base), to = r4(unit:getFuel() or 0) }   -- before/after getFuel()
    })
end

-- --------------------------------------------------------------
-- 2b) synthesise the MP-missing START event (S_EVENT_REFUELING, id 7)
-- --------------------------------------------------------------
-- DCS never fires it on MP servers, so the STOP row would have no partner.
-- Emitted at the FIRST positive rise inside aar_sample() - the moment the transfer
-- actually starts. It needs no confirmation, and it also covers contacts that never
-- get a STOP (player disconnects on the boom).
-- BOTH PATHS, ONE ROW: on SP / a non-dedicated server DCS fires id 7 itself, so
-- AAR.real[uid] is set and emitStart() stays silent - DCS' own event is richer
-- (position/lat-lon) and travels the normal path. If the synthetic row is already
-- out when the real event shows up, the real one is swallowed (aar_skip) so one
-- contact can never produce two START rows. Safe to inject: nothing on
-- the Python side validates eventName - listener.py writes data['eventName']
-- verbatim (line 152/164) and the live-stat branches (BIRTH / KILL /
-- UNIT_LOST / BASE_CAPTURED) simply ignore it, so no embed or report changes.
function AAR.emitStart(unit, t)
    -- respect event_filter: if id 7 is filtered out, event_by_id has no name for
    -- it and we must not inject a row the user asked not to receive either
    local name = event_by_id[world.event.S_EVENT_REFUELING]
    if not name or not t or t.start_sent then
        return
    end
    if AAR.real[unit:getID()] then
        return                            -- DCS sent its own START -> never double up
    end
    t.start_sent = true
    dcsbot.sendBotTable({
        command   = 'onMissionEvent',
        id        = world.event.S_EVENT_REFUELING,
        time      = t.t_base,                 -- mission time of the baseline sample
        eventName = name,
        initiator = AAR.initiator(unit)
    })
end

function AAR.initiator(unit)                  -- same shape _update_database() reads
    local group = unit:getGroup()
    local desc  = unit:getDesc()
    return {
        type       = 'UNIT',
        unit       = unit,
        unit_name  = unit:getName(),
        group      = group,
        group_name = (group and group:isExist()) and group:getName() or nil,
        name       = unit:getPlayerName(),
        coalition  = unit:getCoalition(),
        unit_type  = unit:getTypeName(),
        category   = desc and desc.category
    }
end

function dcsbot.eventHandler:onEvent(event)
	status, err = pcall(onMissionEvent, event)
	if not status then
		env.warning("DCSServerBot - Error during MissionStatistics:onEvent(): " .. err)
	end
end

function onMissionEvent(event)
	if event == nil then
	    return
	end

    if event_by_id[event.id] == nil then
        return
    end

    local msg = {
        command = 'onMissionEvent',
        id = event.id,
        time = event.time,
        eventName = event_by_id[event.id]
    }

    if event.initiator then
        msg.initiator = {}
        local category = Object.getCategory(event.initiator)
        if category == Object.Category.UNIT then
            msg.initiator.type = 'UNIT'
            msg.initiator.unit = event.initiator
            msg.initiator.unit_name = msg.initiator.unit:getName()
            msg.initiator.group = msg.initiator.unit:getGroup()
            if msg.initiator.group and msg.initiator.group:isExist() then
                msg.initiator.group_name = msg.initiator.group:getName()
            end
            msg.initiator.name = msg.initiator.unit:getPlayerName()
            msg.initiator.coalition = msg.initiator.unit:getCoalition()
            msg.initiator.unit_type = msg.initiator.unit:getTypeName()
            msg.initiator.category = msg.initiator.unit:getDesc().category
            local point = msg.initiator.unit:getPosition().p
            if point.y > 0 and point.y < 20000 then
                local lat, lon = Terrain.convertMetersToLatLon(point.x, point.z)
                msg.initiator.position = {
                    point = point,
                    lat = lat,
                    lon = lon
                }
            end
            if event.id == world.event.S_EVENT_RUNWAY_TAKEOFF then
                if not event.place then
                    msg['eventName'] = 'S_EVENT_GROUND_TAKEOFF'
                elseif msg.initiator.name then  -- we only check for taxiway takeoffs for players
                    local place = event.place:getName()
                    local airbase = Airbase.getByName(place)
                    local velocity = msg.initiator.unit:getVelocity()
                    -- ignore takeoffs from ships and FARPs
                    if airbase:getDesc().category == Airbase.Category.AIRDROME then
                        local runways = airbase:getRunways()
                        local on_runway = false

                        for _, runway in pairs(runways) do
                            if is_on_runway(runway, point, velocity) then
                                on_runway = true
                                break
                            end
                        end

                        -- check and allow missing runways
                        if not on_runway then
                            local runway = MISSING_RUNWAYS[place]
                            if runway then
                                on_runway = is_on_runway(runway, point, velocity)
                            end
                        end
                        if not on_runway then
                            -- ignore unnecessary events for helicopters
                            if msg.initiator.category ~= Group.Category.AIRPLANE then
                                return
                            end
                            -- check for vertical takeoffs
                            if is_vertical_takeoff(velocity) then
                                return
                            end
                            msg.eventName = 'S_EVENT_TAXIWAY_TAKEOFF'
                            msg.velocity = velocity
                            --[[
                            if msg.initiator.name then
                                trigger.action.markToAll(marker_num, "Takeoff " .. msg.initiator.name, point, true, '')
                                marker_num = marker_num + 1
                            end
                            ]]--
                        end
                    end
                end
            elseif event.id == world.event.S_EVENT_REFUELING then
                -- SP / non-dedicated server -> DCS reports the START itself
                local uid = event.initiator:getID()
                AAR.real[uid] = true                 -- aar_sample() must not synthesise
                local t = AAR.tasks[uid]
                if t and t.start_sent then
                    return                           -- ignore the event
                end
            elseif event.id == world.event.S_EVENT_REFUELING_STOP then
                msg.comment = aar_onStop(event.initiator)          -- also clears AAR.real[uid]
            end
        elseif category == Object.Category.WEAPON then
            msg.initiator.type = 'WEAPON'
            msg.initiator.unit = event.initiator
            msg.initiator.unit_name = msg.initiator.unit:getName()
            msg.initiator.coalition = msg.initiator.unit:getCoalition()
            msg.initiator.unit_type = msg.initiator.unit:getTypeName()
            msg.initiator.category = msg.initiator.unit:getDesc().category
        elseif category == Object.Category.STATIC then
            msg.initiator.type = 'STATIC'
            -- ejected pilot, unit will not be counted as dead but only lost
            if event.id == world.event.S_EVENT_LANDING_AFTER_EJECTION then
                msg.initiator.unit = event.initiator
                msg.initiator.unit_name = string.format("Ejected Pilot ID %s", tostring(event.initiator.id_))
                msg.initiator.coalition = 0
                msg.initiator.unit_type = 'Ejected Pilot'
                msg.initiator.category = 0
            else
                msg.initiator.unit = event.initiator
                msg.initiator.unit_name = msg.initiator.unit:getName()
                msg.initiator.coalition = msg.initiator.unit:getCoalition()
                msg.initiator.unit_type = msg.initiator.unit:getTypeName()
            end
        elseif category == Object.Category.BASE then
            msg.initiator.type = 'BASE'
            msg.initiator.unit = event.initiator
            msg.initiator.unit_name = msg.initiator.unit:getName()
            msg.initiator.coalition = msg.initiator.unit:getCoalition()
            msg.initiator.unit_type = msg.initiator.unit:getTypeName()
        elseif category == Object.Category.SCENERY  then
            msg.initiator.type = 'SCENERY'
            msg.initiator.unit = event.initiator
            if msg.initiator.unit.getName ~= nil then
                msg.initiator.unit_name = msg.initiator.unit:getName()
            else
                msg.initiator.unit_name = 'n/a'
            end
            if msg.initiator.unit.getTypeName ~= nil then
                msg.initiator.unit_type = msg.initiator.unit:getTypeName()
            else
                msg.initiator.unit_type = "SCENERY"
            end
            msg.initiator.coalition = coalition.side.NEUTRAL
        elseif category == Object.Category.CARGO then
            msg.initiator.type = 'CARGO'
            msg.initiator.unit = event.initiator
            msg.initiator.unit_name = msg.initiator.unit:getName()
            msg.initiator.coalition = msg.initiator.unit:getCoalition()
            msg.initiator.unit_type = msg.initiator.unit:getTypeName()
        else
            -- ignore the event
            return
        end
    end
    if event.target then
        msg.target = {}
        local category = Object.getCategory(event.target)
        if category == Object.Category.UNIT then
            msg.target.type = 'UNIT'
            msg.target.unit = event.target
            msg.target.unit_name = msg.target.unit:getName()
            msg.target.group = msg.target.unit:getGroup()
            if msg.target.group and msg.target.group:isExist() then
                msg.target.group_name = msg.target.group:getName()
            end
            msg.target.name = msg.target.unit:getPlayerName()
            msg.target.coalition = msg.target.unit:getCoalition()
            msg.target.unit_type = msg.target.unit:getTypeName()
            msg.target.category = msg.target.unit:getDesc().category
            local point = msg.target.unit:getPosition().p
            if point.y > 0 and point.y < 20000 then
                local lat, lon = Terrain.convertMetersToLatLon(point.x, point.z)
                msg.target.position = {
                    point = point,
                    lat = lat,
                    lon = lon
                }
            end
            if msg.initiator ~= nil and msg.initiator.position ~= nil and msg.target.position ~= nil then
                msg.distance = get_distance(msg.initiator.position.point, msg.target.position.point)
            end
        elseif category == Object.Category.WEAPON then
            msg.target.type = 'WEAPON'
            msg.target.unit = event.target
            msg.target.unit_name = msg.target.unit:getName()
            msg.target.coalition = msg.target.unit:getCoalition()
            msg.target.unit_type = msg.target.unit:getTypeName()
            msg.target.category = msg.target.unit:getDesc().category
        elseif category == Object.Category.STATIC then
            msg.target.type = 'STATIC'
            msg.target.unit = event.target
            if msg.target.unit.isExist ~= nil and msg.target.unit:isExist() == true then
                msg.target.unit_name = msg.target.unit:getName()
                if msg.target.unit_name ~= nil and msg.target.unit_name ~= '' then
                    msg.target.coalition = msg.target.unit:getCoalition()
                    msg.target.unit_type = msg.target.unit:getTypeName()
                end
            end
        elseif category == Object.Category.BASE then
            msg.target.type = 'BASE'
            msg.target.unit = event.target
            msg.target.unit_name = msg.target.unit:getName()
            msg.target.coalition = msg.target.unit:getCoalition()
            msg.target.unit_type = msg.target.unit:getTypeName()
        elseif category == Object.Category.SCENERY then
            msg.target.type = 'SCENERY'
            msg.target.unit = event.target
            if msg.target.unit.getName ~= nil then
                msg.target.unit_name = msg.target.unit:getName()
            else
                msg.target.unit_name = 'n/a'
            end
            msg.target.coalition = coalition.side.NEUTRAL
            if msg.target.unit.getTypeName ~= nil then
                msg.target.unit_type = msg.target.unit:getTypeName()
            else
                msg.target.unit_type = 'n/a'
            end
        elseif category == Object.Category.CARGO then
            msg.target.type = 'CARGO'
            msg.target.unit = event.target
            msg.target.unit_name = msg.target.unit:getName()
            msg.target.coalition = msg.target.unit:getCoalition()
            msg.target.unit_type = msg.target.unit:getTypeName()
        else
            -- ignore the event
            return
        end
    end
    if event.place and event.place:isExist() then
        msg.place = {}
        msg.place.id = event.place.id_
        msg.place.name = event.place:getName()
    end
    if event.weapon then
        msg.weapon = {}
        msg.weapon.id = event.weapon.id_
        if event.weapon:isExist() then
            msg.weapon.name = event.weapon:getTypeName()
        elseif event.weapon_name ~= nil then
            msg.weapon.name = event.weapon_name
        else
            msg.weapon.name = 'Gun'
        end
        -- msg.weapon.category = event.weapon:getDesc().category
        if msg.weapon.name == nil or msg.weapon.name == '' then
            msg.weapon.name = 'Gun'
        end
        if event.target == nil then
            msg.comment = "unsupported"
        end
    elseif event.weapon_name ~= nil then
        msg.weapon = {}
        msg.weapon.name = event.weapon_name
        if msg.weapon.name == nil or msg.weapon.name == '' then
            msg.weapon.name = 'Gun'
        end
    end
    if event.comment then
        msg.comment = event.comment
    end
    dcsbot.sendBotTable(msg)
end

function fillCoalitionsData(color)
    local coalitionColor = {}

    coalitionColor.airbases = {}
    for _, airbase in pairs(coalition.getAirbases(coalition.side[color])) do
        table.insert(coalitionColor.airbases, airbase:getName())
    end

    coalitionColor.units = {}
    for _, group in pairs(coalition.getGroups(coalition.side[color])) do
        local category = GROUP_CATEGORY[group:getCategory()]
        if category ~= nil then
            if (coalitionColor.units[category] == nil) then
                coalitionColor.units[category] = {}
            end
            for _, unit in pairs(Group.getUnits(group)) do
                if unit:isExist() and unit:isActive() then
                    table.insert(coalitionColor.units[category], unit:getName())
                end
            end
        else
            env.warning('Category not in table: ' .. group:getCategory(), false)
        end
    end

    coalitionColor.statics = {}
	for _, static in pairs(coalition.getStaticObjects(coalition.side[color])) do
		table.insert(coalitionColor.statics, static:getName())
    end
	return coalitionColor
end

function dcsbot.getMissionSituation(channel)
    env.info('DCSServerBot - getMissionSituation()')
    local msg = {
        command = 'getMissionSituation',
        coalitions = {
			BLUE = fillCoalitionsData('BLUE'),
			RED = fillCoalitionsData('RED'),
			NEUTRAL = fillCoalitionsData('NEUTRAL')
		}
    }
    dcsbot.sendBotTable(msg, channel)
end

function dcsbot.enableMissionStats(filter)
    filter = net.json2lua(filter)

    local filter_lookup = {}
    for _, v in ipairs(filter) do
        filter_lookup[v] = true
    end

    if not dcsbot.mission_stats_enabled then
        for k, v in pairs(world.event) do
            if not filter_lookup[k] then
                event_by_id[v] = k
            end
        end
    end
    world.addEventHandler(dcsbot.eventHandler)
    -- enable AAR timer
    if not AAR.timer_id then
        AAR.timer_id = timer.scheduleFunction(aar_tick, {}, timer.getTime() + AAR.INTERVAL)
    end
    env.info('DCSServerBot - Mission Statistics enabled.')
    dcsbot.mission_stats_enabled = true
end

function dcsbot.disableMissionStats()
	if dcsbot.mission_stats_enabled then
        world.removeEventHandler(dcsbot.eventHandler)
        -- disable AAR timer
        if AAR.timer_id then
            timer.removeFunction(AAR.timer_id)
            AAR.timer_id = nil
        end
        AAR.tasks = {}
        dcsbot.mission_stats_enabled = false
        env.info('DCSServerBot - Mission Statistics disabled.')
    end
end

env.info("DCSServerBot - MissionStats: mission.lua loaded.")
