local airbossCVN = AIRBOSS:New("CVN-7X", "CVN-7X") -- Carrier
airbossCVN:SetFunkManOn(10042, "127.0.0.1") -- YOUR SERVERBOT PORT / local ip
-- [...]
-- do any moose airboss setup in here
-- [...]
airbossCVN:Start()
function airboss:OnAfterLSOGrade(From, Event, To, playerData, grade)
    local PlayerData = playerData
    local Grade = grade
    local score = tonumber(Grade.points)
    local name = tostring(PlayerData.name)
end
