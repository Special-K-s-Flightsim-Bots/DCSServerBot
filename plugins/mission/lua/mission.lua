local base		= _G

dcsbot 			= base.dcsbot

-- deprecated
function dcsbot.sendPopupMessage(to, message, time)
	env.info('DCSServerBot - Popup Message')
	if to == 'all' then
        trigger.action.outText(message, time)
	elseif to == 'red' then
		trigger.action.outTextForCoalition(coalition.side.RED, message, time)
	elseif to == 'blue' then
		trigger.action.outTextForCoalition(coalition.side.BLUE, message, time)
	elseif to == 'neutrals' then
		trigger.action.outTextForCoalition(coalition.side.NEUTRAL, message, time)
	else
        local unit = Unit.getByName(to)
        if unit and unit:isExist() then
            trigger.action.outTextForUnit(unit:getID(), message, time)
        end
	end
end

-- deprecated
function dcsbot.playSound(to, sound)
	env.info('DCSServerBot - Play Sound')
	if to == 'all' then
        trigger.action.outSound(sound)
	elseif to == 'red' then
		trigger.action.outSoundForCoalition(coalition.side.RED, sound)
	elseif to == 'blue' then
		trigger.action.outSoundForCoalition(coalition.side.BLUE, sound)
	elseif to == 'neutrals' then
		trigger.action.outSoundForCoalition(coalition.side.NEUTRAL, sound)
	else
        local unit = Unit.getByName(to)
        if unit and unit:isExist() then
            trigger.action.outSoundForUnit(unit:getID(), sound)
        end
	end
end

function dcsbot.sendPopupMessage2(to, id, message, time)
	env.info('DCSServerBot - Popup Message')
	if to == 'all' then
        trigger.action.outText(message, time)
	elseif to == 'coalition' then
		if id == 'all' then
	        trigger.action.outText(message, time)
		elseif id == 'red' then
			trigger.action.outTextForCoalition(coalition.side.RED, message, time)
		elseif id == 'blue' then
			trigger.action.outTextForCoalition(coalition.side.BLUE, message, time)
		elseif id == 'neutrals' then
			trigger.action.outTextForCoalition(coalition.side.NEUTRAL, message, time)
		end
	elseif to == 'unit' then
        local unit = Unit.getByName(id)
        if unit and unit:isExist() then
            trigger.action.outTextForUnit(unit:getID(), message, time)
        end
	elseif to == 'group' then
		local group = Group.getByName(id)
		if group and group:isExist() then
            trigger.action.outTextForGroup(group:getID(), message, time)
		end
	end
end

function dcsbot.playSound2(to, id, sound)
	env.info('DCSServerBot - Play Sound')
	if to == 'all' then
        trigger.action.outSound(sound, time)
	elseif to == 'coalition' then
		if id == 'all' then
	        trigger.action.outSound(sound, time)
		elseif id == 'red' then
			trigger.action.outSoundForCoalition(coalition.side.RED, sound)
		elseif id == 'blue' then
			trigger.action.outSoundForCoalition(coalition.side.BLUE, sound)
		elseif id == 'neutrals' then
			trigger.action.outSoundForCoalition(coalition.side.NEUTRAL, sound)
		end
	elseif to == 'unit' then
        local unit = Unit.getByName(id)
        if unit and unit:isExist() then
            trigger.action.outSoundForUnit(unit:getID(), sound)
        end
	elseif to == 'group' then
		local group = Group.getByName(id)
		if group and group:isExist() then
            trigger.action.outSoundForGroup(group:getID(), sound)
		end
	end
end
