Range = RANGE:New("Range")
Range:SetFunkManOn(10042, "127.0.0.1") -- YOUR SERVERBOT PORT / local ip
Range:AddBombingTargets("bombtarget", 25)
Range:AddStrafePit({"strafetarget-1"}, 4000, 500, 180, false, 20)
-- [...]
Range:Start()
