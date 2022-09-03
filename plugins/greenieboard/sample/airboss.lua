-- -------------------------
-- defaults not requiring code
-- default marshall radio 305.000 AM, changed below
-- default LSO radio 264.000 AM, changed below

-- Create AIRBOSS object.
local airbossWashington = AIRBOSS:New("CVN-73", "CVN-73")
airbossWashington:SetCarrierControlledArea(50)
airbossWashington:SetHandleAIOFF()
airbossWashington:SetAirbossNiceGuy(true)

--Options

--frequencies

airbossWashington:SetLSORadio(260.000, AM)
airbossWashington:SetMarshalRadio(260.300, AM)

--Recovery Window

airbossWashington:AddRecoveryWindow("06:07", "19:00", 1, nil, true, 30, false)
airbossWashington:AddRecoveryWindow("20:10", "04:15+1", 3, nil, true, 30, false)

--Option to close current recovery window, commented out for experimentation

--WireCorrection for multiplayer

airbossWashington:SetMPWireCorrection(12)

airbossWashington:SetDefaultPlayerSkill("Naval Aviator")
airbossWashington:SetPatrolAdInfinitum(true)

--debug

-- airbossWashington:SetDebugModeON()

--Menu Options

airbossWashington:SetMenuRecovery(180, 30, false)
airbossWashington:SetMenuMarkZones(airboss_enable_markzones)
airbossWashington:SetMenuSmokeZones(airboss_enable_smokezones)

-- Single carrier menu optimization.
airbossWashington:SetMenuSingleCarrier(true)

--Nav Options
airbossWashington:SetTACAN(73, "X", "GWB")
airbossWashington:SetICLS(13, "LSO")

--set VoiceOVer choice

airbossWashington:SetVoiceOversLSOByRaynor()
airbossWashington:SetVoiceOversMarshalByRaynor()


-- Radio relay units.
airbossWashington:SetRadioRelayMarshal("USS Arleigh Burke")

-- Remove landed AI planes from flight deck.
airbossWashington:SetDespawnOnEngineShutdown()

-- SAVING

-- Load all saved player grades from your "Saved Games\DCS" (if blank) folder (if lfs was desanitized) (or specified folder).

airbossWashington:Load()

airbossWashington:Save()


-- Automatically save player results to your "Saved Games\DCS" folder each time a player get a final grade from the LSO.
airbossWashington:SetAutoSave()

-- Enable trap sheet.
airbossWashington:SetTrapSheet()

