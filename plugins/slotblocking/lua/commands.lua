local base 	= _G
local dcsbot= base.dcsbot

function dcsbot.uploadUserRoles(json)
    log.write('DCSServerBot', log.DEBUG, 'Slotblocking: uploadUserRoles()')
    dcsbot.userInfo[json.ucid].roles = json.roles
end
