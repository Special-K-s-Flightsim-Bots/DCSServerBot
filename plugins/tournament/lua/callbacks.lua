local base      = _G
local utils 	= base.require("DCSServerBotUtils")

local tournament = tournament or {}

function tournament.onMissionLoadEnd()
    log.write('DCSServerBot', log.DEBUG, 'Tournament: onMissionLoadEnd()')
    utils.loadScript('DCSServerBot.lua')
    utils.loadScript('tournament/mission.lua')
end

DCS.setUserCallbacks(tournament)
