-- dd2mcp watcher, input module (2026-11-03): read-only. Stream `input`: edges of the game's own button flags (app.CharacterInput.Action, bit = 1 << value) for the
-- player AND every pawn (a pawn's "buttons" are what its AI presses). Independent of key bindings / pad. Needs dd2mcp_watch.lua.
local W = rawget(_G, "DD2WATCH")
if not W or W.input_loaded2 then return end
W.input_loaded2 = true
local emit = W.emit
local NAMES = { [0] = "None", "AttackS", "AttackL", "Jump", "Dash", "Grab", "Interact", "Skill1", "Skill2", "Skill3", "Skill4", "AimHold", "AimSwitch", "Aim", "Shift1", "Shift2",
    "CameraReset", "Come", "Go", "Help", "Wait", "ActCancel", "KeepDash", "Shoot", "SkillAdditional", "Throw", "ThrowRelease", "Skill5", "ShootRelease", "JobSpecialAction",
    "Shift1OnlySpecificJob", "SwitchLockOn", "SwitchSkill", "Ascent", "Descent", "Present", "Sheathe", "Draw", "Lantern", "RecoverHpItem", "RecoverStaminaItem",
    "Job01GuardLockOnChange", "Job02MeteorShotSwitchAimType", "ItemMenu", "MoveForward", "MoveBackward", "MoveLeftward", "MoveRightward", "CameraUp", "CameraDown",
    "CameraLeft", "CameraRight", "CameraMove", "GimmickCancel", "GimmickSpecial1", "GimmickSpecial2", "GimmickAction", "GliderAscent", "GliderDescent", "Job08SwitchSkill",
    "Walk", "Skill6", "SkillGuideFix" }
local last = {}
local ERRN = 0
local function go_name(c)
    local ok, n = pcall(function() return c:call("get_GameObject"):call("get_Name") end)
    return ok and n and tostring(n) or "?"
end
local function bits_to_names(mask)
    local out = {}
    for i = 1, 62 do if (mask & (1 << i)) ~= 0 then out[#out + 1] = NAMES[i] or ("bit" .. i) end end
    return out
end
local function poll(who, chara)
    local input = chara:call("get_Input")
    if not input then return end
    local on = input:get_field("ButtonOnFlags") or 0
    local prev = last[who] or 0
    if on ~= prev then
        last[who] = on
        local down = bits_to_names(on & ~prev)
        local up = bits_to_names(prev & ~on)
        -- the locomotion axes change constantly: keep them in `held` but only report events when a non-move button changes or a move starts / stops
        emit("input", { who = who, down = (#down > 0) and down or nil, up = (#up > 0) and up or nil, held = bits_to_names(on) })
    end
end
re.on_frame(function()
    local ok, e = pcall(function()
        local cm = sdk.get_managed_singleton("app.CharacterManager")
        local h = cm and cm:call("get_ManualPlayer")
        if h then poll("player", h) end
        local pm = sdk.get_managed_singleton("app.PawnManager")
        local pl = pm and pm:call("get_PawnCharacterList")
        if pl then
            for i = 0, pl:call("get_Count") - 1 do
                local c = pl:call("get_Item", i)
                poll(go_name(c), c)
            end
        end
    end)
    if not ok then
        ERRN = ERRN + 1
        if ERRN <= 10 or ERRN % 200 == 0 then emit("errors", { where = "input", n = ERRN, msg = tostring(e):sub(1, 300) }) end
    end
end)
emit("meta", { ev = "input_module_loaded" })
