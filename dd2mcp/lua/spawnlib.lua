-- Spawn / despawn of enemies (and NPCs, pawns: see the notes at the end) through the game's own GenerateManager. Load with
--   loadfile("D:/DD2Modding/dd2-mcp/dd2mcp/lua/spawnlib.lua")()   (run in phase "update")
-- Verified 2026-11-03 for monsters (category 3):
--   * a spawn is a REQUEST: the game's saved request data (getEnemyRequests) of a live enemy is cloned, moved next to the player and converted to a
--     container with convRequestSaveDataToContainer; requestGenerate registers it and the instance appears several seconds later (async prefab load);
--   * despawn = GenerateManager.execDestroyInstance(category, InstanceInfo, true, true, true). A request that is still registered REGENERATES the
--     instance (the game's repop logic), so a one-shot spawn needs option flags (see ONESHOT) or the request destroyed as well;
--   * NEVER call requestDestroy(container, ...) twice for one container: containers are pooled and the second call crashes the game.
local S = rawget(_G, "SPAWN_LIB") or {}
_G.SPAWN_LIB = S

S.CAT = { Arisen = 0, Pawn = 1, StrayPawn = 2, Monster = 3, Npc = 4, Gimmick = 5 }
-- app.GenerateManager.GenerateOprionFlags
S.FLAG = { DisableRegenerateOnUnload = 1, RemoveContextOnUnload = 2, NeverGenerateByDead = 4, DisableItemDrop = 8388608, DisableGetExp = 4194304 }

function S.gm() return sdk.get_managed_singleton("app.GenerateManager") end
function S.iim() return S.gm():get_field("_InstanceInfoManager") end

-- live instances of a character id in a category: list of app.InstanceInfo (Instance = GameObject, Chara, Container ...)
function S.instances(cat, cid)
    local l = S.iim():call("get_InstanceInfoList(app.GeneratorCategory, app.CharacterID)", cat, cid)
    return l
end

function S.list(cat, cid)
    local l = S.iim():call("getInstanceInfoList(app.GeneratorCategory, app.CharacterID)", cat, cid)
    local t = {}
    for i = 0, l:call("get_Count") - 1 do t[#t + 1] = l:call("get_Item", i) end
    return t
end

-- known templates: the character ids the game currently has requests for (so only enemies that are in the loaded world can be cloned)
function S.templates(cat)
    local l = S.gm():call("getEnemyRequests(app.GeneratorCategory, System.Boolean, System.Boolean)", cat or 3, true, false)
    local t = {}
    for i = 0, l:call("get_Count") - 1 do
        local d = l:call("get_Item", i)
        t[#t + 1] = { id = d:get_field("_CharaIDHash"), name = D.enum_name("app.CharacterID", d:get_field("_CharaIDHash")), data = d }
    end
    return t
end

-- Room under the category's generate limit (monsters: getGenerateLimit(3) = 30, measured 2026-11-18). A request sent with no room is
-- REJECTED (requestGenerate returns nil) and its leftover crashed the game later (InstanceInfoController.autoDestroyCharaInstance).
-- Do not make room by despawning right before a spawn either: that crashed in GenerateReserveDistributor.ReserveQueue.update.
function S.room(cat)
    local l = S.iim():call("getInstanceInfoList(app.GeneratorCategory)", cat or 3)
    return S.gm():call("getGenerateLimit(app.GeneratorCategory)", cat or 3) - (l and l:call("get_Count") or 0)
end

_G.SPAWN_KEEP = _G.SPAWN_KEEP or {}   -- clones/containers/controllers the game may hold: never collected

-- every reference-type field of an object and its parents
local function ref_fields(obj)
    local out, td = {}, obj:get_type_definition()
    while td and td:get_full_name() ~= "System.Object" do
        for _, f in ipairs(td:get_fields()) do
            if not f:is_static() and not f:get_type():is_value_type() then out[#out + 1] = f end
        end
        td = td:get_parent_type()
    end
    return out
end

-- spawn character id `cid` at (dx, dz) metres from the player (universal coordinates, y of the player). Returns the GenerateController
-- or nil + reason. Rewritten 2026-11-18 (memory safety): the world's own request is CLONED, never edited; the clone's shared references
-- are counted before any set_field; the via.Position fields are written as raw doubles (set_field of a struct overruns 16 bytes).
-- Works only for characters the CURRENT AREA has requests for (their data is loaded): changing a clone into another character (id +
-- its own _InitSetTypeHash) was accepted but crashed during generation when that character is not native to the area.
local OFF_POS, OFF_INITPOS = 0x30, 0x50   -- GenerateRequestCommonSaveData._Pos / _InitPos (via.Position = 3 doubles)
function S.spawn(cat, cid, dx, dz, flags)
    local gm = S.gm()
    if S.room(cat) < 1 then return nil, "no room under the generate limit (requests at the limit are rejected and crash later)" end
    local tpl
    for _, t in ipairs(S.templates(cat)) do if t.id == cid then tpl = t.data break end end
    if not tpl then return nil, "no saved request for that CharacterID in the loaded world" end
    local c = tpl:call("MemberwiseClone")
    c:add_ref_permanent(); SPAWN_KEEP[#SPAWN_KEEP + 1] = c
    for _, f in ipairs(ref_fields(c)) do
        local v = f:get_data(c)
        if type(v) == "userdata" and v.add_ref_permanent then v:add_ref_permanent() end
    end
    local u = D.player_pos()
    for _, off in ipairs({ OFF_POS, OFF_INITPOS }) do
        c:write_double(off, u.ux + dx); c:write_double(off + 8, u.uy); c:write_double(off + 16, u.uz + dz)
    end
    local rid = gm:call("getPoolRequestID")
    rid:add_ref_permanent()
    c:set_field("_ReqID", rid)
    c:set_field("_Option", flags or 0)
    local cont = gm:call("convRequestSaveDataToContainer(app.GenerateManager.GenerateRequestCharacterSaveData)", c)
    cont:add_ref_permanent(); SPAWN_KEEP[#SPAWN_KEEP + 1] = cont
    local ctrl = gm:call("requestGenerate(app.GenerateInfo.GenerateInfoContainer, app.GenerateManager.GenerateOprionFlags, System.Boolean, System.Boolean, System.Action`2<app.PrefabInstantiateResults,app.DummyArg_2_0_0_13>, System.Action`2<app.PrefabInstantiateResults,app.DummyArg_2_0_0_14>)",
        cont, flags or 0, false, false, nil, nil)
    if ctrl then ctrl:add_ref_permanent(); SPAWN_KEEP[#SPAWN_KEEP + 1] = ctrl end
    return ctrl, cont
end

function S.despawn(cat, info)
    return S.gm():call("execDestroyInstance(app.GeneratorCategory, app.InstanceInfo, System.Boolean, System.Boolean, System.Boolean)", cat, info, true, true, true)
end

return S
