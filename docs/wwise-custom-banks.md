# Custom Wwise banks in Dragon's Dogma 2 (verified in game 2026-11-03)

Used by the Wizard mod's Toll the Dead bell (Wizard/tools/make_bell.py, tools/build_pak.py `userdata_files`, src/.../toll.lua `ensure_bell`).

## Recipe
1. **Banks** go to `natives/stm/sound/wwise/<name>.sbnk.1.x64` in the mod pak (an `_es` with BKHD+HIRC, an `_m` with BKHD+DIDX+DATA; bank id in BKHD = FNV-1 of the
   lower-cased bank name, `wwise.fnv`; DATA media 16-byte aligned in the file). New names only: overriding a game bank crashes the game at start.
2. **The engine only loads a bank that comes out of deserialized game data.** `sdk.create_resource("via.simplewwise.BankResource", path)` returns an empty
   shell (resource+0x58 data pointer and +0x60 size stay 0, even for real game banks). So ship one `soundlib.SoundBankListData` `.user` per bank: clone
   `natives/stm/sound/resource/gm/gm51_228/gm51_228_banklistdata.user.3` (258 bytes: USR header, resource table, RSZ block with one holder string
   `@Sound/Wwise/<name>.sbnk`), swap the path, keep the RSZ data start 16-byte aligned in the FILE (pad after the instance table; header dataOffset at rsz+0x20 and
   userdataOffset at rsz+0x28), and use the SAME path length for the es and m banks (a shorter one parsed as an empty list; cause unknown).
   Load with `sdk.create_userdata("soundlib.SoundBankListData", "Sound/Resource/<dir>/<file>.user")`. Verify: `holder = list._BankList[0]`;
   `res = D.peek(holder:get_address()+0x10, "u64")`; `D.peek(res+0x58,"u64")` data pointer, `D.peek(res+0x60,"u64")` size (equals the bank file size).
   Loading is asynchronous (give it a second or more).
3. **Play** through your OWN sound container: `via.GameObject.create`, `createComponent(app.WwiseContainerApp)`, set `_UserDataList` (copy of the player's
   list via `GetRange(0,0)`) to [m bank list, es bank list, a `soundlib.SoundTriggerInfoListData` with `_Bank` = the es holder and a cloned
   `SoundTriggerInfo` whose `_TriggerId`/`_EventId` are yours], call `awake`, wait ~3 s, `reconstructTriggerInfoList`, then
   `comp:triggerLogLess(UInt32 trigger, via.vec3, via.GameObject)`. Build the container from an update tick, NOT inside a hook callback (the banks came out wrong
   there). PlayingId != 0 in a `SoundManager.postRequestInfo` hook (RequestInfo.Container == your component) means the engine accepted the post.
4. **Stop** with static `SoundManager.stopEvent(go, eventId, fadeMs)`.

## Sound content
* The sound object must keep its **parent actor-mixer id** (it supplies the output bus): a zeroed parent gives an accepted but silent post.
* **PCM WEM sources are silently dropped** (post accepted, no sound, no log). Use the game's own Vorbis media and change the result with sound properties:
  after the parent id the object has u8 bit vector, u8 cProps, cProps ids, cProps float values (07 Priority, 08 PriorityDistanceOffset ...). Add id 2 = Pitch in cents
  (pitch factor f -> 1200*log2(f); it changes speed too, like a tape). `wwise.build_layered_banks` does this and builds multi-layer events (ring + strike).
* Making wholly new audio needs a Wwise Vorbis encoder (not built).

## Introspection
`dd2_wwise` (banks / event / extract) + `wwise.resolve`; vgmstream-cli (Tools/vgmstream) decodes WEM. Finding a skill's sound: audition trigger ids from the
player's container (`AllTriggerInfoListData`) and read `SoundTriggerInfo._EventId`.
