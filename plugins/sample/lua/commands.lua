-----------------------------------------------------
-- All callback commands have to go here.
-- You have to make sure, that these commands are
-- named uniquely in the DCSServerBot context.
-----------------------------------------------------
local base 	    = _G
local dcsbot    = base.dcsbot
local utils 	= base.require("DCSServerBotUtils")
local config	= base.require("DCSServerBotConfig")

function dcsbot.sample(json)
    log.write('DCSServerBot', log.DEBUG, 'Sample: sample()')
    local msg = {}
    msg.command = 'sample'
    msg.message = json.message
    utils.sendBotTable(msg, json.channel)
end