--credit for the Sierra Hotel Break goes to Sickdog from the Angry Arizona Pilots - thank you!
function airbossWashington:OnAfterLSOGrade(From, Event, To, playerData, myGrade)

    local string_grade = myGrade.grade
    local player_name = playerData.name
    local player_wire = playerData.wire
    local player_case = myGrade.case
    local player_detail = myGrade.details

    player_name = player_name:gsub('[%p]', '')

    --local gradeForFile
    local trapsheet = ''
    if string_grade == "_OK_" and player_wire > 1 then
        --if  string_grade == "_OK_" and player_wire == "3" and player_Tgroove >=15 and player_Tgroove <19 then
        timer.scheduleFunction(underlinePass, {}, timer.getTime() + 5)
        if client_performing_sh:Get() == 1 then
            myGrade.grade = "_OK_<SH>"
            myGrade.points = myGrade.points
            client_performing_sh:Set(0)
            trapsheet = "SH_unicorn_AIRBOSS-trapsheet-" .. player_name
        else
            trapsheet = "unicorn_AIRBOSS-trapsheet-" .. player_name
        end

    elseif string_grade == "OK" and player_wire > 1 then
        if client_performing_sh:Get() == 1 then
            myGrade.grade = "OK<SH>"
            myGrade.points = myGrade.points + 0.5
            client_performing_sh:Set(0)
            trapsheet = "SH_AIRBOSS-trapsheet-" .. player_name
        else
            trapsheet = "AIRBOSS-trapsheet-" .. player_name
        end

    elseif string_grade == "(OK)" and player_wire > 1 then
        airbossWashington:SetTrapSheet(nil, "AIRBOSS-trapsheet-" .. player_name)
        if client_performing_sh:Get() == 1 then
            myGrade.grade = "(OK)<SH>"
            myGrade.points = myGrade.points + 1.00
            client_performing_sh:Set(0)
            trapsheet = "SH_AIRBOSS-trapsheet-" .. player_name
        else
            trapsheet = "AIRBOSS-trapsheet-" .. player_name
        end

    elseif string_grade == "--" and player_wire > 1 then
        if client_performing_sh:Get() == 1 then
            myGrade.grade = "--<SH>"
            myGrade.points = myGrade.points + 1.00
            client_performing_sh:Set(0)
            trapsheet = "SH_AIRBOSS-trapsheet-" .. player_name
        else
            trapsheet = "AIRBOSS-trapsheet-" .. player_name
        end

    elseif string_grade == "-- (BOLTER)" then
        trapsheet = "Bolter_AIRBOSS-trapsheet-" .. player_name
    elseif string_grade == "WOFD" then
        trapsheet = "WOFD_AIRBOSS-trapsheet-" .. player_name
    elseif string_grade == "OWO" then
        trapsheet = "OWO_AIRBOSS-trapsheet-" .. player_name
    elseif string_grade == "CUT" then
        if player_wire == 1 then
            myGrade.points = myGrade.points + 1.00
            trapsheet = "Cut_AIRBOSS-trapsheet-" .. player_name
        else
            trapsheet = "Cut_AIRBOSS-trapsheet-" .. player_name
        end
    end
    if player_case == 3 and player_detail == "    " then
        trapsheet = "NIGHT5_AIRBOSS-trapsheet-" .. player_name
        myGrade.grade = "_OK_"
        myGrade.points = 5.0
    end
    airbossWashington:SetTrapSheet(nil, trapsheet)

    myGrade.messageType = 2
    myGrade.callsign = playerData.callsign
    myGrade.name = playerData.name
    if playerData.wire == 1 then
        myGrade.points = myGrade.points - 1.00
        local onewire_to_discord = ('**' .. player_name .. ' almost had a rampstrike with that 1-wire!**')
        dcsbot.sendBotMessage(onewire_to_discord)
    end
    self:_SaveTrapSheet(playerData, mygrade)
    msg = {}
    msg.command = "onMissionEvent"
    msg.eventName = "S_EVENT_AIRBOSS"
    msg.initiator = {}
    msg.initiator.name = playerData.name
    msg.place = {}
    msg.place.name = myGrade.carriername
    msg.time = timer.getAbsTime()
    msg.points = myGrade.points
    msg.trapsheet = trapsheet
    dcsbot.sendBotTable(msg)
    timer.scheduleFunction(resetTrapSheetFileFormat, {}, timer.getTime() + 10)
end

--- Function called when recovery starts.
--
-- define airboss class.
airbossWashington:Start()

local cvnGroup = GROUP:FindByName("CSG-26")
local CVN_GROUPZone = ZONE_GROUP:New('cvnGroupZone', cvnGroup, 1111)

local BlueCVNClients = SET_CLIENT:New():FilterCoalitions("blue"):FilterStart()

