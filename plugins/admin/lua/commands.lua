local base = _G

local Terrain   = base.require('terrain')
local UC        = base.require("utils_common")

local dcsbot    = base.dcsbot
local utils 	= base.require("DCSServerBotUtils")
local config	= base.require("DCSServerBotConfig")

dcsbot.registered = false
dcsbot.banList = {}


function dcsbot.registerDCSServer(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: registerDCSServer()')
	local msg = {}
	msg.command = 'registerDCSServer'
	msg.hook_version = config.VERSION
	msg.dcs_version = Export.LoGetVersionInfo().ProductVersion[1] .. '.' .. Export.LoGetVersionInfo().ProductVersion[2] .. '.' .. Export.LoGetVersionInfo().ProductVersion[3] .. '.' .. Export.LoGetVersionInfo().ProductVersion[4]
    msg.host = config.DCS_HOST
	msg.port = config.DCS_PORT
	msg.chat_channel = config.CHAT_CHANNEL
	msg.status_channel = config.STATUS_CHANNEL
	msg.admin_channel = config.ADMIN_CHANNEL
	-- backwards compatibility
	if (config.STATISTICS ~= nil) then
		msg.statistics = config.STATISTICS
	else
		msg.statistics = true
	end
	msg.options = DCS.getUserOptions()
    -- airbases
    msg.airbases = {}
    local airdromes = Terrain.GetTerrainConfig("Airdromes")
    if (airdromes ~= nil) then
        for airdromeID, airdrome in pairs(airdromes) do
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
    end
    -- mission
    if DCS.getCurrentMission() then
        msg.filename = DCS.getMissionFilename()
        msg.current_mission = DCS.getMissionName()
        msg.current_map = DCS.getCurrentMission().mission.theatre
        msg.mission_time = DCS.getModelTime()
        msg.real_time = DCS.getRealTime()
        msg.start_time = DCS.getCurrentMission().mission.start_time
        msg.date = DCS.getCurrentMission().mission.date
        msg.pause = DCS.getPause()
        -- weather
        local weather = DCS.getCurrentMission().mission.weather
        msg.weather = weather
        local clouds = weather.clouds
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
        -- slots
        msg.num_slots_blue = table.getn(DCS.getAvailableSlots('blue'))
        msg.num_slots_red = table.getn(DCS.getAvailableSlots('red'))
        -- players
        plist = net.get_player_list()
        num_players = table.getn(plist)
        if num_players > 0 then
            msg.players = {}
            for i = 1, num_players do
                msg.players[i] = net.get_player_info(plist[i])
                msg.players[i].unit_type, msg.players[i].slot, msg.players[i].sub_slot = utils.getMulticrewAllParameters(plist[i])
                msg.players[i].unit_name = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_NAME)
                msg.players[i].group_name = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_GROUPNAME)
                msg.players[i].group_id = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_GROUP_MISSION_ID)
                msg.players[i].unit_callsign = DCS.getUnitProperty(msg.players[i].slot, DCS.UNIT_CALLSIGN)
                -- server user is never active
                if (msg.players[i].id == 1) then
                    msg.players[i].active = false
                else
                    msg.players[i].active = true
                end
            end
        end
    end
    -- check if DSMC is enabled
    msg.dsmc_enabled = (base.HOOK ~= nil)
    if (json ~= nil) then
        utils.sendBotTable(msg, json.channel)
    else
        utils.sendBotTable(msg)
    end
    dcsbot.registered = true
end

function dcsbot.start_server(json)
    net.start_server(utils.loadSettingsRaw())
end

function dcsbot.stop_server(json)
    net.stop_game()
end

function dcsbot.shutdown(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: shutdown()')
	DCS.exitProcess()
end

function dcsbot.kick(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: kick()')
    if json.id then
        net.kick(json.id, json.reason)
        return
    end
    plist = net.get_player_list()
    for i = 1, table.getn(plist) do
        if ((json.ucid and net.get_player_info(plist[i], 'ucid') == json.ucid) or
                (json.name and net.get_player_info(plist[i], 'name') == json.name)) then
            net.kick(plist[i], json.reason)
            break
        end
    end
end

function dcsbot.ban(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: ban()')
    dcsbot.banList[json.ucid] = json.reason
    dcsbot.kick(json)
end

function dcsbot.unban(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: unban()')
	dcsbot.banList[json.ucid] = nil
end

function dcsbot.force_player_slot(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: force_player_slot()')
    net.force_player_slot(json.playerID, json.sideID or 0, json.slotID or '')
end

function dcsbot.loadParams(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: loadParams(' .. json.plugin ..')')
    dcsbot.params = dcsbot.params or {}
    dcsbot.params[json.plugin] = json.params
end

function dcsbot.setCoalitionPassword(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: setCoalitionPassword()')
    settings = utils.loadSettingsRaw()
    if json.bluePassword then
        if json.bluePassword == '' then
            settings['advanced']['bluePasswordHash'] = nil
        else
            settings['advanced']['bluePasswordHash'] = net.hash_password(json.bluePassword)
        end
    end
    if json.redPassword then
        if json.redPassword == '' then
            settings['advanced']['redPasswordHash'] = nil
        else
            settings['advanced']['redPasswordHash'] = net.hash_password(json.redPassword)
        end
    end
    utils.saveSettings(settings)
end
