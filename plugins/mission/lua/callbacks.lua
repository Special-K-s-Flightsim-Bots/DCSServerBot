local base      = _G
local Terrain   = base.require('terrain')
local UC        = base.require("utils_common")

local dcsbot    = base.dcsbot
local utils 	= base.require("DCSServerBotUtils")
local config	= base.require("DCSServerBotConfig")

dcsbot.userInfo = dcsbot.userInfo or {}
dcsbot.red_slots = dcsbot.red_slots or {}
dcsbot.blue_slots = dcsbot.blue_slots or {}

local mission = mission or {}
mission.last_to_landing = {}
mission.last_change_slot = {}
mission.last_collision = {}
mission.last_victim = {}

local SERVER_USER_ID = 1

local default_names = {
    'Player',
    'Joueur',
    'Spieler',
    'Игрок',
    'Jugador',
    '玩家',
    'Hráč',
    '플레이어'
}

local function locate(table, value)
    for i = 1, #table do
        if table[i]:lower() == value:lower() then return true end
    end
    return false
end

local function isBanned(ucid)
	return dcsbot.banList[ucid] ~= nil
end

function mission.onPlayerTryConnect(addr, name, ucid, playerID)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerTryConnect()')
    if locate(default_names, name) then
        return false, config.MESSAGE_PLAYER_DEFAULT_USERNAME
    end
    local name2 = name:gsub("[\r\n%z]", "")
    if name ~= name2 then
        return false, config.MESSAGE_PLAYER_USERNAME
    end
    ipaddr = utils.getIP(addr)
    if isBanned(ucid) then
        -- add their IP to the smart ban system
        dcsbot.banList[ipaddr] = ucid
        local msg = {
            command = 'sendMessage',
            message = 'Banned user ' .. name .. ' (ucid=' .. ucid .. ') rejected.'
        }
        utils.sendBotTable(msg, config.ADMIN_CHANNEL)
        return false, string.gsub(config.MESSAGE_BAN, "{}", dcsbot.banList[ucid])
    elseif isBanned(ipaddr) and dcsbot.banList[dcsbot.banList[ipaddr]] then
        local old_ucid = dcsbot.banList[ipaddr]
        local msg = {
            command = 'sendMessage',
            message = 'Player ' .. name .. ' (ucid=' .. ucid .. ') connected from the same IP as banned player (ucid=' .. old_ucid .. ')!',
            mention = 'DCS Admin'
        }
        utils.sendBotTable(msg, config.ADMIN_CHANNEL)
        return false, string.gsub(config.MESSAGE_BAN, "{}", dcsbot.banList[old_ucid])
    end
end

function mission.onMissionLoadBegin()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onMissionLoadBegin()')
	if dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
	if DCS.getCurrentMission() then
        local msg = {
            command = 'onMissionLoadBegin',
            current_mission = DCS.getMissionName(),
            current_map = DCS.getCurrentMission().mission.theatre,
            mission_time = 0
        }
        utils.sendBotTable(msg)
    end
end