Scheduler, SchedulerID = SCHEDULER:New(nil,
        function()

            local clientData = {}
            local player_name

            BlueCVNClients:ForEachClientInZone(CVN_GROUPZone,
                    function(MooseClient)

                        local function resetFlag()
                            client_in_zone_flag:Set(0)
                        end

                        local player_velocity = MooseClient:GetVelocityKNOTS()
                        local player_name = MooseClient:GetPlayerName()
                        local player_alt = MooseClient:GetAltitude()
                        local player_type = MooseClient:GetTypeName()

                        player_alt_feet = player_alt * 3.28
                        player_alt_feet = player_alt_feet / 10
                        player_alt_feet = math.floor(player_alt_feet) * 10

                        player_velocity_round = player_velocity / 10
                        player_velocity_round = math.floor(player_velocity_round) * 10

                        local function roundVelocity(player_velocity)
                            return x >= 0 and math.floor(x + 0.5) or math.ceil(x - 0.5)
                        end

                        if client_in_zone_flag == nil then
                            client_in_zone_flag = USERFLAG:New(MooseClient:GetClientGroupID() + 10000000)
                        else
                        end

                        if client_performing_sh == nil then
                            client_performing_sh = USERFLAG:New(MooseClient:GetClientGroupID() + 100000000)
                        else
                        end

                        if client_in_zone_flag:Get() == 0 and player_velocity > 475 and player_alt < 213 then
                            -- Requirements for Shit Hot break are velocity >475 knots and less than 213 meters (700')
                            trigger.action.outText(player_name .. ' performing a Sierra Hotel Break!', 10)
                            local sh_message_to_discord = ('**' .. player_name .. ' is performing a Sierra Hotel Break at ' .. player_velocity_round .. ' knots and ' .. player_alt_feet .. ' feet in a ' .. player_type .. '!**')
                            dcsbot.sendBotMessage(sh_message_to_discord)
                            client_in_zone_flag:Set(1)
                            client_performing_sh:Set(1)
                            timer.scheduleFunction(resetFlag, {}, timer.getTime() + 10)
                        else
                        end

                    end
            )

        end, {}, 2, 1
)

--[[

The AIRBOSS class supports all three commonly used recovery cases, i.e.

CASE I during daytime and good weather (ceiling > 3000 ft, visibility > 5 NM),
CASE II during daytime but poor visibility conditions (ceiling > 1000 ft, visibility > 5NM),
CASE III when below Case II conditions and during nighttime (ceiling < 1000 ft, visibility < 5 NM).


The F10 Radio Menu
The F10 radio menu can be used to post requests to Marshal but also provides information about the player and carrier status. Additionally, helper functions can be called.

Request Marshal
This radio command can be used to request a stack in the holding pattern from Marshal. Necessary conditions are that the flight is inside the Carrier Controlled Area (CCA)

Marshal will assign an individual stack for each player group depending on the current or next open recovery case window. If multiple players have registered as a section, the section lead will be assigned a stack and is responsible to guide his section to the assigned holding position.

Request Commence
This command can be used to request commencing from the marshal stack to the landing pattern. Necessary condition is that the player is in the lowest marshal stack and that the number of aircraft in the landing pattern is smaller than four (or the number set by the mission designer).

Spinning
If the pattern is full, players can go into the spinning pattern. This step is only allowed, if the player is in the pattern and his next step is initial, break entry, early/late break. At this point, the player should climb to 1200 ft a fly on the port side of the boat to go back to the initial again.

If a player is in the spin pattern, flights in the Marshal queue should hold their altitude and are not allowed into the pattern until the spinning aircraft proceeds.

Once the player reaches a point 100 meters behind the boat and at least 1 NM port, his step is set to "Initial" and he can resume the normal pattern approach.

If necessary, the player can call "Spinning" again when in the above mentioned steps.

Emergency Landing
Request an emergency landing, i.e. bypass all pattern steps and go directly to the final approach.

All section members are supposed to follow. Player (or section lead) is removed from all other queues and automatically added to the landing pattern queue.

If this command is called while the player is currently on the carrier, he will be put in the bolter pattern. So the next expected step after take of is the abeam position. This allows for quick landing training exercises without having to go through the whole pattern.

Set Section
With this command, you can define a section of human flights. The player who issues the command becomes the section lead and all other human players within a radius of 100 meters become members of the section.

The responsibilities of the section leader are:

To request Marshal. The section members are not allowed to do this and have to follow the lead to his assigned stack.
To lead the right way to the pattern if the flight is allowed to commence.
The lead is also the only one who can request commence if the flight wants to bypass the Marshal stack.
Each time the command is issued by the lead, the complete section is set up from scratch. Members which are not inside the 100 m radius any more are removed and/or new members which are now in range are added.

If a section member issues this command, it is removed from the section of his lead. All flights which are not yet in another section will become members.

The default maximum size of a section is two human players. This can be adjusted by the AIRBOSS.SetMaxSectionSize(size) function. The maximum allowed size is four.

Pattern Queue
Banner Image

Lists all flights currently in the landing pattern queue showing the time since they entered the pattern. By default, a maximum of four flights is allowed to enter the pattern. This can be set via the AIRBOSS.SetMaxLandingPattern function.

Waiting Queue
Lists all flights currently waiting for a free Case I Marshal stack. Note, stacks are limited only for Case I recovery ops but not for Case II or III. If the carrier is switches recovery ops form Case I to Case II or III, all waiting flights will be assigned a stack.

Landing Signal Officer (LSO)
The LSO will first contact you on his radio channel when you are at the the abeam position (Case I) with the phrase "Paddles, contact.". Once you are in the groove the LSO will ask you to "Call the ball." and then acknowledge your ball call by "Roger Ball."

]]--