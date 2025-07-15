local base 	    = _G
local dcsbot    = base.dcsbot
local utils 	= base.require("DCSServerBotUtils")

local slotblock = slotblock or {}


local function has_value(tab, value)
    if not tab then
        return false
    end
    for idx1, value1 in ipairs(tab) do
        if type(value) == "table" then
            for idx2, value2 in ipairs(value) do
                if value1 == value2 then
                    return true
                end
            end
        elseif value1 == value then
            return true
        end
    end
    return false
end

local function is_vip(ucid)
    if not dcsbot.params then
        return false
    end
    local cfg = dcsbot.params['slotblocking']['VIP']
    if not cfg then
        return false
    end
    if cfg['ucid'] and has_value(cfg['ucid'], ucid) then
        return true
    end
    if cfg['discord'] and dcsbot.userInfo[ucid].roles ~= nil and has_value(cfg['discord'], dcsbot.userInfo[ucid].roles) then
        return true
    end
    return false
end

function slotblock.onPlayerTryConnect(addr, name, ucid, playerID)
    if playerID == 1 then
        return
    end
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: onPlayerTryConnect()')
    if not dcsbot.params or not dcsbot.params['slotblocking'] then
        return
    end
    local cfg = dcsbot.params['slotblocking']['VIP']
    if not cfg then
        return
    end
    if cfg['slots'] then
        local max = tonumber(utils.loadSettingsRaw()['maxPlayers'])
        local current = #net.get_player_list() + 1
        if current >= (max - tonumber(cfg['slots'])) then
            if not is_vip(ucid) then
                return false, cfg['message_server_full'] or 'The server is full, please try again later!'
            end
        end
    end
end

function restrict_slots(playerID, side, slotID)
    local player = net.get_player_info(playerID, 'ucid')
    local unit_name = Sim.getUnitProperty(slotID, Sim.UNIT_NAME)
    local group_name = Sim.getUnitProperty(slotID, Sim.UNIT_GROUPNAME)
    local unit_type = Sim.getUnitType(slotID)
    local points
    -- check levels if any
    for id, unit in pairs(dcsbot.params['slotblocking']['restricted']) do
        local is_unit_type_match = (unit['unit_type'] and unit['unit_type'] == unit_type) or (unit['unit_type'] == 'dynamic' and utils.isDynamic(slotID))
        local is_unit_name_match = unit['unit_name'] and string.match(unit_name, unit['unit_name'])
        local is_group_name_match = unit['group_name'] and string.match(group_name, unit['group_name'])
        local is_side = (tonumber(unit['side']) or side) == side

        if is_side and (is_unit_type_match or is_unit_name_match or is_group_name_match) then
            -- blocking slots by points // check multicrew
            if tonumber(slotID) then
                points = tonumber(unit['points'])
            else
                points = tonumber(unit['crew'])
            end
            if points then
                if not dcsbot.userInfo[player].points then
                    log.write('DCSServerBot', log.ERROR, 'Slotblocking: User has no points, but points are configured. Check your creditsystem.yaml and make sure a campaign is running.')
                    return
                end
                if dcsbot.userInfo[player].points < points then
                    local message = 'You need at least ' .. points .. ' points to enter this slot. You currently have ' .. dcsbot.userInfo[player].points .. ' points.'
                    net.send_chat_to(message, playerID)
                    return false
                end
            end
            if unit['ucid'] and player ~= unit['ucid'] then
                local message = unit['message'] or 'This slot is only accessible to a certain user.'
                net.send_chat_to(message, playerID)
                return false
            elseif unit['ucids'] and not has_value(unit['ucids'], player) then
                local message = unit['message'] or 'This slot is only accessible to certain users.'
                net.send_chat_to(message, playerID)
                return false
                -- blocking slots by discord groups
            elseif unit['discord'] and not has_value(unit['discord'], dcsbot.userInfo[player].roles) then
                local message = unit['message'] or 'This slot is only accessible to members with a specific Discord role.'
                net.send_chat_to(message, playerID)
                return false
            elseif unit['VIP'] and not is_vip(player) then
                local message = unit['message'] or 'This slot is only accessible to VIP users.'
                net.send_chat_to(message, playerID)
                return false
            end
        end
    end
end

function calculate_balance(numPlayersBlue, numPlayersRed, blue_vs_red)
    local total = numPlayersBlue + numPlayersRed
    local balance

    if total ~= 0 then
        balance = numPlayersBlue / total
    else
        balance = blue_vs_red
    end

    return balance
end

function balance_slots(playerID, side, slotID)
    local config = dcsbot.params['slotblocking']['balancing']
    local blue_vs_red = config['blue_vs_red'] or 0.5
    local threshold = config['threshold'] or 0.1
    local activation_threshold = tonumber(config['activation_threshold'] or 0)
    local message = config['message'] or 'You need to take a slot of the opposite coalition to keep the balance!'
    local players = net.get_player_list()
    local numPlayersBlue = 0
    local numPlayersRed = 0

    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: balance_slots()')
    if #players < activation_threshold then
        log.write('DCSServerBot', log.DEBUG, 'Slotblocking: activation_threshold not reached')
        return
    end

    for _, id in base.pairs(players) do
        local side = net.get_player_info(id, 'side')
        local _, slot, sub_slot = utils.getMulticrewAllParameters(id)

        -- only count real seats
        if sub_slot == 0 and slot ~= -1 then
            if side == 2 then
                numPlayersBlue = numPlayersBlue + 1
            end
            if side == 1 then
                numPlayersRed = numPlayersRed + 1
            end
        end
    end
    local balance = calculate_balance(numPlayersBlue, numPlayersRed, blue_vs_red)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: balance: ' .. tostring(balance))
    if (side == 2 and balance > blue_vs_red + threshold) or (side == 1 and balance < blue_vs_red - threshold) then
        net.send_chat_to(message, playerID)
        return false
    end
end

function slotblock.onPlayerTryChangeSlot(playerID, side, slotID)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: onPlayerTryChangeSlot()')
    -- we will not block spectator slots
    if side == 0 then
        return
    end
    if not dcsbot.params or not dcsbot.params['slotblocking'] then
        log.write('DCSServerBot', log.ERROR, 'Slotblocking: No configuration found, skipping.')
        return
    end
    -- check slot restrictions by role and points
    if dcsbot.params['slotblocking']['restricted'] then
        if restrict_slots(playerID, side, slotID) == false then
            return false
        end
    end
    -- check slot restrictions by balance
    local old_side = net.get_player_info(playerID, 'side')
    -- if not side change happens or they want in a sub-slot, do not run balancing
    if old_side ~= side and tonumber(slotID) and dcsbot.params['slotblocking']['balancing'] then
        return balance_slots(playerID, side, slotID)
    end
end

Sim.setUserCallbacks(slotblock)
