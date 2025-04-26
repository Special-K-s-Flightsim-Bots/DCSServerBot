local base	    = _G
dcsbot 		    = base.dcsbot

dcsbot.tournamentEventHandler = dcsbot.tournamentEventHandler or {}

function dcsbot.tournamentEventHandler:onEvent(event)
	status, err = pcall(onTournamentEvent, event)
	if not status then
		env.warning("DCSServerBot - Error during Tournament:onTournamentEvent(): " .. err)
	end
end

function onTournamentEvent(event)
    if event.id == world.event.S_EVENT_BIRTH and event.initiator:getPlayerName() then
        local msg = {
            command = "addPlayerToMatch",
            player_name = event.initiator:getPlayerName(),
            match_id = 'tournament'
        }
        dcsbot.sendBotTable(msg)
    end
end

if not dcsbot.tournament_enabled then
    world.addEventHandler(dcsbot.tournamentEventHandler)
    env.info('DCSServerBot - Tournament EventHandler enabled.')
    dcsbot.tournament_enabled = true
end
