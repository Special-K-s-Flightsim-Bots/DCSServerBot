local base   		= _G

module('DCSServerBotUtils')

local net			= base.net
local package		= base.package
local pairs			= base.pairs
local require		= base.require
local string 		= base.string
local table         = base.table
local tonumber		= base.tonumber
local tostring		= base.tostring
local DCS			= base.DCS
local type			= base.type
local os            = base.os

local lfs			= require('lfs')
local TableUtils 	= require('TableUtils')
local Tools     	= require('tools')
local U 			= require('me_utilities')
local config		= require('DCSServerBotConfig')

package.path  = package.path..";.\\LuaSocket\\?.lua;"
package.cpath = package.cpath..";.\\LuaSocket\\?.dll;"
local socket = require("socket")
UDPSendSocket = socket.udp()

-- this is the DCS server name
server_name = nil

function sendBotTable(tbl, channel)
	if server_name == nil then
		server_name = loadSettingsRaw().name
	end
	tbl.server_name = server_name
	tbl.channel = channel or "-1"
	socket.try(UDPSendSocket:sendto(net.lua2json(tbl), config.BOT_HOST, config.BOT_PORT))
end

function loadSettingsRaw()
	local defaultSettingsServer = net.get_default_server_settings()
    local tbl = Tools.safeDoFile(lfs.writedir() .. "Config/serverSettings.lua", false)
    if (tbl and tbl.cfg) then
        return TableUtils.mergeTables(defaultSettingsServer, tbl.cfg)
    else
        return defaultSettingsServer
    end
end

function mergeGuiSettings(new_settings)
    local settings = loadSettingsRaw()
    for k, v in pairs(new_settings) do
        settings[k] = v
    end
    return settings
end

function saveSettings(settings)
    mergedSettings = mergeGuiSettings(settings)
    U.saveInFile(mergedSettings, "cfg", lfs.writedir() .. "Config/serverSettings.lua")
    return true
end

-- from perun
function getCategory(id)
	-- Helper function returns object category basing on https://pastebin.com/GUAXrd2U
	local _killed_target_category = "Other"

	-- Sometimes we get empty object id (seems like DCS API bug)
	if id ~= nil and id ~= "" then
		_killed_target_category = DCS.getUnitTypeAttribute(id, "category")

		-- Below, simple hack to get the proper category when DCS API is not returning correct value
		if _killed_target_category == nil then
			local _killed_target_cat_check_ship = DCS.getUnitTypeAttribute(id, "DeckLevel")
			local _killed_target_cat_check_plane = DCS.getUnitTypeAttribute(id, "WingSpan")
			if _killed_target_cat_check_ship ~= nil and _killed_target_cat_check_plane == nil then
				_killed_target_category = "Ships"
			elseif _killed_target_cat_check_ship == nil and _killed_target_cat_check_plane ~= nil then
				_killed_target_category = "Planes"
			else
				_killed_target_category = "Helicopters"
			end
		end
	end
	return _killed_target_category
end

-- from perun (slightly changed)
function getMulticrewAllParameters(PlayerId)
	-- Gets all multicrew parameters
	local _master_type= "?"
	local _master_slot
	local _sub_slot

	local _player_slot = net.get_player_info(PlayerId, 'slot')

	if _player_slot and _player_slot ~= '' then
		if not(string.find(_player_slot, 'red') or string.find(_player_slot, 'blue')) then
			-- Player took model
			_master_slot = _player_slot
			_sub_slot = 0

			if (not tonumber(_player_slot)) then
				-- If this is multiseat slot parse master slot and look for seat number
				_t_start, _t_end = string.find(_player_slot, '_%d+')

				if _t_start then
					-- This is co-player
					_master_slot = tonumber(string.sub(_player_slot, 0 , _t_start -1 ))
					_sub_slot = tonumber(string.sub(_player_slot, _t_start + 1, _t_end ))
				end
			else
				_master_slot = tonumber(_player_slot)
			end
			_master_type = DCS.getUnitType(_master_slot)

		else
			-- Deal with the special slots added by Combined Arms and Spectators
			if string.find(_player_slot, 'artillery_commander') then
				_master_type = "artillery_commander"
			elseif string.find(_player_slot, 'instructor') then
				_master_type = "instructor"
			elseif string.find(_player_slot, 'forward_observer') then
				_master_type = "forward_observer"
			elseif string.find(_player_slot, 'observer') then
				_master_type = "observer"
			end
			_master_slot = -1
			_sub_slot = 0
		end
	else
		_master_slot = -1
		_sub_slot = -1
	end
	return _master_type, _master_slot, _sub_slot
end

function split(str, sep)
   local result = {}
   local regex = ("([^%s]+)"):format(sep)
   for each in str:gmatch(regex) do
      table.insert(result, each)
   end
   return result
end

function basicSerialize(s)
	if s == nil then
		return "\"\""
	else
		if ((type(s) == 'number') or (type(s) == 'boolean') or (type(s) == 'function') or (type(s) == 'table') or (type(s) == 'userdata') ) then
			return tostring(s)
		elseif type(s) == 'string' then
			return string.format('%q', s)
		end
  end
end

function isWithinInterval(last_event, interval)
    return last_event and last_event > (os.clock() - interval)
end

function loadScript(scriptPath)
    command = 'dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. 'Scripts/net/DCSServerBot/' .. scriptPath .. '\\")'
    net.dostring_in('mission', 'a_do_script("' .. command .. '")')
end

function getIP(s)
	if s ~= nil then
		for ip in string.gmatch(s, "(%d+.%d+.%d+.%d+)") do
			return ip
		end
	end
    return nil
end

function isDynamic(slotId)
    if not(string.find(slotId, 'red') or string.find(slotId, 'blue')) then
        -- Player took model
        _master_slot = slotId
        _sub_slot = 0

        if (not tonumber(slotId)) then
            -- If this is multiseat slot parse master slot and look for seat number
            _t_start, _t_end = string.find(slotId, '_%d+')

            if _t_start then
                -- This is co-player
                _master_slot = tonumber(string.sub(slotId, 0 , _t_start -1 ))
                _sub_slot = tonumber(string.sub(slotId, _t_start + 1, _t_end ))
            end
        else
            _master_slot = tonumber(slotId)
        end
        return _master_slot > 1000000
    else
        return false
    end
end
