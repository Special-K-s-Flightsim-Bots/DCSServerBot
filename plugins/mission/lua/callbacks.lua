local base          = _G

local dcsbot        = base.dcsbot
local utils 	    = base.require("DCSServerBotUtils")
local Censorship    = base.require('censorship')
local textutil      = base.require('textutil')

dcsbot.banList      = dcsbot.banList or {}
dcsbot.locked       = dcsbot.locked or {}
dcsbot.muted        = dcsbot.muted or {}
dcsbot.userInfo     = dcsbot.userInfo or {}
dcsbot.red_slots    = dcsbot.red_slots or {}
dcsbot.blue_slots   = dcsbot.blue_slots or {}
dcsbot.whitelist    = dcsbot.whitelist or {}

local mission = mission or {}
mission.last_landing = {}
mission.last_change_slot = {}
mission.num_change_slots = {}
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
    '플레이어',
    'User'
}

local function locate(table, value)
    for i = 1, #table do
        if textutil.Utf8ToUpperCase(table[i]) == textutil.Utf8ToUpperCase(value) then return true end
    end
    return false
end

-- escape every Lua‑pattern metacharacter
local function escapePat(str)
    return str:gsub("([%^%$%(%)%%%.%[%]%*%+%-%?])", "%%%1")
end

local function normalize(name)
    name = name:lower()

    ---------
    -- 1.  Leet → alpha (single‑char only)
    ---------
    local leet = {
        ["4"] = "a", ["@"] = "a",
        ["8"] = "b", ["|3"] = "b",
        ["3"] = "e",
        ["1"] = "i", ["!"] = "i", ["|"] = "i",
        ["0"] = "o", ["()"] = "o",
        ["$"] = "s", ["5"] = "s",
        ["7"] = "t", ["+"] = "t",
        ["2"] = "z",
        ["9"] = "g", ["6"] = "g",
    }

    -- Process longer keys first only if you *add* any multi‑char keys again
    for k, v in pairs(leet) do
        local pat = escapePat(k)        -- literal pattern
        name = name:gsub(pat, v)        -- replace
    end

    ---------
    -- 2.  Diacritics → ASCII
    ---------
    local diacritics = {
        ["á"] = "a", ["é"] = "e", ["í"] = "i", ["ó"] = "o", ["ú"] = "u",
        ["ä"] = "a", ["ë"] = "e", ["ï"] = "i", ["ö"] = "o", ["ü"] = "u",
        ["ñ"] = "n",
    }
    for k, v in pairs(diacritics) do
        name = name:gsub(k, v)
    end

    -- collapse accidental whitespace
    name = name:gsub("%s+", " "):match("^%s*(.-)%s*$")

    return name
end

local function isBanned(ucid)
	return dcsbot.banList[ucid] ~= nil
end

local function isLocked(ucid)
    return dcsbot.locked[ucid] ~= nil
end

local function isMuted(ucid)
    return dcsbot.muted[ucid] ~= nil
end

function mission.onPlayerTryConnect(addr, name, ucid, _playerID)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerTryConnect()')
    if dcsbot.params == nil then
        -- don't block players, if the bot is not up
        return
    end
    local config = dcsbot.params.mission
    -- check if the server is locked
    if dcsbot.server_locked then
        return false, config.messages.message_server_locked
    end
    -- check if players use default names
    if locate(default_names, name) then
        return false, config.messages.message_player_default_username
    end
    local name2 = name:gsub("[\r\n%z]", "")
    -- local name2 = name:gsub("[%c]", "")
    if name ~= name2 or #name < 3 then
        return false, config.messages.message_player_username
    end
    -- check bans including the SMART ban system
    ipaddr = utils.getIP(addr)
    if isBanned(ucid) then
        if config.smart_bans then
            -- add their IP to the smart ban system
            dcsbot.banList[ipaddr] = ucid
        else
            log.write('DCSServerBot', log.DEBUG, 'Mission: Smart Bans disabled')
        end
        local msg = {
            command = 'onBanReject',
            name = name,
            ucid = ucid,
            ipaddr = ipaddr,
            reason = dcsbot.banList[ucid]
        }
        utils.sendBotTable(msg, config.channels.admin)
        return false, string.gsub(config.messages.message_ban, "{}", dcsbot.banList[ucid])
    elseif isBanned(ipaddr) and dcsbot.banList[dcsbot.banList[ipaddr]] then
        local old_ucid = dcsbot.banList[ipaddr]
        local msg = {
            command = 'onBanEvade',
            name = name,
            ucid = ucid,
            ipaddr = ipaddr,
            old_ucid = old_ucid,
            reason = dcsbot.banList[old_ucid]
        }
        utils.sendBotTable(msg, config.channels.admin)
        return false, string.gsub(config.messages.message_ban, "{}", dcsbot.banList[old_ucid])
    -- check if a player is temporarily locked
    elseif isLocked(ucid) then
        return false, config.messages.message_seat_locked
    end
    -- check if player uses profanity
    if config.profanity_filter and not dcsbot.whitelist[name] then
        name2 = normalize(name)
        if name2 ~= Censorship.censor(name2) then
            local msg = {
                command = 'onCensoredPlayerName',
                ucid = ucid,
                name = name
            }
            utils.sendBotTable(msg)
            if config.no_join_with_cursename then
                return false, config.messages.message_player_inappropriate_username
            end
        end
    end
