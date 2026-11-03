-- dd2mcp watcher, AI module (2026-11-03): read-only. Needs dd2mcp_watch.lua (DD2WATCH.emit). Streams: ai (current decision of pawns / nearby enemies,
-- blackboard action packs, situation tasks), skill (ev "req2": job selector requests WITH the owner read from HumanActionSelectorImpl.Chara),
-- order (pawn order events), inventory (one-time dumps of the decision objects).
-- Where the AI state lives (found 2026-11-03): Character.AIDecisionMaker.DecisionModule (DecisionEvaluationModule).DecisionExecutor.ExecutingDecision
-- (DecisionEvaluationResult: Score, Target (AITarget*: Character / GameObject / _Position), ActionPackData (ActInterPackData: get_Path()), Decision (AIDecision: Priority,
-- Policy, Situation, Stage, ControlTags, Target flag, CooldownID, HumanCustomSkillIDs)); Character.AIBlackBoardController (ReqMain/SubActInterPackData, AIMetaController
-- (PawnOrderController, PawnOrderTargetController, PawnCoopUseGimmickData...), BBAggregate); Character.HateSystem._Ranking; Character.AISituationAgent
-- (_State, _CurrentTaskList, _CurrentSituations).
local W = rawget(_G, "DD2WATCH")
if not W or W.ai_loaded then return end
W.ai_loaded = true
local emit = W.emit

local ERRN = 0
local function try(f, ...)
    local ok, r = pcall(f, ...)
    if not ok then
        ERRN = ERRN + 1
        if ERRN <= 30 or ERRN % 100 == 0 then emit("errors", { where = "ai", n = ERRN, msg = tostring(r):sub(1, 300) }) end
        return nil
    end
    return r
end
local function round(v, n) local m = 10 ^ (n or 1) return math.floor(v * m + 0.5) / m end
local function go_name(c)
    local ok, n = pcall(function() return c:call("get_GameObject"):call("get_Name") end)
    return ok and n and tostring(n) or "?"
end
local function tname(o)
    local ok, n = pcall(function() return o:get_type_definition():get_full_name() end)
    return ok and n or "?"
end
local function path_of(ap)
    if not ap then return nil end
    for _, m in ipairs({ "get_Path", "get_URI" }) do
        local ok, v = pcall(function() return ap:call(m) end)
        if ok and v and v ~= "" then return tostring(v) end
    end
    return tname(ap)
end

