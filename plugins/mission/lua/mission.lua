local base		= _G

dcsbot 			= base.dcsbot

function dcsbot.sendPopupMessage(to, message, time)
	env.info('DCSServerBot - Popup Message!')
	if to == 'all' then
        trigger.action.outText(message, time)
	elseif to == 'red' then
		trigger.action.outTextForCoalition(coalition.side.RED, message, time)
	elseif to == 'blue' then
		trigger.action.outTextForCoalition(coalition.side.BLUE, message, time)
	else
        unit = Unit.getByName(to)
        if unit and unit:isExist() then
            trigger.action.outTextForUnit(unit:getID(), message, time)
        end
	end
end
