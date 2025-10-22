local base 	= _G
local utils	= base.require("DCSServerBotUtils")
local dcsbot= base.dcsbot

function dcsbot.do_script(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: do_script()')
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(json.script) .. ')')
end

function dcsbot.do_script_file(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: do_script_file()')
    net.dostring_in('mission', 'a_do_script("dofile(\\"' .. lfs.writedir():gsub('\\', '/') .. json.file .. '\\")")')
end

function dcsbot.setFlag(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: setFlag()')
	if json.value then
		net.dostring_in('mission', 'a_set_flag_value("' .. json.flag .. '", ' .. json.value .. ')')
	else
	    net.dostring_in('mission', 'a_set_flag("' .. json.flag .. '")')
	end
end

function dcsbot.getFlag(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: getFlag()')
    net.dostring_in('mission', 'a_do_script("dcsbot.getFlag(\\"' .. json.flag ..'\\", \\"' .. json.channel .. '\\")")')
end

function dcsbot.clearFlag(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: clearFlag()')
	net.dostring_in('mission', 'a_clear_flag("' .. json.flag .. '")')
end

function dcsbot.getVariable(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: getVariable()')
    local script = 'dcsbot.getVariable(' .. utils.basicSerialize(json.name) .. ', "' .. json.channel .. '")'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end

function dcsbot.setVariable(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: setVariable()')
    local script = 'dcsbot.setVariable(' .. utils.basicSerialize(json.name) .. ', ' .. utils.basicSerialize(json.value) .. ')'
    net.dostring_in('mission', 'a_do_script(' .. utils.basicSerialize(script) .. ')')
end

--[[
-- internal, do not use inside of missions unless you know what you are doing!
function dcsbot.setUserCoalition(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: setUserCoalition()')
    dcsbot.userInfo[json.ucid].coalition = tonumber(json.coalition)
end
]]--

function dcsbot.resetUserCoalitions()
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: resetUserCoalitions()')
    net.resetJoinCooldownEndForAll()
end

function dcsbot.resetUserCoalition(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: resetUserCoalition()')
    net.resetJoinCooldownEndForPlayer(json.id)
end
