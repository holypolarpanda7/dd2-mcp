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

-- spawn character id `cid` at (dx, dz) metres from the player (universal coordinates, y of the player). Returns the GenerateController.
function S.spawn(cat, cid, dx, dz, flags)
    local gm = S.gm()
    local tpl
    for _, t in ipairs(S.templates(cat)) do if t.id == cid then tpl = t.data break end end
    if not tpl then return nil, "no saved request for that CharacterID in the loaded world" end
    local u = D.player_pos()
    local p = tpl:get_field("_Pos")
    p.x, p.y, p.z = u.ux + dx, u.uy, u.uz + dz
    tpl:set_field("_Pos", p); tpl:set_field("_InitPos", p)
    tpl:set_field("_ReqID", gm:call("getPoolRequestID"))
    local cont = gm:call("convRequestSaveDataToContainer(app.GenerateManager.GenerateRequestCharacterSaveData)", tpl)
    local ctrl = gm:call("requestGenerate(app.GenerateInfo.GenerateInfoContainer, app.GenerateManager.GenerateOprionFlags, System.Boolean, System.Boolean, System.Action`2<app.PrefabInstantiateResults,app.DummyArg_2_0_0_13>, System.Action`2<app.PrefabInstantiateResults,app.DummyArg_2_0_0_14>)",
        cont, flags or 0, false, false, nil, nil)
    return ctrl, cont
end

function S.despawn(cat, info)
    return S.gm():call("execDestroyInstance(app.GeneratorCategory, app.InstanceInfo, System.Boolean, System.Boolean, System.Boolean)", cat, info, true, true, true)
end

return S
