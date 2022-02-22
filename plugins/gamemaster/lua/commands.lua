local base 	= _G
local dcsbot= base.dcsbot

function dcsbot.setFlag(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: setFlag()')
	if json.value then
		net.dostring_in('mission', 'a_set_flag_value(' .. json.flag .. ', ' .. json.value .. ')')
	else
	    net.dostring_in('mission', 'a_set_flag(' .. json.flag .. ')')
	end
end

function dcsbot.clearFlag(json)
    log.write('DCSServerBot', log.DEBUG, 'GameMaster: clearFlag()')
	net.dostring_in('mission', 'a_clear_flag(' .. json.flag .. ')')
end
