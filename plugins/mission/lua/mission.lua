local base	= _G
dcsbot 		= base.dcsbot

local _menuItems = {}
local _roles = {}

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
    for _, menuEntry in ipairs(menuTable) do
        for menuName, menuData in pairs(menuEntry) do
            if type(menuData) == "table" then
                if menuData.command then
                    -- Add a command if the menuData contains "command"
                    missionCommands.addCommandForGroup(groupID, menuName, parentMenu, function()
                        sendEventToBot(playerID, menuData) -- Call the function with menuData
                    end)
                else
                    -- Create a submenu if it's a nested structure
                    local subMenu = missionCommands.addSubMenuForGroup(groupID, menuName, parentMenu)
                    buildMenu(playerID, groupID, menuData, subMenu) -- Process the submenu recursively
                end
            end
        end
    end
end

function dcsbot.createMenu(playerID, groupID, data)
    if not _menuItems[groupID] then
        parsedData = net.json2lua(data)
        _menuItems[groupID] = {}

        for _, rootMenuEntry in ipairs(parsedData) do
            for rootMenuName, rootMenuData in pairs(rootMenuEntry) do
                -- Create the root menu
                local rootMenu = missionCommands.addSubMenuForGroup(groupID, rootMenuName)
                -- Add the root menu to _menuItems[groupID] list
                _menuItems[groupID][rootMenuName] = rootMenu
                -- Process all children of the root menu recursively
                buildMenu(playerID, groupID, rootMenuData, rootMenu)
            end
        end
    end
end

function dcsbot.deleteMenu(groupID)
    menu = _menuItems[groupID]
    if menu then
        -- Iterate through each root menu in the table and delete it
        for _, menuItem in pairs(menu) do
            missionCommands.removeItemForGroup(groupID, menuItem)
        end
        -- Clear the menu items for this group
        _menuItems[groupID] = nil
    end
end

-- Don't call this function, it's for internal use only!
function dcsbot._setUserRoles(user, roles)
    _roles[user] = net.json2lua(roles)
end

function dcsbot.getUserRoles(user)
    return _roles[user]
end


-- Disable error popups in missions
env.setErrorMessageBoxEnabled(false)
