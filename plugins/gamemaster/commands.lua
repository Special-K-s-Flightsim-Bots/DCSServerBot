-----------------------------------------------------
-- All callback commands have to go here.
-- You have to make sure, that these commands are
-- named uniquely in the DCSServerBot context.
-----------------------------------------------------
local base 	= _G
local dcsbot= base.dcsbot

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

function dcsbot.sendChatMessage(json)
	local message = json.message
	if (json.from) then
		message = json.from .. ': ' .. message
	end
	if (json.to) then
		net.send_chat_to(message, json.to)
	else
		net.send_chat(message, true)
	end
end

function dcsbot.sendPopupMessage(json)
  local message = json.message
	if (json.from) then
		message = json.from .. ': ' .. message
	end
  time = json.time or 10
  to = json.to or 'all'
  if to == 'all' then
    net.dostring_in('mission', 'a_out_text_delay(' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
  elseif to == 'red' then
    net.dostring_in('mission', 'a_out_text_delay_s(\'red\', ' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
  elseif to == 'blue' then
    net.dostring_in('mission', 'a_out_text_delay_s(\'blue\', ' .. basicSerialize(message) .. ', ' .. tostring(time) .. ')')
  end
end

