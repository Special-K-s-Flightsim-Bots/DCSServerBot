local base		= _G

dcsbot 			= base.dcsbot

function dcsbot.sendPopupMessage(to, message, time)
	env.info('DCSServerBot - Popup Message')
	if to == 'all' then
        trigger.action.outText(message, time)
	elseif to == 'red' then
		trigger.action.outTextForCoalition(coalition.side.RED, message, time)
	elseif to == 'blue' then
		trigger.action.outTextForCoalition(coalition.side.BLUE, message, time)
	else
        local unit = Unit.getByName(to)
        if unit and unit:isExist() then
            trigger.action.outTextForUnit(unit:getID(), message, time)
        end
	end
end

function dcsbot.playSound(to, sound)
	env.info('DCSServerBot - Play Sound')
	if to == 'all' then
        trigger.action.outSound(sound, time)
	elseif to == 'red' then
		trigger.action.outSoundForCoalition(coalition.side.RED, sound, time)
	elseif to == 'blue' then
		trigger.action.outSoundForCoalition(coalition.side.BLUE, sound, time)
	else
        local unit = Unit.getByName(to)
        if unit and unit:isExist() then
            trigger.action.outSoundForUnit(unit:getID(), sound, time)
        end
	end
end
