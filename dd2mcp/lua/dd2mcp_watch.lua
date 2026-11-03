-- dd2mcp watcher (2026-11-03): READ-ONLY observation of normal play. It never changes game state; it only reads and appends JSON lines to
--   <game>/reframework/data/dd2mcp/watch_<session>_<stream>.jsonl
-- Streams: meta, state (1 Hz heartbeat: player pos/nodes/job/counts), node (action-node changes of the player, pawns and enemies nearby, ~10 Hz poll),
-- snap (2 s snapshots of every actor in range), key (keyboard/mouse presses), damage (every HitController.calcDamageValue: victim, damage, type, element),
-- skill (job selector requestActionImpl / getCustomSkillAction: skill id -> action name, who asked), gui (GUI windows enabled / disabled), item (inventory
-- changes), inventory (one-time field dumps of the player / pawn / each enemy type: where the AI state lives), hook (extra hooks you add in
-- dd2mcp/watch_hooks.txt, one "type::method signature" per line, re-read every 5 s), errors.
-- Safety: everything is pcall-wrapped, hooks are pass-through, each stream stops after 400 errors or 80 MB. Installed by dd2_watch (dd2-mcp).
local W = rawget(_G, "DD2WATCH")
if W and W.loaded then return end
W = W or {}
_G.DD2WATCH = W
W.loaded = true
W.version = "1.0"

local DATA = "dd2mcp/"
local session = os.date("%Y%m%d_%H%M%S")
W.session = session
W.counts, W.errn, W.bytes, W.stopped = {}, {}, {}, {}
local buf = {}
local MAX_BYTES, MAX_ERR = 80 * 1024 * 1024, 400

