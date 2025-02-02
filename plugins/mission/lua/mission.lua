local base	= _G
dcsbot 		= base.dcsbot

dcsbot.menuItems = {}

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

function dcsbot.callback(msg, channel)
	local newmsg = msg
	newmsg.subcommand = msg.command
	newmsg.command = 'callback'
	dcsbot.sendBotTable(newmsg, channel)
end

function dcsbot.startMission(id)
	local msg = {
		command = 'startMission',
		id = id
	}
	dcsbot.callback(msg)
end

function dcsbot.shutdown()
	local msg = {
		command = 'shutdown'
	}
	dcsbot.callback(msg)
end

function dcsbot.restartMission()
	local msg = {
		command = 'restartMission'
	}
	dcsbot.callback(msg)
end

function dcsbot.sendEmbed(title, description, img, fields, footer, channel)
	dcsbot.updateEmbed(nil, title, description, img, fields, footer, channel)
end

function dcsbot.updateEmbed(id, title, description, img, fields, footer, channel)
	local msg = {
		command = 'sendEmbed',
		id = id,
		title = title,
		description = description,
		img = img,
		fields = fields,
		footer = footer
	}
	dcsbot.sendBotTable(msg, channel)
end

function dcsbot.setFog(visibility, thickness, channel)
    if visibility ~= -1 then
    	world.weather.setFogVisibilityDistance(visibility)
    end
    if thickness ~= -1 then
    	world.weather.setFogThickness(thickness)
    end
    local msg = {
        command = 'setFog',
        thickness =  world.weather.getFogThickness(),
        visibility = world.weather.getFogVisibilityDistance()
    }
    dcsbot.sendBotTable(msg, channel)
end

function dcsbot.getFog(channel)
    local msg = {
        command = 'getFog',
        thickness =  world.weather.getFogThickness(),
        visibility = world.weather.getFogVisibilityDistance()
    }
    dcsbot.sendBotTable(msg, channel)
end

function dcsbot.setFogAnimation(animation, channel)
    world.weather.setFogAnimation(animation)
    local msg = {
        command = 'setFogAnimation',
        thickness =  world.weather.getFogThickness(),
        visibility = world.weather.getFogVisibilityDistance()
    }
    dcsbot.sendBotTable(msg, channel)
end

-- Function to send an event to the bot
local function sendEventToBot(playerID, eventData)
    local msg = {
        command = eventData.command,
        subcommand = eventData.subcommand,
        params = eventData.params or {},
        from = playerID
    }
    dcsbot.sendBotTable(msg, "-1")
end

local function buildMenu(playerID, groupID, menuTable, parentMenu)
    for menuName, menuData in pairs(menuTable) do
        if type(menuData) == "table" and menuData.command then
            -- If it's a valid command at this level, create a menu command
            missionCommands.addCommandForGroup(groupID, menuName, parentMenu, function()
                sendEventToBot(playerID, menuData)
            end)
        elseif type(menuData) == "table" then
            -- Otherwise, assume it's a submenu
            local subMenu = missionCommands.addSubMenuForGroup(groupID, menuName, parentMenu)
            buildMenu(playerID, groupID, menuData, subMenu)  -- Recursively handle submenus
        end
    end
end

function dcsbot.createMenu(playerID, groupID, data)
    if not dcsbot.menuItems[groupID] then
        parsedData = net.json2lua(data)
        for rootMenuName, rootMenuData in pairs(parsedData) do
            dcsbot.menuItems[groupID] = missionCommands.addSubMenuForGroup(groupID, rootMenuName)
            buildMenu(playerID, groupID, rootMenuData, dcsbot.menuItems[groupID])
        end
    end
end

function dcsbot.deleteMenu(groupID)
    menu = dcsbot.menuItems[groupID]
    if menu then
        missionCommands.removeItemForGroup(groupID, menu)
        dcsbot.menuItems[groupID] = nil
    end
end

env.setErrorMessageBoxEnabled(false)