function mission.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onMissionLoadEnd()')
    utils.loadScript('DCSServerBot.lua')
    utils.loadScript('mission/mission.lua')
    local msg = {
        command = 'onMissionLoadEnd',
        filename = DCS.getMissionFilename(),
        current_mission = DCS.getMissionName(),
        current_map = DCS.getCurrentMission().mission.theatre,
        mission_time = 0,
        start_time = DCS.getCurrentMission().mission.start_time,
        date = DCS.getCurrentMission().mission.date
    }

    num_slots_red = 0
    dcsbot.red_slots = {}
    for k,v in pairs(DCS.getAvailableSlots("red")) do
        dcsbot.red_slots[v.unitId] = v
        num_slots_red = num_slots_red + 1
    end

    num_slots_blue = 0
    dcsbot.blue_slots = {}
    for k,v in pairs(DCS.getAvailableSlots("blue")) do
        dcsbot.blue_slots[v.unitId] = v
        num_slots_blue = num_slots_blue + 1
    end

    msg.num_slots_blue = num_slots_blue
    msg.num_slots_red = num_slots_red
    msg.weather = DCS.getCurrentMission().mission.weather
    local clouds = msg.weather.clouds
    if clouds.preset ~= nil then
        local func, err = loadfile(lfs.currentdir() .. '/Config/Effects/clouds.lua')

        local env = {
            type = _G.type,
            next = _G.next,
            setmetatable = _G.setmetatable,
            getmetatable = _G.getmetatable,
            _ = _,
        }
        setfenv(func, env)
        func()
        local preset = env.clouds and env.clouds.presets and env.clouds.presets[clouds.preset]
        if preset ~= nil then
            msg.clouds = {}
            msg.clouds.base = clouds.base
            msg.clouds.preset = preset
        end
    else
        msg.clouds = clouds
    end
    msg.airbases = {}
    for airdromeID, airdrome in pairs(Terrain.GetTerrainConfig("Airdromes")) do
        if (airdrome.reference_point) and (airdrome.abandoned ~= true)  then
            local airbase = {}
            airbase.code = airdrome.code
            if airdrome.display_name then
                airbase.name = airdrome.display_name
            else
                airbase.name = airdrome.names['en']
            end
            airbase.id = airdrome.id
            airbase.lat, airbase.lng = Terrain.convertMetersToLatLon(airdrome.reference_point.x, airdrome.reference_point.y)
            airbase.alt = Terrain.GetHeight(airdrome.reference_point.x, airdrome.reference_point.y)
            airbase.position = {}
            airbase.position.x = airdrome.reference_point.x
            airbase.position.y = airbase.alt
            airbase.position.z = airdrome.reference_point.y
            local frequencyList = {}
            if airdrome.frequency then
                frequencyList	= airdrome.frequency
            else
                if airdrome.radio then
                    for k, radioId in pairs(airdrome.radio) do
                        local frequencies = DCS.getATCradiosData(radioId)
                        if frequencies then
                            for kk,vv in pairs(frequencies) do
                                table.insert(frequencyList, vv)
                            end
                        end
                    end
                end
            end
            airbase.frequencyList = frequencyList
            airbase.runwayList = {}
            if (airdrome.runwayName ~= nil) then
                for r, runwayName in pairs(airdrome.runwayName) do
                    table.insert(airbase.runwayList, runwayName)
                end
            end
            heading = UC.toDegrees(Terrain.getRunwayHeading(airdrome.roadnet))
            if (heading < 0) then
                heading = 360 + heading
            end
            airbase.rwy_heading = heading
            table.insert(msg.airbases, airbase)
        end
    end
    msg.dsmc_enabled = (base.HOOK ~= nil)
    utils.sendBotTable(msg)
end

function mission.onPlayerConnect(id)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerConnect()')
	if id == SERVER_USER_ID and dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
	local msg = {
        command = 'onPlayerConnect',
        id = id,
        name = net.get_player_info(id, 'name'),
        ucid = net.get_player_info(id, 'ucid'),
        ipaddr = utils.getIP(net.get_player_info(id, 'ipaddr')),
        side = 0
    }
    -- server user is never active
    if (msg.id == SERVER_USER_ID) then
        msg.active = false
    else
        msg.active = true
    end
    dcsbot.userInfo[msg.ucid] = dcsbot.userInfo[msg.ucid] or {}
    dcsbot.userInfo[msg.ucid].points = nil
    dcsbot.userInfo[msg.ucid].coalition = nil
	utils.sendBotTable(msg)
end

function mission.onPlayerStart(id)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerStart()')
	if id == SERVER_USER_ID and dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
	local msg = {
        command = 'onPlayerStart',
        id = id,
        ucid = net.get_player_info(id, 'ucid'),
        name = net.get_player_info(id, 'name'),
        side = 0,
        slot = -1,
        sub_slot = -1
    }
    -- server user is never active
    if (msg.id == SERVER_USER_ID) then
        msg.active = false
    else
        msg.active = true
    end
	utils.sendBotTable(msg)
end

function mission.onPlayerStop(id)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerStop()')
    local msg = {
        command = 'onPlayerStop',
        id = id,
        ucid = net.get_player_info(id, 'ucid'),
        name = net.get_player_info(id, 'name'),
        active = false
    }
    utils.sendBotTable(msg)
end

function mission.onPlayerChangeSlot(id)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerChangeSlot()')
    local msg = {
        command = 'onPlayerChangeSlot',
        id = id,
        ucid = net.get_player_info(id, 'ucid'),
        name = net.get_player_info(id, 'name'),
        side = net.get_player_info(id, 'side'),
        active = true
    }
    msg.unit_type, msg.slot, msg.sub_slot = utils.getMulticrewAllParameters(id)
    msg.unit_name = DCS.getUnitProperty(msg.slot, DCS.UNIT_NAME)
    msg.group_name = DCS.getUnitProperty(msg.slot, DCS.UNIT_GROUPNAME)
    msg.group_id = DCS.getUnitProperty(msg.slot, DCS.UNIT_GROUP_MISSION_ID)
    msg.unit_callsign = DCS.getUnitProperty(msg.slot, DCS.UNIT_CALLSIGN)
    msg.unit_display_name = DCS.getUnitTypeAttribute(DCS.getUnitType(msg.slot), "DisplayName")

    -- DCS MC bug workaround
    if msg.sub_slot > 0 then
        if dcsbot.blue_slots[net.get_player_info(id, 'slot')] ~= nil then
            msg.side = 2
        elseif dcsbot.red_slots[net.get_player_info(id, 'slot')] ~= nil then
            msg.side = 1
        end
    end
    utils.sendBotTable(msg)
end

