-- dd2mcp eval bridge (REFramework autorun). Generic dev tool, not part of any mod.
-- Protocol, files under reframework/data/dd2mcp/:
--   in.lua       "--id:<n>" first line, optional "--phase:update" second line, then a Lua chunk
--   out.json     {id, ok, out, ret, err, phase}
-- "frame" commands run from re.on_frame (fine for reads). "update" commands run inside
-- UpdateBehavior: anything that changes game state (spawning, items) silently no-ops elsewhere.
-- Helpers live on the global D; all REFramework scripts share one Lua state, so mod globals
-- (e.g. Bastion's H) are reachable from commands.

local DATA = "dd2mcp/"
local CMD, RESULT = DATA .. "in.lua", DATA .. "out.json"

D = D or {}
D.version = "0.8.2"
local next_poll, queued = 0, nil  -- the last command id lives on D (D.last_id) so a reload cannot re-run it

function D.show(v)
    local t = type(v)
    if t == "userdata" then
        local ok, name = pcall(function() return v:get_type_definition():get_full_name() end)
        if ok then return "<" .. name .. ">" end
        return tostring(v)
    elseif t == "table" then
        local ok, s = pcall(json.dump_string, v)
        if ok then return s end
    end
    return tostring(v)
end

function D.methods(typename, pat)
    local td = sdk.find_type_definition(typename)
    if not td then return "no type " .. typename end
    local res = {}
    while td and td:get_full_name() ~= "System.Object" do
        for _, m in ipairs(td:get_methods()) do
            local name = m:get_name()
            if not pat or name:lower():find(pat:lower()) then
                local params = {}
                for _, p in ipairs(m:get_param_types() or {}) do params[#params + 1] = p:get_full_name() end
                local ret = m:get_return_type()
                res[#res + 1] = td:get_full_name() .. "::" .. name .. "(" .. table.concat(params, ", ") .. ")"
                    .. (ret and (" -> " .. ret:get_full_name()) or "")
            end
        end
        td = td:get_parent_type()
    end
    return res
end

-- Strict call by full signature. REFramework only resolves signatures written with ", "
-- between parameter types; obj:call() with an unresolved signature silently does nothing
-- (this hid ItemManager.getItem working, 2026-10-26). Raises instead.
function D.call(obj, sig, ...)
    local td = obj:get_type_definition()
    local m = td:get_method(sig) or td:get_method((sig:gsub(",%s*", ", ")))
    if not m then error("no method " .. td:get_full_name() .. "::" .. sig, 2) end
    return m:call(obj, ...)
end

function D.fields(typename, pat)
    local td = sdk.find_type_definition(typename)
    if not td then return "no type " .. typename end
    local res = {}
    while td and td:get_full_name() ~= "System.Object" do
        for _, f in ipairs(td:get_fields()) do
            local name = f:get_name()
            if not pat or name:lower():find(pat:lower()) then
                res[#res + 1] = td:get_full_name() .. "." .. name .. " : " .. f:get_type():get_full_name()
                    .. (f:is_static() and " (static)" or "")
            end
        end
        td = td:get_parent_type()
    end
    return res
end

function D.player()
    local cm = sdk.get_managed_singleton("app.CharacterManager")
    return cm and cm:call("get_ManualPlayer")
end

function D.player_pos()
    local p = D.player()
    if not p then return nil end
    local tf = p:call("get_GameObject"):call("get_Transform")
    local v, u = tf:call("get_Position"), tf:call("get_UniversalPosition")
    return { x = v.x, y = v.y, z = v.z, ux = u.x, uy = u.y, uz = u.z }
end

-- Item ids whose display name contains `pat` (case-insensitive).
function D.find_items(pat)
    local im = sdk.get_managed_singleton("app.ItemManager")
    local e = im:get_field("_ItemDataDict"):call("GetEnumerator")
    local res = {}
    while e:call("MoveNext") do
        local kv = e:call("get_Current")
        local p = kv:get_field("value")
        local ok, name = pcall(p.call, p, "get_Name")
        if ok and name and name:lower():find(pat:lower(), 1, true) then
            res[#res + 1] = { id = kv:get_field("key"), name = name, category = p:get_field("_Category") }
        end
    end
    table.sort(res, function(a, b) return a.id < b.id end)
    return res
end

-- Count of an item in a storage (default: the player's own storage, keyed by CharaID).
function D.item_count(item_id, storage)
    local im = sdk.get_managed_singleton("app.ItemManager")
    storage = storage or D.player():call("get_CharaID")
    local list = im:get_field("StorageDict"):call("get_Item", storage)
    local n = 0
    for i = 0, list:call("get_Count") - 1 do
        local p = list:call("get_Item", i):get_field("_Param")
        if p:get_field("_ItemId") == item_id then n = n + p:get_field("_Num") end
    end
    return n
end

-- Most recently changed storage entries across every storage (player, pawns, stash), newest
-- first. DD2 keeps no pickup log; StorageData._UpdateIndex rises with every add/stack change.
function D.recent_items(n, pat)
    local im = sdk.get_managed_singleton("app.ItemManager")
    local me = D.player():call("get_CharaID")
    local rows = {}
    local e = im:get_field("StorageDict"):call("GetEnumerator")
    while e:call("MoveNext") do
        local kv = e:call("get_Current")
        local key, list = kv:get_field("key"), kv:get_field("value")
        local owner = key == me and "PLAYER" or key == 65535 and "STASH" or ("other " .. tostring(key))
        for i = 0, list:call("get_Count") - 1 do
            local p = list:call("get_Item", i):get_field("_Param")
            local id = p:get_field("_ItemId")
            local d = im:call("getItemData(System.Int32)", id)
            local name = d and d:call("get_Name") or "?"
            if not pat or name:lower():find(pat:lower(), 1, true) then
                local sub = d and d:get_field("_SubCategory")
                rows[#rows + 1] = { upd = p:get_field("_UpdateIndex"), owner = owner, id = id, name = name,
                    num = p:get_field("_Num"), new = p:get_field("_IsNew"), key_item = d and d:call("get_IsKey"),
                    sub = sub and D.enum_name("app.ItemSubCategory", sub) or nil }
            end
        end
    end
    table.sort(rows, function(a, b) return a.upd > b.upd end)
    local res = {}
    for i = 1, math.min(n or 25, #rows) do res[i] = rows[i] end
    return res
end

local enum_cache = {}
function D.enum_name(typename, value)
    local c = enum_cache[typename]
    if not c then
        c = {}
        for _, f in ipairs(sdk.find_type_definition(typename):get_fields()) do
            if f:is_static() then
                local ok, v = pcall(f.get_data, f, nil)
                if ok and type(v) == "number" and c[v] == nil then c[v] = f:get_name() end
            end
        end
        enum_cache[typename] = c
    end
    return c[value] or tostring(value)
end

-- Every named value of an enum type, sorted by value: { {name=, value=}, ... }.
function D.enum(typename)
    local td = sdk.find_type_definition(typename)
    if not td then error("no type " .. typename, 2) end
    local res = {}
    for _, f in ipairs(td:get_fields()) do
        if f:is_static() and f:get_name() ~= "value__" then
            local ok, v = pcall(f.get_data, f, nil)
            if ok and type(v) == "number" then res[#res + 1] = { name = f:get_name(), value = v } end
        end
    end
    table.sort(res, function(a, b) return a.value < b.value end)
    return res
end

-- Weapon item params: id, name, WeaponID name, _Job mask (1 << JobEnum) and its vocations, stats.
function D.weapons()
    local im = sdk.get_managed_singleton("app.ItemManager")
    local e = im:get_field("_ItemDataDict"):call("GetEnumerator")
    local rows = {}
    while e:call("MoveNext") do
        local p = e:call("get_Current"):get_field("value")
        if p and p:get_type_definition():get_full_name() == "app.ItemWeaponParam" then
            local mask, jobs = p:get_field("_Job"), {}
            for j = 1, 15 do if mask & (1 << j) ~= 0 then jobs[#jobs + 1] = j end end
            local ok, name = pcall(p.call, p, "get_Name")
            rows[#rows + 1] = { id = p:get_field("_Id"), name = ok and name or "?",
                weapon = D.enum_name("app.WeaponID", p:get_field("_WeaponId")), job_mask = mask, jobs = jobs,
                equip = D.enum_name("app.ItemEquipCategory", p:get_field("_EquipCategory")), lv = p:get_field("_Lv"),
                phys = p:get_field("_PhysicalAttack"), magic = p:get_field("_MagicAttack"),
                model = p:get_field("_ModelId") }
        end
    end
    table.sort(rows, function(a, b) return a.id < b.id end)
    return rows
end

-- Native memory. REFramework Lua has no raw write: sdk.to_valuetype copies the bytes, so writes to it
-- go nowhere. Pokes go through the game's own System.Buffer.Memmove(byte&, byte&, nuint) from a
-- managed buffer (verified 2026-10-29 on the job-count global).
function D.addr(p) return tonumber(tostring(p):match("(%x+)$"), 16) end

function D.peek(addr, kind)
    local vt = sdk.to_valuetype(addr, "System.UInt64")
    if kind == "u64" then return vt:read_qword(0) end
    if kind == "u8" then return vt:read_byte(0) end
    return vt:read_dword(0)
end

local memmove
function D.poke32(addr, value)
    memmove = memmove or sdk.find_type_definition("System.Buffer"):get_method("Memmove(System.Byte, System.Byte, System.UIntPtr)")
    local src = sdk.create_managed_array("System.Int32", 1):add_ref()
    src[0] = value
    memmove:call(nil, sdk.to_ptr(addr), sdk.to_ptr(src:get_address() + 0x20), sdk.to_ptr(4))
    return D.peek(addr)
end

-- Native code address of a managed method ("app.JobContext", "get_JobIndex" or a full signature).
function D.fnaddr(typename, method)
    local td = sdk.find_type_definition(typename)
    local m = td and td:get_method(method)
    if not m then error("no method " .. typename .. "::" .. method, 2) end
    return D.addr(m:get_function())
end

-- DD2.exe load address: walk down from a method's code in 64 KB steps to the "MZ" header.
-- The image is mapped contiguously, so every probe stays inside it until the header.
function D.module_base()
    if D._base then return D._base end
    local a = D.fnaddr("app.JobContext", "get_JobIndex") & ~0xffff
    for _ = 1, 0x10000 do
        if D.peek(a, "u8") == 0x4d and (D.peek(a) & 0xffff) == 0x5a4d then
            local pe = D.peek(a + 0x3c)
            if pe > 0 and pe < 0x1000 and D.peek(a + pe) == 0x4550 then D._base = a return a end
        end
        a = a - 0x10000
    end
    error("module base not found")
end

-- Every method of the types listed in dd2mcp/typenames.txt as "rva<TAB>Type.method(params)" lines
-- into dd2mcp/methodmap.tsv (maps crash addresses back to methods; one export per game build).
function D.export_methodmap()
    local base, n, types = D.module_base(), 0, 0
    local f = assert(io.open(DATA .. "methodmap.tsv", "w"))
    for name in io.lines(DATA .. "typenames.txt") do
        local td = sdk.find_type_definition(name)
        if td then
            types = types + 1
            for _, m in ipairs(td:get_methods() or {}) do
                local p = D.addr(m:get_function())
                if p and p > base then
                    local ps = {}
                    for _, t in ipairs(m:get_param_types() or {}) do ps[#ps + 1] = t:get_full_name() end
                    f:write(string.format("%x\t%s.%s(%s)\n", p - base, name, m:get_name(), table.concat(ps, ", ")))
                    n = n + 1
                end
            end
        end
    end
    f:close()
    return { base = string.format("%x", base), types = types, methods = n }
end

-- Quiet test mode: removes world noise that breaks scripted tests. Support pawns walk up to the player
-- after every load and open a hire dialog that eats menu key presses (2026-10-29, user tip); their sales
-- approach is gated by AISituationTaskState_StrayPawn_SalesMoveToPlayer.canTalkGreet, forced false here.
-- Off by default (normal play untouched); the harness turns it on with the save guard.
D.quiet = D.quiet or { on = false }
if not D.quiet_hooked then
    -- canTalkGreet alone did not stop them (2026-10-29), so the sales states' per-frame updates are skipped too.
    local function skip_when_quiet(tn, name)
        local td = sdk.find_type_definition(tn)
        local m = td and td:get_method(name)
        if not m then log.error("[dd2mcp] quiet: no " .. tn .. "." .. name) return end
        sdk.hook(m, function() if D.quiet.on then return sdk.PreHookResult.SKIP_ORIGINAL end end, nil)
    end
    local greet = sdk.find_type_definition("app.AISituationTaskState_StrayPawn_SalesMoveToPlayer"):get_method("canTalkGreet")
    sdk.hook(greet, nil, function(ret) if D.quiet.on then return sdk.to_ptr(0) end return ret end)
    skip_when_quiet("app.AISituationTaskState_StrayPawn_SalesMoveToPlayer", "onUpdate")
    skip_when_quiet("app.AISituationTaskState_StrayPawn_TalkPlayer", "onUpdate")
    D.quiet_hooked = true
end

-- Game input actions, injected below the key bindings: app.CharacterInput.Action n = bit n of
-- UserInput.ButtonOnFlags (Skill1-4 = 7-10, Shift1 = 14 is the skill modifier, AttackS = 1, AttackL = 2, Jump = 3).
-- D.press(bits, frames) holds them for `frames` player-input updates, then releases (2026-10-31: the keyboard
-- binding of skills is a modifier chord, and 1-4 are pawn commands).
D.input = D.input or {}
if not D.input_hooked then
    sdk.hook(sdk.find_type_definition("app.PlayerInputProcessor"):get_method("update"), function(args)
        local p = D.input.press
        if not p then return end
        local i = sdk.to_managed_object(args[2]):call("get_Input")
        if p.frames > 0 then
            i:set_field("ButtonOnFlags", i:get_field("ButtonOnFlags") | p.mask)
            if p.first then
                i:set_field("ButtonTriggerFlags", i:get_field("ButtonTriggerFlags") | p.mask)
                p.first = false
            end
            p.frames = p.frames - 1
        else
            i:set_field("ButtonReleaseFlags", i:get_field("ButtonReleaseFlags") | p.mask)
            D.input.press = nil
        end
    end, function(r) return r end)
    D.input_hooked = true
end

-- Test god mode: the player takes no damage at all (HitController.damageProc skipped). HitController.IsInvincible
-- did not stop bandits or an ogre killing the test character (2026-10-31). Off by default; resets on restart.
D.godmode = D.godmode or { on = false }
if not D.god_hooked then
    sdk.hook(sdk.find_type_definition("app.HitController"):get_method("damageProc(app.DamageInfo)"), function(args)
        if not D.godmode.on then return end
        local p = D.player()
        local hc = p and p:call("get_Hit")
        if hc and sdk.to_int64(args[2]) == hc:get_address() then return sdk.PreHookResult.SKIP_ORIGINAL end
    end, function(r) return r end)
    D.god_hooked = true
end

-- A kill still got through damageProc alone (DieLoop at camp 049, 2026-10-31), so god mode also holds the game's
-- own HitController flags every frame.
function D.god(on)
    D.godmode.on = on ~= false
    if not D.god_frame then
        re.on_frame(function()
            if not D.godmode.on then return end
            local p = D.player()
            local hc = p and p:call("get_Hit")
            if hc then
                pcall(hc.call, hc, "set_IsNoDie", true)
                pcall(hc.call, hc, "set_IsDamageZero", true)
            end
        end)
        D.god_frame = true
    end
    return D.godmode.on
end

-- True while the player is dead (the "This tale has ended" screen: its first item loads the autosave).
function D.dead()
    local p = D.player()
    local hc = p and p:call("get_Hit")
    return hc ~= nil and hc:call("get_IsDie") == true
end

function D.press(bits, frames)
    local mask = 0
    for _, b in ipairs(bits) do mask = mask | (1 << b) end
    D.input.press = { mask = mask, frames = frames or 6, first = true }
end

-- Current behavior-tree node per layer of a character's action FSM (layer 0 full body, 1 upper body).
function D.nodes(chara)
    local fsm = (chara or D.player()):call("get_ActionManager"):get_field("Fsm")
    local r = {}
    for l = 0, 1 do
        local ok, n = pcall(fsm.call, fsm, "getCurrentNodeName(System.UInt32)", l)
        r[#r + 1] = ok and n and tostring(n) or "-"
    end
    return r
end

-- Characters to inspect: "player" or "pawn1".."pawnN" (PawnManager.PawnCharacterList order: party pawns in the world).
function D.party()
    local pm = sdk.get_managed_singleton("app.PawnManager")
    local res = { player = D.player() }
    local list = pm and pm:call("get_PawnCharacterList")
    if list then
        for i = 0, list:call("get_Count") - 1 do res["pawn" .. (i + 1)] = list:call("get_Item", i) end
    end
    return res
end

-- Vocation state of a character (default the player): job, exp per job, job bits.
function D.job(who)
    local party = D.party()
    local c = party[who or "player"]
    if not c then
        local have = {}
        for k in pairs(party) do have[#have + 1] = k end
        table.sort(have)
        error("no character " .. tostring(who) .. " (present: " .. table.concat(have, ", ") .. ")", 0)
    end
    local jc = c:call("get_Human"):call("get_JobContext")
    local exp, ce = {}, jc:get_field("CulmativeExp")
    for i = 0, ce:get_size() - 1 do exp[i + 1] = ce:get_element(i):get_field("m_value") end  -- boxed Int32
    local job = jc:get_field("CurrentJob")
    return { who = who or "player", chara = c:call("get_CharaIDString"), job = job,
        job_name = D.enum_name("app.Character.JobEnum", job), job_index = jc:call("get_JobIndex"), exp = exp,
        changed_bits = jc:get_field("ChangedJobBits"), qualified_bits = jc:get_field("QualifiedJobBits") }
end

-- Save guard for the test harness. Slot = unit*5 + type (Auto 0, Manual 1, Back 2, Clear 3,
-- AddAuto 4); 3 units. Disarmed it only logs, so normal play is untouched. Armed it refuses
-- every save outside `units`, and every autosave while `autosave` is false.
D.saveguard = D.saveguard or { armed = false, autosave = true, units = { [0] = true }, log = {} }
local SG = D.saveguard

function SG.arm(opts)
    opts = opts or {}
    SG.armed = true
    SG.autosave = opts.autosave == true
    SG.units = {}
    for _, u in ipairs(opts.units or { 0 }) do SG.units[u] = true end
    return SG.status()
end

function SG.disarm() SG.armed = false; SG.autosave = true; return SG.status() end

function SG.status()
    local units = {}
    for u in pairs(SG.units) do units[#units + 1] = u end
    table.sort(units)
    return { armed = SG.armed, autosave = SG.autosave, units = units, log = SG.log }
end

-- Returns true to let the save through.
function SG.check(kind, slot)
    local allowed = true
    if SG.armed and slot ~= -1 then
        local unit = slot and slot >= 0 and (slot // 5) or nil
        -- The game autosaves through requestSave(slot) too, so judge by slot type, not method.
        local auto = kind == "auto" or (slot and slot >= 0 and (slot % 5 == 0 or slot % 5 == 4))
        allowed = unit ~= nil and SG.units[unit] == true and (SG.autosave or not auto)
    end
    table.insert(SG.log, 1, { t = os.date("%H:%M:%S"), kind = kind, slot = slot, allowed = allowed })
    SG.log[21] = nil
    return allowed
end

local function auto_slot()
    local g = sdk.get_managed_singleton("app.GameSystem")
    return g and g:call("get_CachedSlotAuto")
end

if not D.saveguard_hooked then
    local sdm = sdk.find_type_definition("app.SaveDataManager")
    local function guard(sig, kind, slot_of)
        local m = sdm:get_method(sig)
        if not m then log.error("[dd2mcp] saveguard: no method " .. sig) return end
        sdk.hook(m, function(args)
            local ok, allowed = pcall(function() return SG.check(kind, slot_of(args)) end)
            if ok and not allowed then return sdk.PreHookResult.SKIP_ORIGINAL end
        end, function(r) return r end)
    end
    local function arg_slot(args) return ((sdk.to_int64(args[3]) & 0xFFFFFFFF) ~ 0x80000000) - 0x80000000 end
    guard("requestSaveAuto(app.SaveDataManager.SaveLoadRequestArgs, System.UInt32[])", "auto", function() return auto_slot() end)
    guard("requestSaveManual(System.Int32, app.SaveDataManager.SaveLoadRequestArgs, System.UInt32[])", "manual", arg_slot)
    guard("requestSaveSlot(System.Int32, app.SaveDataManager.SaveLoadRequestArgs, System.UInt32[])", "slot", arg_slot)
    guard("requestSave(System.Int32, app.SaveDataManager.SaveLoadRequestArgs, System.UInt32[])", "raw", arg_slot)
    D.saveguard_hooked = true
end

local function run(id, body, phase)
    local out = {}
    local old_print = print
    print = function(...)
        local parts = {}
        for i = 1, select("#", ...) do parts[#parts + 1] = D.show((select(i, ...))) end
        out[#out + 1] = table.concat(parts, "\t")
    end
    local res = { id = id, ok = false, phase = phase }
    local fn, cerr = load(body, "=dd2mcp", "t")
    if not fn then
        res.err = "compile: " .. tostring(cerr)
    else
        local packed = table.pack(xpcall(fn, debug.traceback))
        res.ok = packed[1]
        if packed[1] then
            local rets = {}
            for i = 2, packed.n do rets[#rets + 1] = D.show(packed[i]) end
            res.ret = rets
        else
            res.err = tostring(packed[2])
        end
    end
    print = old_print
    res.out = out
    fs.write(RESULT, json.dump_string(res))
end

function D.poll()
    local now = os.clock()
    if now < next_poll then return end
    next_poll = now + 0.03
    local ok, s = pcall(fs.read, CMD)
    if not ok or not s or #s == 0 then return end
    local id = s:match("^%-%-id:(%S+)")
    if not id or id == D.last_id then return end
    D.last_id = id
    if s:match("\n%-%-phase:update") then
        queued = { id = id, body = s }
    else
        run(id, s, "frame")
    end
end

function D.update()
    if queued then
        local q = queued
        queued = nil
        run(q.id, q.body, "update")
    end
end

-- Registered once per game run; they call through D, so reloading this file swaps the logic.
if not D.hooked_v2 then
    re.on_frame(function()
        local ok, err = pcall(D.poll)
        if not ok then log.error("[dd2mcp] " .. tostring(err)) end
    end)
    re.on_pre_application_entry("UpdateBehavior", function()
        local ok, err = pcall(D.update)
        if not ok then log.error("[dd2mcp] " .. tostring(err)) end
    end)
    D.hooked_v2 = true
end
log.info("[dd2mcp] bridge " .. D.version .. " loaded")
