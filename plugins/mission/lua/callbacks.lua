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
	local msg = {}
    if locate(default_names, name) then
        return false, config.MESSAGE_PLAYER_DEFAULT_USERNAME
    end
    name2 = name:gsub("[\r\n%z]", "")
    if name ~= name2 then
        return false, config.MESSAGE_PLAYER_USERNAME
    end
	if isBanned(ucid) then
        msg.command = 'sendMessage'
        msg.message = 'Banned user ' .. name .. ' (ucid=' .. ucid .. ') rejected.'
    	utils.sendBotTable(msg, config.ADMIN_CHANNEL)
	    return false, string.gsub(config.MESSAGE_BAN, "{}", dcsbot.banList[ucid])
	end
end

function mission.onMissionLoadBegin()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onMissionLoadBegin()')
	if dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
	if DCS.getCurrentMission() then
        local msg = {}
        msg.command = 'onMissionLoadBegin'
        msg.current_mission = DCS.getMissionName()
        msg.current_map = DCS.getCurrentMission().mission.theatre
        msg.mission_time = 0
        utils.sendBotTable(msg)
    end
end

function mission.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onMissionLoadEnd()')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/DCSServerBot.lua' .. '\\")")')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/mission/mission.lua' .. '\\")")')
    local msg = {}
    msg.command = 'onMissionLoadEnd'
    msg.filename = DCS.getMissionFilename()
    msg.current_mission = DCS.getMissionName()
    msg.current_map = DCS.getCurrentMission().mission.theatre
    msg.mission_time = 0
    msg.start_time = DCS.getCurrentMission().mission.start_time
    msg.date = DCS.getCurrentMission().mission.date

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
        local presets
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
	if id == 1 and dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
	local msg = {}
	msg.command = 'onPlayerConnect'
	msg.id = id
	msg.name = net.get_player_info(id, 'name')
	msg.ucid = net.get_player_info(id, 'ucid')
    msg.side = 0
    -- server user is never active
    if (msg.id == 1) then
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
	if id == 1 and dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
	local msg = {}
	msg.command = 'onPlayerStart'
	msg.id = id
	msg.ucid = net.get_player_info(id, 'ucid')
	msg.name = net.get_player_info(id, 'name')
    msg.side = 0
    msg.slot = -1
    msg.sub_slot = -1
    -- server user is never active
    if (msg.id == 1) then
        msg.active = false
    else
        msg.active = true
    end
	utils.sendBotTable(msg)
end

function mission.onPlayerStop(id)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerStop()')
    local msg = {}
    msg.command = 'onPlayerStop'
    msg.id = id
    msg.ucid = net.get_player_info(id, 'ucid')
    msg.name = net.get_player_info(id, 'name')
    msg.active = false
    utils.sendBotTable(msg)
end

function mission.onPlayerChangeSlot(id)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerChangeSlot()')
    local msg = {}
    msg.command = 'onPlayerChangeSlot'
    msg.id = id
    msg.ucid = net.get_player_info(id, 'ucid')
    msg.name = net.get_player_info(id, 'name')
    msg.side = net.get_player_info(id, 'side')
    msg.unit_type, msg.slot, msg.sub_slot = utils.getMulticrewAllParameters(id)
    -- DCS MC bug workaround
    if msg.sub_slot > 0 and msg.side == 0 then
        if dcsbot.blue_slots[net.get_player_info(PlayerId, 'slot')] ~= nil then
            msg.side = 2
        else
            msg.side = 1
        end
    end
    msg.unit_name = DCS.getUnitProperty(msg.slot, DCS.UNIT_NAME)
    msg.group_name = DCS.getUnitProperty(msg.slot, DCS.UNIT_GROUPNAME)
    msg.group_id = DCS.getUnitProperty(msg.slot, DCS.UNIT_GROUP_MISSION_ID)
    msg.unit_callsign = DCS.getUnitProperty(msg.slot, DCS.UNIT_CALLSIGN)
    msg.unit_display_name = DCS.getUnitTypeAttribute(DCS.getUnitType(msg.slot), "DisplayName")
    msg.active = true
    utils.sendBotTable(msg)
end

function mission.onSimulationStart()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onSimulationStart()')
    local msg = {}
    msg.command = 'onSimulationStart'
    utils.sendBotTable(msg)
end

function mission.onSimulationStop()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onSimulationStop()')
    dcsbot.registered = false
    local msg = {}
    msg.command = 'onSimulationStop'
    utils.sendBotTable(msg)
end

function mission.onSimulationPause()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onSimulationPause()')
	local msg = {}
	msg.command = 'onSimulationPause'
	utils.sendBotTable(msg)
end

function mission.onSimulationResume()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onSimulationResume()')
	local msg = {}
	msg.command = 'onSimulationResume'
	utils.sendBotTable(msg)
end

function mission.onGameEvent(eventName,arg1,arg2,arg3,arg4,arg5,arg6,arg7)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onGameEvent(' .. eventName .. ')')
    -- ignore false takeoff or landing events
    if eventName == 'change_slot' then
        mission.last_change_slot[arg1] = os.clock()
    elseif eventName == 'takeoff' or eventName == 'landing' then
        if mission.last_change_slot[arg1] and mission.last_change_slot[arg1] > (os.clock() - 60) then
            return
        end
        if mission.last_to_landing[arg1] and mission.last_to_landing[arg1] > (os.clock() - 10) then
            return
        else
            mission.last_to_landing[arg1] = os.clock()
        end
    end
	local msg = {}
	msg.command = 'onGameEvent'
	msg.eventName = eventName
	msg.arg1 = arg1
	msg.arg2 = arg2
	msg.arg3 = arg3
	msg.arg4 = arg4
	msg.arg5 = arg5
	msg.arg6 = arg6
	msg.arg7 = arg7
	if (msg.eventName == 'kill') then
		msg.victimCategory = utils.getCategory(arg5)
		msg.killerCategory = utils.getCategory(arg2)
	end
	utils.sendBotTable(msg)
end

function mission.onPlayerTrySendChat(from, message, to)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerTrySendChat()')
    if from == 1 then
        return message
    end
    local msg = {}
    if string.sub(message, 1, 1) == config.CHAT_COMMAND_PREFIX then
        msg.command = 'onChatCommand'
        local elements = utils.split(message, ' ')
        msg.subcommand = string.sub(elements[1], 2)
        msg.params = { unpack(elements, 2) }
        msg.from_id = net.get_player_info(from, 'id')
        msg.from_name = net.get_player_info(from, 'name')
        msg.to = to
        utils.sendBotTable(msg)
        return ''
    else
        msg.command = 'onChatMessage'
        msg.message = message
        msg.from_id = net.get_player_info(from, 'id')
        msg.from_name = net.get_player_info(from, 'name')
        msg.to = to
        if msg.from_id ~= 1 then
            utils.sendBotTable(msg)
        end
    end
    return message
end

function mission.onChatMessage(message, from, to)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onChatMessage()')
    if not from then
        local msg = {}
        msg.command = 'onChatMessage'
        msg.message = message
        msg.from_id = net.get_player_info(from, 'id')
        msg.from_name = net.get_player_info(from, 'name')
        msg.to = to
        utils.sendBotTable(msg, config.CHAT_CHANNEL)
    end
end

DCS.setUserCallbacks(mission)