end

function mission.onMissionLoadBegin()
    log.write('DCSServerBot', log.DEBUG, 'Mission: onMissionLoadBegin()')
	if dcsbot.registered == false then
		dcsbot.registerDCSServer()
	end
	if Sim.getCurrentMission() then
        local msg = {
            command = 'onMissionLoadBegin',
            current_mission = Sim.getMissionName(),
            current_map = Sim.getCurrentMission().mission.theatre,
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
        filename = Sim.getMissionFilename(),
        current_mission = Sim.getMissionName(),
        current_map = Sim.getCurrentMission().mission.theatre,
        mission_time = 0,
        start_time = Sim.getCurrentMission().mission.start_time,
        date = Sim.getCurrentMission().mission.date
    }

    num_slots_red = 0
    local availableSlots = Sim.getAvailableSlots("red")
    dcsbot.red_slots = {}
    if availableSlots ~= nil then
        for _,v in pairs(availableSlots) do
            dcsbot.red_slots[v.unitId] = v
            num_slots_red = num_slots_red + 1
        end
    end

    num_slots_blue = 0
    availableSlots = Sim.getAvailableSlots("blue")
    dcsbot.blue_slots = {}
    if availableSlots ~= nil then
        for _,v in pairs(availableSlots) do
            dcsbot.blue_slots[v.unitId] = v
            num_slots_blue = num_slots_blue + 1
        end
    end

    msg.num_slots_blue = num_slots_blue
    msg.num_slots_red = num_slots_red
    -- weather
    msg.weather = {}
    -- airbases
    msg.airbases = {}
    msg.dsmc_enabled = (base.HOOK ~= nil)
    -- clear any lockings
    dcsbot.server_locked = false
    dcsbot.locked = {}
    utils.sendBotTable(msg)
end

function mission.onMissionRestart(arg1)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onMissionRestart()')
    local msg = {
        command = "onMissionRestart",
        arg1 = arg1
    }
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
    mission.num_change_slots[id] = 0
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
        ipaddr = utils.getIP(net.get_player_info(id, 'ipaddr')),
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

