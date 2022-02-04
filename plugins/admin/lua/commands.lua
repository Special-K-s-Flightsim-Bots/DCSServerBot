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
	-- load the servers configuration (SRS, et al)
	local f = io.open(lfs.writedir() .. 'Scripts\\Hooks\\DCS-SRS-AutoConnectGameGUI.lua', 'r')
	if f then
		local content = f:read("*all")
		data = string.gsub(content, 'local SRSAuto = {}', 'SRSAuto = {}')
		data = string.gsub(data, '-- DO NOT EDIT BELOW HERE --(.*)$', '')
		loadstring(data)()
		f:close()
	end
	if (SRSAuto ~= nil) then
		local config_path = string.gsub(SRSAuto.SRS_NUDGE_PATH, 'clients.list.json', 'server.cfg')
		local f = io.open(config_path, 'r')
		if f then
			for line in f:lines() do
				k,v = line:match('^([^=]+)=(.+)$')
			  if k ~= nil then
						if (string.upper(v) == 'FALSE') then
							v = false
						elseif (string.upper(v) == 'TRUE') then
							v = true
						end
				SRSAuto[k] = v
				end
			  end
			f:close()
		end
	end
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
	msg.serverSettings = utils.loadSettingsRaw()
	msg.options = DCS.getUserOptions()
	msg.SRSSettings = SRSAuto
	if (lotatc_inst ~= nil) then
		msg.lotAtcSettings = lotatc_inst.options
	end
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
    if (json ~= nil) then
        utils.sendBotTable(msg, json.channel)
    else
        utils.sendBotTable(msg)
    end
    dcsbot.registered = true
end

function dcsbot.shutdown(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: shutdown()')
	DCS.exitProcess()
end

function dcsbot.kick(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: kick()')
    plist = net.get_player_list()
    for i = 1, table.getn(plist) do
        if ((json.ucid and net.get_player_info(plist[i], 'ucid') == json.ucid) or (json.name and net.get_player_info(plist[i], 'name') == json.name)) then
            net.kick(plist[i], json.reason)
            break
        end
    end
end

function dcsbot.ban(json)
    log.write('DCSServerBot', log.DEBUG, 'Admin: ban()')
    dcsbot.banList[json.ucid] = true
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
    log.write('DCSServerBot', log.DEBUG, 'Admin: loadParams()')
    dcsbot.params = dcsbot.params or {}
    dcsbot.params[json.plugin] = json.params
end
