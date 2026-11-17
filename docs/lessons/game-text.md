# Game text and checksummed files

## Base-game files flagged 0x1000 are checksum-verified at boot
873 of 1060 .msg files (pak entry flags 0x1002/0x1001) and a few tex/mesh files. Any byte change in your pak's copy, even an
identical rewrite stored plain or zstd, crashes DD2 about 1 s after the first frame, before any Lua runs. The game checks YOUR
entry's checksum: an identical file with the game's own entry checksum boots. The algorithm is unknown (not md5, sha*, blake*,
xxh*, murmur3, city or crc32 of raw or compressed data). DLC .msg (flag 0x2) and base .user files (0x1) override fine, and new
files are never checked. REFramework has no bypass for this. Its "conditional_jmp" errors are a different check.

## Add text: your own NEW .msg, registered the game's way
The engine adds .msg files with the component `via.gui.AdditionalMessage` (one field `v0_MessageList` = msg paths relative to
`natives/stm`). The DLC scenes carry one each. Recipe:
1. Put your entries in a NEW file, e.g. `natives/stm/<yourmod>/text/<yourmod>_text.msg.24` (take a base .msg layout, empty it).
2. Build a prefab with one GameObject + Transform + AdditionalMessage listing your msg (reetool `pfb-addmsg` from a small donor
   prefab), and a `.user` copied from the game's `appsystem/catalog/maincontents/userdata/democatalog.user.3` (app.DemoCatalog
   with one via.Prefab `_EventClonePrefab`) with the prefab path swapped for yours, at EQUAL LENGTH (REE-Lib's rewrite shifts an
   offset: byte-patch donors with equal-length strings).
3. At startup (the title screen is fine): `sdk.create_userdata("app.DemoCatalog", "<your .user path>")`. The prefab field is nil at
   first and filled ~1 s later. Then `set_Standby(true)`, wait for `get_Ready()`, `instantiate(via.vec3)` in UpdateBehavior. Keep
   everything referenced. `via.gui.message.get(guid)` now resolves your entries.
- Failed routes: `sdk.create_resource("via.gui.MessageResource")` loads but doesn't register; `create_holder("via.Prefab")` is
  nil; a .user loaded through `create_resource` + `create_holder` crashed ~8 s later even unchanged (use `create_userdata`).
- Fallback that works but conflicts with other mods: the changed .msg as a LOOSE file under `<game>/natives/...` with REFramework
  `LooseFileLoader_Enabled=true` (`dd2_deploy` and `dd2_package` support it).

## Writing text to the UI
`via.gui.Text.set_Message` takes a plain Lua string. Never reuse one kept managed string (crashed 3 times, see memory-safety.md).
Prefer names the game resolves itself from message data over writing text into widgets.