function mission.onPlayerTryChangeSlot(id, side, slot)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerTryChangeSlot()')
    local config = (dcsbot.params and dcsbot.params.mission) or {}
    local slot_spamming = config.slot_spamming

    -- check, if seat is locked
    local ucid = net.get_player_info(id, 'ucid')
    if side > 0 and isLocked(ucid) then
        log.write('DCSServerBot', log.DEBUG, 'Mission: Player locked.')
        net.send_chat_to(config.messages.message_seat_locked, id)
        return false
    end
    -- check slot spamming
    if mission.num_change_slots[id] == -1 then
        return false
    end
    if not slot_spamming or not tonumber(slot) or utils.isDynamic(slot) then
        return
    end
	if mission.last_change_slot[id] and mission.last_change_slot[id] > (os.clock() - tonumber(slot_spamming.check_time or 5)) then
		mission.num_change_slots[id] = mission.num_change_slots[id] + 1
		if mission.num_change_slots[id] > tonumber(slot_spamming.slot_changes or 5) then
            mission.num_change_slots[id] = -1
			net.kick(id, slot_spamming.message)
            name = net.get_player_info(id, 'name')
            local msg = {
                command = 'sendMessage',
                message = 'Player ' .. name .. ' (ucid=' .. ucid .. ') kicked for slot spamming!'
            }
            utils.sendBotTable(msg, config.channels.admin)
			return false
        end
	else
		mission.last_change_slot[id] = os.clock()
    	mission.num_change_slots[id] = 0
	end
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
    msg.unit_name = Sim.getUnitProperty(msg.slot, Sim.UNIT_NAME)
    msg.group_name = Sim.getUnitProperty(msg.slot, Sim.UNIT_GROUPNAME)
    msg.group_id = Sim.getUnitProperty(msg.slot, Sim.UNIT_GROUP_MISSION_ID)
    msg.unit_callsign = Sim.getUnitProperty(msg.slot, Sim.UNIT_CALLSIGN)
    msg.unit_display_name = Sim.getUnitTypeAttribute(Sim.getUnitType(msg.slot), "DisplayName")

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
    if utils.isWithinInterval(mission.last_landing[arg1], 30) then
        return false
    else
        mission.last_landing[arg1] = os.clock()
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
        local _unit_type, slot, _sub_slot = utils.getMulticrewAllParameters(arg1)
        local display_name = Sim.getUnitTypeAttribute(Sim.getUnitType(slot), "DisplayName")
        -- do we have collisions (weapon == unit name)
        if display_name == arg2 then
            -- ignore "spawn on top"
            if utils.isWithinInterval(mission.last_change_slot[arg1], 60) or utils.isWithinInterval(mission.last_change_slot[arg3], 60) then
                return false
            end
            -- ignore multiple collisions that happened in-between 10s
            if ((utils.isWithinInterval(mission.last_collision[arg1], 10) and mission.last_victim[arg1] == arg3)) or ((utils.isWithinInterval(mission.last_collision[arg3], 10) and mission.last_victim[arg3] == arg1)) then
                return false
            else
                local now = os.clock()
                mission.last_collision[arg1] = now
                mission.last_collision[arg3] = now
                mission.last_victim[arg1] = arg3
                mission.last_victim[arg3] = arg1
            end
        end
    end,
    kill = function(arg1, _arg2, _arg3, arg4, _arg5, _arg6, arg7)
        local _unit_type, slot, _sub_slot = utils.getMulticrewAllParameters(arg1)
        local display_name = Sim.getUnitTypeAttribute(Sim.getUnitType(slot), "DisplayName")
        -- do we have collision kill (weapon == unit name)
        if display_name == arg7 then
            -- ignore "spawn on top"
            if utils.isWithinInterval(mission.last_change_slot[arg1], 60) or utils.isWithinInterval(mission.last_change_slot[arg4], 60) then
                return false
            end
        end
    end,
    onMissionRestart = function(arg1)
        mission.onMissionRestart(arg1)
    end
}

function mission.onGameEvent(eventName, arg1, arg2, arg3, arg4, arg5, arg6, arg7)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onGameEvent(' .. eventName .. ')')
    -- Call the appropriate handler based on the eventName
    if eventHandlers[eventName] then
        local result = eventHandlers[eventName](arg1, arg2, arg3, arg4, arg5, arg6, arg7)
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
		msg.killerCategory = utils.getCategory(arg2)
		msg.victimCategory = utils.getCategory(arg5)
	end
	utils.sendBotTable(msg)
end

function mission.onPlayerTrySendChat(from, message, to)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onPlayerTrySendChat()')
    if from == SERVER_USER_ID then
        return
    end
    local config = dcsbot.params.mission
    if string.sub(message, 1, 1) == config.chat_command_prefix then
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
    local ucid = net.get_player_info(from, 'ucid')
    if isMuted(ucid) then
        net.send_chat_to('You have been muted by an admin.', from)
        return ''
    end
    if config.profanity_filter then
        local new_msg = Censorship.censor(message)
        if new_msg ~= message then
            net.send_chat_to('Message was censored.', from)
            return new_msg
        end
    end
    -- Workaround DCS bug
    local side = net.get_player_info(from, 'side')
    if to == -2 and (side == 1 or side == 2) then
        mission.onChatMessage(message, from, to)
    end
end

function mission.onChatMessage(message, from, to)
    log.write('DCSServerBot', log.DEBUG, 'Mission: onChatMessage()')
    if from > 1 then
        local msg = {
            command = 'onChatMessage',
            message = message,
            from = from,
            to = to
        }
        utils.sendBotTable(msg, dcsbot.params.mission.channels.chat)
    end
end

Sim.setUserCallbacks(mission)