local function emit(stream, tbl)
    if W.stopped[stream] then return end
    tbl.t = tbl.t or math.floor(os.clock() * 1000) / 1000
    tbl.wall = tbl.wall or os.time()
    local ok, s = pcall(json.dump_string, tbl)
    if not ok then return end
    local b = buf[stream]
    if not b then b = {}; buf[stream] = b end
    b[#b + 1] = s
    W.counts[stream] = (W.counts[stream] or 0) + 1
end
W.emit = emit

local function err(where, e)
    local n = (W.errn[where] or 0) + 1
    W.errn[where] = n
    if n == 1 or n % 50 == 0 then emit("errors", { where = where, n = n, msg = tostring(e):sub(1, 300) }) end
    if n >= MAX_ERR then W.stopped[where] = true end
end

local function flush()
    for stream, b in pairs(buf) do
        if #b > 0 then
            local text = table.concat(b, "\n") .. "\n"
            buf[stream] = {}
            local f = io.open(DATA .. "watch_" .. session .. "_" .. stream .. ".jsonl", "a")
            if f then
                f:write(text); f:close()
                W.bytes[stream] = (W.bytes[stream] or 0) + #text
                if W.bytes[stream] > MAX_BYTES then W.stopped[stream] = true end
            end
        end
    end
end

local function try(where, f, ...)
    if W.stopped[where] then return nil end
    local ok, r = pcall(f, ...)
    if not ok then err(where, r) return nil end
    return r
end

-- ===== helpers ===========================================================================================================================
local function singleton(n) return sdk.get_managed_singleton(n) end
local function round(v, n) local m = 10 ^ (n or 1) return math.floor(v * m + 0.5) / m end

local function go_name(c)
    local ok, n = pcall(function() return c:call("get_GameObject"):call("get_Name") end)
    return ok and n and tostring(n) or "?"
end

local function player_char()
    local cm = singleton("app.CharacterManager")
    return cm and cm:call("get_ManualPlayer")
end

local function tf_of(c) return c:call("get_GameObject"):call("get_Transform") end

local function nodes_of(c)
    local fsm = c:call("get_ActionManager"):get_field("Fsm")
    local r = {}
    for l = 0, 1 do
        local ok, n = pcall(fsm.call, fsm, "getCurrentNodeName(System.UInt32)", l)
        r[#r + 1] = ok and n and tostring(n) or "-"
    end
    return r[1], r[2]
end

local HP_GETTERS = { "get_Hp", "get_HitPoint", "get_CurrentHp", "get_HpRate", "get_HealthRate" }
local hp_getter_for = {}
local function hp_of(c)
    local tn = c:get_type_definition():get_full_name()
    local g = hp_getter_for[tn]
    if g == nil then
        g = false
        for _, name in ipairs(HP_GETTERS) do
            local ok, v = pcall(c.call, c, name)
            if ok and type(v) == "number" then g = name break end
        end
        hp_getter_for[tn] = g
    end
    if not g then return nil end
    local ok, v = pcall(c.call, c, g)
    return ok and type(v) == "number" and round(v, 1) or nil
end

local function distance(a, b) return math.sqrt((a.x - b.x) ^ 2 + (a.z - b.z) ^ 2) end

-- actors: the player, the pawns of the world, and enemies within RANGE metres (horizontal) of the player
local RANGE = 60
local function actors()
    local list = {}
    local p = player_char()
    if not p then return list, nil end
    local pp = tf_of(p):call("get_Position")
    list[#list + 1] = { c = p, kind = "player", name = go_name(p), d = 0 }
    local pm = singleton("app.PawnManager")
    local pl = pm and pm:call("get_PawnCharacterList")
    if pl then
        for i = 0, pl:call("get_Count") - 1 do
            local c = pl:call("get_Item", i)
            local ok, d = pcall(function() return distance(tf_of(c):call("get_Position"), pp) end)
            list[#list + 1] = { c = c, kind = "pawn", name = go_name(c), d = ok and d or -1 }
        end
    end
    local em = singleton("app.EnemyManager")
    local el = em and em:get_field("_EnemyList")
    if el then
        for i = 0, el:call("get_Count") - 1 do
            local e = el:call("get_Item", i)
            local ok, d = pcall(function() return distance(tf_of(e):call("get_Position"), pp) end)
            if ok and d <= RANGE then list[#list + 1] = { c = e, kind = "enemy", name = go_name(e), d = d } end
        end
    end
    return list, pp
end

-- ===== one-time field dumps (where does the AI / state live?) ==============================================================================
local dumped = {}
local function dump_object(tag, o)
    if dumped[tag] then return end
    dumped[tag] = true
    local rows, n = {}, 0
    local td = o:get_type_definition()
    local depth = 0
    while td and depth < 6 and n < 260 do
        for _, f in ipairs(td:get_fields()) do
            local fname = f:get_name()
            local ft = f:get_type()
            local ftn = ft and ft:get_full_name() or "?"
            local val
            local ok, v = pcall(function() return o:get_field(fname) end)
            if ok then
                local tv = type(v)
                if tv == "number" or tv == "boolean" or tv == "string" then val = v
                elseif v ~= nil then
                    local ok2, tn = pcall(function() return v:get_type_definition():get_full_name() end)
                    val = ok2 and ("<" .. tn .. ">") or "<obj>"
                end
            end
            rows[#rows + 1] = { td:get_full_name(), fname, ftn, val }
            n = n + 1
        end
        td = td:get_parent_type(); depth = depth + 1
    end
    emit("inventory", { tag = tag, type = o:get_type_definition():get_full_name(), fields = rows })
end

local function dump_actor(a)
    local c = a.c
    local tag = a.kind .. ":" .. (a.kind == "enemy" and a.name:gsub("_%d+$", "") or a.name)
    try("inventory", function()
        dump_object(tag, c)
        local h = nil
        pcall(function() h = c:call("get_Human") end)
        if h then dump_object(tag .. ":human", h) end
        local go = c:call("get_GameObject")
        -- the component types on the object (what systems this character is made of)
        local ok, comps = pcall(function() return go:call("get_Components") end)
        if ok and comps and not dumped[tag .. ":components"] then
            dumped[tag .. ":components"] = true
            local names = {}
            local ok2 = pcall(function() for _, k in ipairs(comps:get_elements()) do names[#names + 1] = k:get_type_definition():get_full_name() end end)
            if ok2 then emit("inventory", { tag = tag .. ":components", components = names }) end
        end
    end)
end

-- ===== polls ============================================================================================================================
local S = { last = {}, seen = {}, keys = {}, gui = {}, items = {}, t_state = 0, t_snap = 0, t_gui = 0, t_item = 0, t_hooks = 0, t_node = 0 }
W.S = S

local KEYS = { { 0x01, "LMB" }, { 0x02, "RMB" }, { 0x04, "MMB" }, { 0x57, "W" }, { 0x41, "A" }, { 0x53, "S" }, { 0x44, "D" }, { 0x10, "Shift" }, { 0x11, "Ctrl" },
    { 0x12, "Alt" }, { 0x20, "Space" }, { 0x45, "E" }, { 0x46, "F" }, { 0x47, "G" }, { 0x51, "Q" }, { 0x52, "R" }, { 0x58, "X" }, { 0x43, "C" }, { 0x56, "V" },
    { 0x5A, "Z" }, { 0x09, "Tab" }, { 0x1B, "Esc" }, { 0x31, "1" }, { 0x32, "2" }, { 0x33, "3" }, { 0x34, "4" }, { 0x25, "Left" }, { 0x26, "Up" },
    { 0x27, "Right" }, { 0x28, "Down" }, { 0x4D, "M" }, { 0x49, "I" }, { 0x48, "H" }, { 0x4C, "L" }, { 0x54, "T" }, { 0x42, "B" } }

local function poll_keys(now, pnode)
    for _, k in ipairs(KEYS) do
        local ok, down = pcall(function() return reframework:is_key_down(k[1]) end)
        if ok then
            down = down and true or false
            if S.keys[k[1]] ~= down then
                if S.keys[k[1]] ~= nil or down then emit("key", { k = k[2], down = down, node = pnode }) end
                S.keys[k[1]] = down
            end
        end
    end
end

local function poll_nodes(now)
    local list, pp = actors()
    local present = {}
    local pnode
    for _, a in ipairs(list) do
        present[a.name] = true
        local n0, n1 = nodes_of(a.c)
        if a.kind == "player" then pnode = n0 .. "|" .. n1 end
        local last = S.last[a.name]
        if not S.seen[a.name] then
            S.seen[a.name] = true
            emit("node", { ev = "appear", who = a.name, kind = a.kind, d = round(a.d), n0 = n0, n1 = n1 })
            dump_actor(a)
        elseif not last or last[1] ~= n0 or last[2] ~= n1 then
            emit("node", { ev = "node", who = a.name, kind = a.kind, d = round(a.d), n0 = n0, n1 = n1, from0 = last and last[1], from1 = last and last[2] })
        end
        S.last[a.name] = { n0, n1 }
    end
    for name in pairs(S.seen) do
        if not present[name] then
            S.seen[name] = nil; S.last[name] = nil
            emit("node", { ev = "gone", who = name })
        end
    end
    S.list, S.pnode = list, pnode
end

local function snapshot(list)
    for _, a in ipairs(list) do
        local row = { who = a.name, kind = a.kind, d = round(a.d) }
        local ok, u = pcall(function() return tf_of(a.c):call("get_UniversalPosition") end)
        if ok and u then row.x, row.y, row.z = round(u.x), round(u.y), round(u.z) end
        row.hp = hp_of(a.c)
        emit("snap", row)
    end
end

local function heartbeat(list, pnode)
    local row = { node = pnode, actors = #list }
    local p = player_char()
    if p then
        local ok, u = pcall(function() return tf_of(p):call("get_UniversalPosition") end)
        if ok and u then row.x, row.y, row.z = round(u.x), round(u.y), round(u.z) end
        row.hp = hp_of(p)
        local okj, job = pcall(function() return p:call("get_Human"):call("get_JobContext"):get_field("CurrentJob") end)
        if okj then row.job = job end
    end
    local tm = singleton("app.TimeManager")
    if tm then local ok, v = pcall(function() return tm:call("get_WholePlayTime") end) if ok then row.play = v end end
    local ok, pst = pcall(function() return singleton("app.PauseManager") ~= nil end)
    emit("state", row)
end

local function poll_gui(now)
    local scene = sdk.call_native_func(sdk.get_native_singleton("via.SceneManager"), sdk.find_type_definition("via.SceneManager"), "get_CurrentScene")
    local arr = scene:call("findComponents(System.Type)", sdk.typeof("via.gui.GUI")):get_elements()
    local now_on = {}
    for _, c in ipairs(arr) do
        local ok, en = pcall(function() return c:call("get_Enabled") end)
        if ok and en then now_on[go_name(c)] = true end
    end
    local opened, closed = {}, {}
    for n in pairs(now_on) do if not S.gui[n] then opened[#opened + 1] = n end end
    for n in pairs(S.gui) do if not now_on[n] then closed[#closed + 1] = n end end
    if S.gui_init and (#opened > 0 or #closed > 0) then table.sort(opened); table.sort(closed); emit("gui", { opened = opened, closed = closed, total = #arr }) end
    if not S.gui_init then local names = {} for n in pairs(now_on) do names[#names + 1] = n end table.sort(names) emit("gui", { initial = names }) S.gui_init = true end
    S.gui = now_on
end

local function poll_items()
    if not D or not D.recent_items then return end
    local rows = D.recent_items(8)
    for _, r in ipairs(rows) do
        local key = tostring(r.owner) .. ":" .. tostring(r.id)
        local prev = S.items[key]
        if prev == nil or prev.upd ~= r.upd or prev.num ~= r.num then
            if S.items_init then emit("item", { name = r.name, id = r.id, owner = r.owner, num = r.num, was = prev and prev.num, new = r.new, sub = r.sub }) end
            S.items[key] = { upd = r.upd, num = r.num }
        end
    end
    S.items_init = true
end

-- ===== hooks ==============================================================================================================================
local hooked = W.hooked or {}
W.hooked = hooked

local function str_of(p)
    local ok, s = pcall(function() return sdk.to_managed_object(p):call("ToString") end)
    return ok and s and tostring(s) or nil
end

-- who owns a job selector instance: addr -> "player" / pawn name, rebuilt every 2 s
local sel_owner, t_sel = {}, 0
local function rebuild_selectors()
    local m = {}
    local function add(c, who)
        local h = c:call("get_Human")
        local sels = h:call("get_HumanActionSelector"):get_field("Selectors")
        for i = 0, sels:call("get_Count") - 1 do
            local s = sels:call("get_Item", i)
            if s then m[s:get_address()] = who end
        end
    end
    local p = player_char()
    if p then pcall(add, p, "player") end
    local pm = singleton("app.PawnManager")
    local pl = pm and pm:call("get_PawnCharacterList")
    if pl then for i = 0, pl:call("get_Count") - 1 do local c = pl:call("get_Item", i) pcall(add, c, go_name(c)) end end
    sel_owner = m
end

local skill_last = {}
local function hook_selectors()
    for j = 1, 10 do
        local tname = string.format("app.Job%02dActionSelector", j)
        local td = sdk.find_type_definition(tname)
        if td then
            local m1 = td:get_method("requestActionImpl(System.String, System.UInt32)")
            if m1 and not hooked[tname .. ":req"] and m1:get_declaring_type():get_full_name() == tname then
                hooked[tname .. ":req"] = true
                sdk.hook(m1, function(args)
                    try("skill", function()
                        local name = str_of(args[3])
                        local layer = sdk.to_int64(args[4]) & 0xffffffff
                        local owner = sel_owner[sdk.to_managed_object(args[2]):get_address()] or "?"
                        emit("skill", { ev = "request", job = j, who = owner, action = name, layer = layer })
                    end)
                end, function(r) return r end)
            end
            local m2 = td:get_method("getCustomSkillAction(app.HumanCustomSkillID, app.LocomotionSpeedTypeEnum)")
            if m2 and not hooked[tname .. ":custom"] and m2:get_declaring_type():get_full_name() == tname then
                hooked[tname .. ":custom"] = true
                sdk.hook(m2, function(args)
                    local st = thread.get_hook_storage()
                    st.id = sdk.to_int64(args[3]) & 0xffffffff
                    st.this = args[2]
                end, function(r)
                    try("skill", function()
                        local st = thread.get_hook_storage()
                        local name = r and str_of(r)
                        local owner = st.this and sel_owner[sdk.to_managed_object(st.this):get_address()] or "?"
                        local key = tostring(owner) .. ":" .. tostring(st.id) .. ":" .. tostring(name)
                        local now = os.clock()
                        if not skill_last[key] or now - skill_last[key] > 2.0 then
                            skill_last[key] = now
                            emit("skill", { ev = "custom", job = j, who = owner, skill = st.id, action = name })
                        end
                    end)
                    return r
                end)
            end
        end
    end
end

local function hook_damage()
    if hooked.damage then return end
    local m = sdk.find_type_definition("app.HitController"):get_method("calcDamageValue")
    if not m then return end
    hooked.damage = true
    sdk.hook(m, function(args)
        local st = thread.get_hook_storage()
        st.this, st.di = args[2], args[3]
    end, function(r)
        try("damage", function()
            local st = thread.get_hook_storage()
            local hc = sdk.to_managed_object(st.this)
            local di = sdk.to_managed_object(st.di)
            local row = { victim = go_name(hc) }
            for _, f in ipairs({ "Damage", "DamageType", "DamageActType", "DmgReactionLv", "BalanceLevel", "HitEffType", "IsMagicAttack", "IsBlowAttack", "IsSlashAttack",
                "IsShootAttack", "IsNonMagicElementAttack", "LeanReactionRate", "BlownReactionRate", "StaminaDamage" }) do
                local ok, v = pcall(function() return di:get_field(f) end)
                if ok and (type(v) == "number" or type(v) == "boolean") then row[f] = type(v) == "number" and round(v, 2) or v end
            end
            local ok, el = pcall(function() return di:call("get_ElementType") end)
            if ok then row.element = el end
            emit("damage", row)
        end)
        return r
    end)
end

-- extra hooks from dd2mcp/watch_hooks.txt: "type::method signature" (the signature as D.methods prints it, e.g. app.HitController::damageProc(app.HitController.DamageInfo))
local function hook_generic(spec)
    local tname, sig = spec:match("^(.-)::(.+)$")
    if not tname then return end
    local td = sdk.find_type_definition(tname)
    local m = td and td:get_method(sig)
    if not m then emit("hook", { ev = "unresolved", spec = spec }) return end
    local ptypes = {}
    pcall(function() for _, t in ipairs(m:get_param_types()) do ptypes[#ptypes + 1] = t:get_full_name() end end)
    sdk.hook(m, function(args)
        try("hook", function()
            local row = { m = sig, args = {} }
            pcall(function() row.this = sdk.to_managed_object(args[2]):get_type_definition():get_full_name() end)
            for i, tn in ipairs(ptypes) do
                local a = args[i + 2]
                if tn == "System.String" then row.args[i] = str_of(a)
                elseif tn == "System.Boolean" then row.args[i] = (sdk.to_int64(a) & 1) == 1
                elseif tn:find("Int") or tn:find("Enum") then row.args[i] = sdk.to_int64(a) & 0xffffffff
                else row.args[i] = tn end
            end
            emit("hook", row)
        end)
    end, function(r) return r end)
    emit("hook", { ev = "installed", spec = spec })
end

local function poll_hook_file()
    local ok, s = pcall(fs.read, DATA .. "watch_hooks.txt")
    if not ok or not s then return end
    for line in s:gmatch("[^\r\n]+") do
        line = line:gsub("^%s+", ""):gsub("%s+$", "")
        if line ~= "" and line:sub(1, 1) ~= "#" and not hooked["g:" .. line] then
            hooked["g:" .. line] = true
            try("hook", hook_generic, line)
        end
    end
end

-- ===== main loop ============================================================================================================================
local started = false
local function tick()
    local now = os.clock()
    if not started then
        started = true
        emit("meta", { ev = "start", session = session, version = W.version, date = os.date("%Y-%m-%d %H:%M:%S") })
        try("hooks", hook_damage)
        try("hooks", hook_selectors)
    end
    if now - t_sel > 2.0 then t_sel = now; try("selectors", rebuild_selectors); try("hooks", hook_selectors) end
    if now - S.t_node > 0.1 then
        S.t_node = now
        try("node", poll_nodes, now)
        try("key", poll_keys, now, S.pnode)
    end
    local list = S.list
    if list and now - S.t_state > 1.0 then S.t_state = now; try("state", heartbeat, list, S.pnode) end
    if list and now - S.t_snap > 2.0 then S.t_snap = now; try("snap", snapshot, list) end
    if now - S.t_gui > 1.0 then S.t_gui = now; try("gui", poll_gui, now) end
    if now - S.t_item > 3.0 then S.t_item = now; try("item", poll_items) end
    if now - S.t_hooks > 5.0 then S.t_hooks = now; try("hook", poll_hook_file) end
    if now - (S.t_flush or 0) > 2.0 then S.t_flush = now; flush() end
end

re.on_frame(function()
    local ok, e = pcall(tick)
    if not ok then err("tick", e) end
end)

re.on_script_reset(function() flush() end)