function mission.onSimulationStart()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onSimulationStart()')
    local msg = {
        command = 'onSimulationStart'
    }
    utils.sendBotTable(msg)
end

function mission.onSimulationStop()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onSimulationStop()')
    dcsbot.registered = false
    local msg = {
        command = 'onSimulationStop'
    }
    utils.sendBotTable(msg)
end

function mission.onSimulationPause()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onSimulationPause()')
	local msg = {
        command = 'onSimulationPause'
    }
	utils.sendBotTable(msg)
end

function mission.onSimulationResume()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onSimulationResume()')
	local msg = {
        command = 'onSimulationResume'
    }
	utils.sendBotTable(msg)
end

local function handleTakeoffLanding(arg1)
    if utils.isWithinInterval(mission.last_change_slot[arg1], 20) then
        return false
    end
    if utils.isWithinInterval(mission.last_to_landing[arg1], 30) then
        return false
    else
        mission.last_to_landing[arg1] = os.clock()
    end
end

local eventHandlers = {
    change_slot = function(arg1)
        mission.last_change_slot[arg1] = os.clock()
        log.write('DCSServerBot', log.DEBUG, 'Mission: change_slot(' .. arg1 .. ') = ' .. mission.last_change_slot[arg1])
    end,
    takeoff = handleTakeoffLanding,
    landing = handleTakeoffLanding,
    friendly_fire = function(arg1, arg2, arg3)
        unit_type, slot, sub_slot = utils.getMulticrewAllParameters(arg1)
        display_name = DCS.getUnitTypeAttribute(DCS.getUnitType(slot), "DisplayName")
        -- do we have collisions (weapon == unit name)
        if display_name == arg2 then
            -- ignore "spawn on top"
            if utils.isWithinInterval(mission.last_change_slot[arg1], 60) or utils.isWithinInterval(mission.last_change_slot[arg3], 60) then
                return false
            end
            -- ignore multiple collisions that happened in-between 10s
            if (utils.isWithinInterval(mission.last_collision[arg1], 10) and mission.last_victim[arg1] == arg3) or (utils.isWithinInterval(mission.last_collision[arg3], 10) and mission.last_victim[arg3] == arg1) then
                return false
            else
                mission.last_collision[arg1] = os.clock()
                mission.last_collision[arg3] = os.clock()
                mission.last_victim[arg1] = arg3
                mission.last_victim[arg3] = arg1
            end
        end
    end,
    kill = function(arg1,arg2,arg3,arg4,arg5,arg6,arg7)
        unit_type, slot, sub_slot = utils.getMulticrewAllParameters(arg1)
        display_name = DCS.getUnitTypeAttribute(DCS.getUnitType(slot), "DisplayName")
        -- do we have collision kill (weapon == unit name)
        if display_name == arg7 then
            -- ignore collision kills that happened in-between 10s
            if (utils.isWithinInterval(mission.last_collision[arg1], 10) and mission.last_victim[arg1] == arg4) or (utils.isWithinInterval(mission.last_collision[arg4], 10) and mission.last_victim[arg4] == arg1) then
                return false
            end
        end
    end
}

function mission.onGameEvent(eventName,arg1,arg2,arg3,arg4,arg5,arg6,arg7)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onGameEvent(' .. eventName .. ')')
    -- Call the appropriate handler based on the eventName
    if eventHandlers[eventName] then
        result = eventHandlers[eventName](arg1,arg2,arg3,arg4,arg5,arg6,arg7)
        if result == false then
            return
        end
    end

    local msg = {
        command = 'onGameEvent',
        eventName = eventName,
        arg1 = arg1,
        arg2 = arg2,
        arg3 = arg3,
        arg4 = arg4,
        arg5 = arg5,
        arg6 = arg6,
        arg7 = arg7
    }
	if eventName == 'kill' then
		msg.victimCategory = utils.getCategory(arg5)
		msg.killerCategory = utils.getCategory(arg2)
	end
	utils.sendBotTable(msg)
end

function mission.onPlayerTrySendChat(from, message, to)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerTrySendChat()')
    if from == SERVER_USER_ID then
        return message
    end
    if string.sub(message, 1, 1) == config.CHAT_COMMAND_PREFIX then
        local elements = utils.split(message, ' ')
        local msg = {
            command = 'onChatCommand',
            subcommand = string.sub(elements[1], 2),
            params = { unpack(elements, 2) },
            from = net.get_player_info(from, 'id'),
            to = to
        }
        utils.sendBotTable(msg)
        return ''
    end
    return message
end

function mission.onChatMessage(message, from, to)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onChatMessage()')
    if from ~= 1 then
        local msg = {
            command = 'onChatMessage',
            message = message,
            from = from,
            to = to
        }
        utils.sendBotTable(msg, config.CHAT_CHANNEL)
    end
end

DCS.setUserCallbacks(mission)