local dumped = {}
local function dump_object(tag, o)
    if dumped[tag] or not o then return end
    dumped[tag] = true
    local rows, n = {}, 0
    local td = o:get_type_definition()
    local depth = 0
    while td and depth < 5 and n < 200 do
        for _, f in ipairs(td:get_fields()) do
            local fname = f:get_name()
            local ok, v = pcall(function() return o:get_field(fname) end)
            local val
            if ok then
                local tv = type(v)
                if tv == "number" or tv == "boolean" or tv == "string" then val = v
                elseif v ~= nil then val = "<" .. tname(v) .. ">" end
            end
            rows[#rows + 1] = { td:get_full_name(), fname, f:get_type():get_full_name(), val }
            n = n + 1
        end
        td = td:get_parent_type(); depth = depth + 1
    end
    emit("inventory", { tag = tag, type = tname(o), fields = rows })
end

local function target_desc(t)
    if not t then return nil end
    local d = { type = tname(t):gsub("^app%.", "") }
    pcall(function()
        local ch = t:get_field("<Character>k__BackingField")
        if ch then d.chara = go_name(ch) end
        local go = t:get_field("<GameObject>k__BackingField")
        if go and not d.chara then d.go = go:call("get_Name") end
        local p = t:get_field("_Position")
        if p then d.x, d.y, d.z = round(p.x), round(p.y), round(p.z) end
    end)
    return d
end

local function ints_of(arr)
    if not arr then return nil end
    local out = {}
    pcall(function()
        for _, v in ipairs(arr:get_elements()) do
            local n = type(v) == "number" and v or nil
            if not n then local ok, mv = pcall(function() return v:get_field("m_value") end) if ok and type(mv) == "number" then n = mv end end
            out[#out + 1] = n or tostring(v)
        end
    end)
    return out
end

local S = { last = {}, t = 0, t_slow = 0 }
local function modules(c)
    local dm = c:get_field("<AIDecisionMaker>k__BackingField")
    local mod = dm and dm:get_field("<DecisionModule>k__BackingField")
    local ex = mod and mod:get_field("<DecisionExecutor>k__BackingField")
    local bb = c:get_field("<AIBlackBoardController>k__BackingField")
    return dm, mod, ex, bb
end

local function poll_actor(kind, c)
    local who = go_name(c)
    local dm, mod, ex, bb = modules(c)
    if not ex then return end
    local rec = S.last[who]
    if not rec then rec = {}; S.last[who] = rec end
    local r = ex:get_field("<ExecutingDecision>k__BackingField")
    local key
    if r then
        local ap = r:get_field("<ActionPackData>k__BackingField")
        local dec = r:get_field("<Decision>k__BackingField")
        local t = r:get_field("<Target>k__BackingField")
        local pack = path_of(ap)
        local td = target_desc(t)
        key = tostring(pack) .. "|" .. tostring(td and (td.chara or td.go or td.type))
        if key ~= rec.key then
            local row = { ev = "decision", who = who, kind = kind, pack = pack, score = round(r:get_field("<Score>k__BackingField") or 0, 3), target = td }
            if dec then
                row.stage = dec:get_field("<Stage>k__BackingField")
                row.policy = dec:get_field("<Policy>k__BackingField")
                row.situation = dec:get_field("<Situation>k__BackingField")
                row.ctl = dec:get_field("<ControlTags>k__BackingField")
                row.tflag = dec:get_field("<Target>k__BackingField")
                row.cd = dec:get_field("<CooldownID>k__BackingField")
                row.cdt = round(dec:get_field("<CooldownTime>k__BackingField") or 0, 2)
                row.skills = ints_of(dec:get_field("<HumanCustomSkillIDs>k__BackingField"))
                row.nskills = ints_of(dec:get_field("<HumanNormalSkillIDs>k__BackingField"))
                dump_object("ai:decision", dec); dump_object("ai:result", r)
                dump_object("ai:target:" .. tname(t), t)
            end
            if ap then dump_object("ai:actpack", ap) end
            row.status = ex:get_field("<DecisionStatus>k__BackingField")
            emit("ai", row)
        end
    elseif rec.key ~= nil then
        emit("ai", { ev = "idle", who = who, kind = kind })
    end
    rec.key = key
    -- blackboard requests (the pack the AI asked the action interface to run)
    if bb then
        local m = path_of(bb:get_field("<ReqMainActInterPackData>k__BackingField"))
        local s = path_of(bb:get_field("<ReqSubActInterPackData>k__BackingField"))
        local k2 = tostring(m) .. "|" .. tostring(s)
        if k2 ~= rec.req then
            rec.req = k2
            emit("ai", { ev = "req", who = who, kind = kind, main = m, sub = s })
        end
    end
end

local function poll_slow(kind, c)
    local who = go_name(c)
    local rec = S.last[who]
    if not rec then rec = {}; S.last[who] = rec end
    local dm, mod, ex, bb = modules(c)
    -- situation agent: state + current task / situation type names
    local ag = c:get_field("<AISituationAgent>k__BackingField")
    if ag then
        local names = {}
        pcall(function()
            local l = ag:get_field("_CurrentSituations")
            for i = 0, l:call("get_Count") - 1 do names[#names + 1] = tname(l:call("get_Item", i)):gsub("^.*%.", "") end
        end)
        local tasks = {}
        pcall(function()
            local l = ag:get_field("_CurrentTaskList")
            for i = 0, l:call("get_Count") - 1 do tasks[#tasks + 1] = tname(l:call("get_Item", i)):gsub("^.*%.", "") end
        end)
        local k = tostring(ag:get_field("_State")) .. "|" .. table.concat(names, ",") .. "|" .. table.concat(tasks, ",")
        if k ~= rec.sit then
            rec.sit = k
            emit("ai", { ev = "situation", who = who, kind = kind, state = ag:get_field("_State"), situations = names, tasks = tasks,
                area = ag:get_field("<CurrentArea>k__BackingField"), local_area = ag:get_field("<CurrentLocalArea>k__BackingField") })
            local l = ag:get_field("_CurrentTaskList")
            if l and l:call("get_Count") > 0 then dump_object("ai:task:" .. tname(l:call("get_Item", 0)), l:call("get_Item", 0)) end
        end
    end
    -- hate ranking (who this character wants to hit)
    local hs = c:get_field("<HateSystem>k__BackingField")
    if hs then
        local rank = hs:get_field("_Ranking")
        local top
        pcall(function()
            local e = rank:get_element(0)
            if e then
                dump_object("ai:hate", e)
                local go
                for _, fn in ipairs({ "_Target", "Target", "<Target>k__BackingField", "_Object", "<Object>k__BackingField", "_GameObject" }) do
                    local ok, v = pcall(function() return e:get_field(fn) end)
                    if ok and v then go = v break end
                end
                local nm = go and (pcall(function() return go:call("get_Name") end) and go:call("get_Name") or go_name(go))
                top = tostring(nm)
            end
        end)
        if top ~= rec.hate then
            rec.hate = top
            emit("ai", { ev = "hate", who = who, kind = kind, top = top })
        end
    end
    -- pawn order machinery, one dump per object type
    if kind == "pawn" and bb then
        local meta = bb:get_field("<AIMetaController>k__BackingField")
        if meta then
            local oc = meta:get_field("<CachedPawnOrderController>k__BackingField")
            if oc then dump_object("pawn:orderctrl", oc) end
            local ot = meta:get_field("<CachedPawnOrderTargetController>k__BackingField")
            if ot then dump_object("pawn:ordertarget", ot) end
            local cg = meta:get_field("_PawnCoopUseGimmickData")
            if cg then dump_object("pawn:coopgimmick", cg) end
        end
        local pers = bb:get_field("<Personality>k__BackingField")
        if pers then dump_object("pawn:personality", pers) end
        -- the pawn component itself (inclination, job, command state live here)
        pcall(function() local pw = c:call("get_Pawn") if pw then dump_object("pawn:pawn", pw) end end)
    end
end

local function party_and_enemies()
    local list = {}
    local cm = sdk.get_managed_singleton("app.CharacterManager")
    local p = cm and cm:call("get_ManualPlayer")
    if not p then return list end
    local pp = p:call("get_GameObject"):call("get_Transform"):call("get_Position")
    local pm = sdk.get_managed_singleton("app.PawnManager")
    local pl = pm and pm:call("get_PawnCharacterList")
    if pl then for i = 0, pl:call("get_Count") - 1 do list[#list + 1] = { "pawn", pl:call("get_Item", i) } end end
    local em = sdk.get_managed_singleton("app.EnemyManager")
    local el = em and em:get_field("_EnemyList")
    if el then
        for i = 0, el:call("get_Count") - 1 do
            local e = el:call("get_Item", i)
            local ok, d = pcall(function()
                local q = e:call("get_GameObject"):call("get_Transform"):call("get_Position")
                return math.sqrt((q.x - pp.x) ^ 2 + (q.z - pp.z) ^ 2)
            end)
            if ok and d <= 40 then list[#list + 1] = { "enemy", e } end
        end
    end
    return list
end

-- selector requests with the true owner
local hooked = W.hooked or {}
W.hooked = hooked
local function str_of(p)
    local ok, s = pcall(function() return sdk.to_managed_object(p):call("ToString") end)
    return ok and s and tostring(s) or nil
end
local function hook_selectors2()
    for j = 1, 10 do
        local tn = string.format("app.Job%02dActionSelector", j)
        local td = sdk.find_type_definition(tn)
        local m = td and td:get_method("requestActionImpl(System.String, System.UInt32)")
        if m and not hooked[tn .. ":req2"] and m:get_declaring_type():get_full_name() == tn then
            hooked[tn .. ":req2"] = true
            sdk.hook(m, function(args)
                try(function()
                    local sel = sdk.to_managed_object(args[2])
                    local ch = sel:get_field("<Chara>k__BackingField")
                    emit("skill", { ev = "req2", job = j, who = ch and go_name(ch) or "?", action = str_of(args[3]), layer = sdk.to_int64(args[4]) & 0xffffffff })
                end)
            end, function(r) return r end)
        end
    end
    -- the generic (non-job) selectors too: every selector type that declares requestActionImpl
end

-- pawn order / decision events
local function hook_events()
    local function h(tn, sig, label, decode)
        if hooked["ai:" .. tn .. sig] then return end
        local td = sdk.find_type_definition(tn)
        local m = td and td:get_method(sig)
        if not m then return end
        hooked["ai:" .. tn .. sig] = true
        sdk.hook(m, function(args)
            try(function()
                local row = { ev = label }
                local this = sdk.to_managed_object(args[2])
                pcall(function() local ch = this:get_field("<Character>k__BackingField") or this:get_field("_Chara") if ch then row.who = go_name(ch) end end)
                if decode then pcall(decode, row, args) end
                emit("order", row)
            end)
        end, function(r) return r end)
    end
    for _, e in ipairs({ { "onExitGo" }, { "onExitHelp" }, { "onEnterWait" }, { "onExitWait" }, { "onEnterExplore" }, { "onExitExplore" }, { "onExitHighFive" }, { "resetPawnOrder" } }) do
        h("app.PawnOrderController", e[1] .. "()", e[1])
    end
    h("app.PawnOrderTargetController", "addOrderTarget(app.VisionMarker)", "orderTarget")
    h("app.PawnOrderTargetController", "resetOrderTarget()", "resetOrderTarget")
    h("app.PawnOrderTargetController", "onStartGuidance(app.QuestDefine.ID, app.Character)", "guidance")
    h("app.DecisionExecutor", "forceEndDecision()", "forceEndDecision")
end

local started = false
re.on_frame(function()
    local now = os.clock()
    if not started then
        started = true
        try(hook_selectors2); try(hook_events)
        emit("meta", { ev = "ai_module_loaded" })
    end
    if now - S.t > 0.25 then
        S.t = now
        for _, a in ipairs(try(party_and_enemies) or {}) do try(poll_actor, a[1], a[2]) end
    end
    if now - S.t_slow > 1.0 then
        S.t_slow = now
        for _, a in ipairs(try(party_and_enemies) or {}) do try(poll_slow, a[1], a[2]) end
        try(hook_selectors2)
    end
end)
