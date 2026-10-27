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
D.version = "0.3.1"
local last_id, next_poll, queued = nil, 0, nil

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
    if not id or id == last_id then return end
    last_id = id
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
