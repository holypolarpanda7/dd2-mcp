-- dd2mcp watcher, world module (2026-11-03): read-only. Stream `world`: in-game time / weather / indoor / cold on change (and every 30 s), player-party and nearby enemy status
-- conditions (poison, electric, enchant...) on change, area info dump. Needs dd2mcp_watch.lua.
local W = rawget(_G, "DD2WATCH")
if not W or W.world_loaded then return end
W.world_loaded = true
local emit = W.emit
local STATUS = { "Poison", "Sleep", "Faint", "Wet", "FireSpread", "OilSpread", "VirulentPoison", "Frostbite", "Freeze", "Oil", "Silence", "Stone", "Electric", "WetElectric",
    "EnchantFire", "EnchantIce", "EnchantThunder", "Original1", "Original2", "Original3", "Brainwash", "Takeover", "Blind", "Cold", "EnchantLight" }
local ERRN = 0
local function try(f, ...)
    local ok, r = pcall(f, ...)
    if not ok then ERRN = ERRN + 1 if ERRN <= 20 or ERRN % 100 == 0 then emit("errors", { where = "world", n = ERRN, msg = tostring(r):sub(1, 300) }) end end
    return ok and r or nil
end
local function go_name(c)
    local ok, n = pcall(function() return c:call("get_GameObject"):call("get_Name") end)
    return ok and n and tostring(n) or "?"
end
local S = { last_world = "", t_world = 0, t_status = 0, t_force = 0, status = {} }

local function poll_world(now)
    local row = {}
    local tm = sdk.get_managed_singleton("app.TimeManager")
    if tm then
        for _, g in ipairs({ { "hour", "get_InGameHour" }, { "min", "get_InGameMinute" }, { "day", "get_InGameDay" }, { "zone", "get_NowTimeZoneType" }, { "scale", "get_TimeScale" } }) do
            local ok, v = pcall(function() return tm:call(g[2]) end)
            if ok then row[g[1]] = v end
        end
    end
    local wm = sdk.get_managed_singleton("app.WeatherManager")
    if wm then
        for _, g in ipairs({ { "weather", "get_NowWeatherEnum" }, { "cold", "get_ColdLevel" }, { "blizzard", "get_BlizzardLevel" }, { "indoor", "get_IsIndoor" }, { "aurora", "get_NowAuroraState" } }) do
            local ok, v = pcall(function() return wm:call(g[2]) end)
            if ok then row[g[1]] = v end
        end
    end
    local key = tostring(row.hour) .. tostring(row.zone) .. tostring(row.weather) .. tostring(row.cold) .. tostring(row.indoor) .. tostring(row.blizzard)
    if key ~= S.last_world or now - S.t_force > 30 then
        S.last_world, S.t_force = key, now
        row.ev = "world"
        emit("world", row)
    end
end

local function actors()
    local list = {}
    local cm = sdk.get_managed_singleton("app.CharacterManager")
    local p = cm and cm:call("get_ManualPlayer")
    if not p then return list end
    list[#list + 1] = { "player", p }
    local pp = p:call("get_GameObject"):call("get_Transform"):call("get_Position")
    local pm = sdk.get_managed_singleton("app.PawnManager")
    local pl = pm and pm:call("get_PawnCharacterList")
    if pl then for i = 0, pl:call("get_Count") - 1 do list[#list + 1] = { "pawn", pl:call("get_Item", i) } end end
    local em = sdk.get_managed_singleton("app.EnemyManager")
    local el = em and em:get_field("_EnemyList")
    if el then
        for i = 0, el:call("get_Count") - 1 do
            local e = el:call("get_Item", i)
            local ok, d = pcall(function() local q = e:call("get_GameObject"):call("get_Transform"):call("get_Position") return math.sqrt((q.x - pp.x) ^ 2 + (q.z - pp.z) ^ 2) end)
            if ok and d <= 40 then list[#list + 1] = { "enemy", e } end
        end
    end
    return list
end

local function poll_status()
    for _, a in ipairs(actors()) do
        local c = a[2]
        local ctrl = c:call("get_StatusConditionCtrl")
        if ctrl then
            local on = {}
            for i, name in ipairs(STATUS) do
                local ok, act = pcall(function() return ctrl:call("IsStatusConditionActive(app.StatusConditionDef.StatusConditionEnum, System.Boolean)", i, false) end)
                if ok and act then on[#on + 1] = name end
            end
            local who = go_name(c)
            local k = table.concat(on, ",")
            if (S.status[who] or "") ~= k then
                S.status[who] = k
                emit("world", { ev = "status", who = who, kind = a[1], active = (#on > 0) and on or nil, was_empty = (k == "") })
            end
        end
    end
end

re.on_frame(function()
    local now = os.clock()
    if now - S.t_world > 5 then S.t_world = now; try(poll_world, now) end
    if now - S.t_status > 1 then S.t_status = now; try(poll_status) end
end)
emit("meta", { ev = "world_module_loaded" })
