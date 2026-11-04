-- dd2mcp watcher, talk module (2026-11-03): read-only. Stream `talk`: dialog play requests / ends (event id, characters involved), choices, pawn monologues. Needs dd2mcp_watch.lua.
local W = rawget(_G, "DD2WATCH")
if not W or W.talk_loaded then return end
W.talk_loaded = true
local emit = W.emit
local hooked = W.hooked or {}
W.hooked = hooked
local ERRN = 0
local function try(f, ...)
    local ok, r = pcall(f, ...)
    if not ok then ERRN = ERRN + 1 if ERRN <= 20 or ERRN % 100 == 0 then emit("errors", { where = "talk", n = ERRN, msg = tostring(r):sub(1, 300) }) end end
    return ok and r or nil
end
local function i32(a) return sdk.to_int64(a) & 0xffffffff end
local function name_of(c)
    local ok, n = pcall(function() return c:call("get_GameObject"):call("get_Name") end)
    return ok and n and tostring(n) or "?"
end
local function str_of(p)
    local ok, s = pcall(function() return sdk.to_managed_object(p):call("ToString") end)
    return ok and s and tostring(s) or nil
end
local function chars_of(list)
    local out = {}
    pcall(function()
        local l = sdk.to_managed_object(list)
        for i = 0, l:call("get_Count") - 1 do
            local v = l:call("get_Item", i)
            out[#out + 1] = type(v) == "number" and v or (pcall(function() return v:get_GameObject() end) and name_of(v) or tostring(v))
        end
    end)
    return out
end
local function hook(tn, sig, fn)
    local key = "talk:" .. tn .. sig
    if hooked[key] then return end
    local td = sdk.find_type_definition(tn)
    local m = td and td:get_method(sig)
    if not m then emit("talk", { ev = "unresolved", spec = tn .. "::" .. sig }) return end
    hooked[key] = true
    sdk.hook(m, function(args) try(fn, args) end, function(r) return r end)
end
local TM = "app.TalkEventManager"
hook(TM, "addPlayRequest(System.Object, app.TalkEventDefine.ID, System.Collections.Generic.List`1<app.CharacterID>, app.TalkEventDefine.EventType)", function(args)
    emit("talk", { ev = "play", id = i32(args[4]), event_type = i32(args[6]), chars = chars_of(args[5]) })
end)
hook(TM, "endPlayRequest(app.TalkEventDefine.ID, app.TalkEventDefine.PlayErrorType, app.TalkEventDynamicResource.ActionArg_Finish)", function(args)
    emit("talk", { ev = "end", id = i32(args[3]), error = i32(args[4]) })
end)
hook(TM, "addChoice(System.Guid)", function(args) emit("talk", { ev = "choice" }) end)
hook(TM, "requestStop(System.Object, app.TalkEventDefine.ID, System.Action`1<app.AppEventDefine.CommandEntityArg_Processed>)", function(args)
    emit("talk", { ev = "stop", id = i32(args[4]) })
end)
hook(TM, "requestStopAll()", function() emit("talk", { ev = "stopAll" }) end)
local TK = "app.TalkManager"
hook(TK, "notifyOnStartPawnTalkMonologue(app.PawnTalkMonologueCategoryData, app.PawnTalkMonologueSegmentData, app.Character, System.Int32, app.AITarget)", function(args)
    local ch = sdk.to_managed_object(args[5])
    emit("talk", { ev = "pawn_monologue_start", who = ch and name_of(ch), n = i32(args[6]), category = str_of(args[3]), segment = str_of(args[4]) })
end)
hook(TK, "notifyOnEndPawnTalkMonologue(app.PawnTalkMonologueCategoryData, app.PawnTalkMonologueSegmentData, app.Character, System.Int32, System.Boolean)", function(args)
    local ch = sdk.to_managed_object(args[5])
    emit("talk", { ev = "pawn_monologue_end", who = ch and name_of(ch), n = i32(args[6]) })
end)
hook(TK, "notifyOnStartPawnTalk(app.PawnTalkMonologueCategoryData, app.PawnTalkMonologueSegmentData)", function(args)
    emit("talk", { ev = "pawn_talk_start", category = str_of(args[3]), segment = str_of(args[4]) })
end)
hook(TK, "notifyOnEndPawnTalk(app.PawnTalkMonologueCategoryData, app.PawnTalkMonologueSegmentData, System.Boolean)", function(args)
    emit("talk", { ev = "pawn_talk_end" })
end)
emit("meta", { ev = "talk_module_loaded" })
