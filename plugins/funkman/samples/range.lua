Range = RANGE:New("Range")
Range:SetFunkManOn(10042, "127.0.0.1") -- YOUR SERVERBOT PORT / local ip
Range:AddBombingTargets("bombtarget", 25)
Range:AddStrafePit({"strafetarget-1"}, 4000, 500, 180, false, 20)
-- [...]
Range:Start()

function Range:OnAfterImpact(From, Event, To, Result, Player)
    local player = Player
    local result = Result
end

function Range:OnAfterStrafeResult(From, Event, To, Player, Result)
    local player = Player
    local result = Result
end