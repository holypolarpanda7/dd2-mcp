// reetool: headless helpers around REE-Lib / the REE Content Editor, used by the dd2-mcp tools dd2_mesh and dd2_efx.
//   mesh-import <in.glb> <out.mesh> [--colors] [--config DD2]   glTF -> DD2 .mesh (Content Editor importer + the fixes below)
//   mesh-info <file>                                              dump a .mesh header/buffers
//   mesh-rt <in> <out>                                            read + rewrite a .mesh (round-trip check)
//   mdf-info <file>                                               list the materials of an .mdf2
//   efx-info <file> [--all]                                       entries and attributes of an .efx (mesh/material paths shown)
//   mdf-scan <dir> [regex] [perMaster]                                       list .mdf2 materials (master shader + names) whose master matches the regex
//   efx-debug <file>                                              step through the efx read (header, strings, sections) printing file offsets
//   efx-normalize <in> <out>                                      strip the per-entry extra int32 this game build added, so REE-Lib can parse it
//   efx-scan <dir> [filter]                                       find .efx files (recursively) with an attribute type containing <filter>
// Env: REE_CE_DIR (Content Editor folder), REE_GAME_DIR (game install folder).
using System.Collections;
using System.Reflection;
using ReeLib;
using ContentEditor.App.FileLoaders;

var ceDir = Environment.GetEnvironmentVariable("REE_CE_DIR") ?? @"D:\DD2Modding\Tools\ContentEditor";
AppDomain.CurrentDomain.AssemblyResolve += (s, e) => {
    var p = Path.Combine(ceDir, new AssemblyName(e.Name).Name + ".dll");
    return File.Exists(p) ? Assembly.LoadFrom(p) : null;
};
if (args.Length == 0) { Console.Error.WriteLine("usage: reetool <mesh-import|mesh-info|mesh-rt|mdf-info|efx-info|efx-scan> ..."); return 2; }
try {
    return args[0] switch {
        "mesh-import" => MeshImport(args),
        "mesh-info" => MeshInfo(args[1]),
        "mesh-rt" => MeshRoundTrip(args),
        "mdf-info" => MdfInfo(args[1]),
        "mdf-scan" => MdfScan(args[1], args.Length > 2 ? args[2] : "", args.Length > 3 ? int.Parse(args[3]) : 3),
        "efx-debug" => EfxDebug(args[1]),
        "efx-normalize" => EfxNormalizeCmd(args[1], args[2]),
        "efx-info" => EfxInfo(args[1], args.Contains("--all")),
        "efx-scan" => EfxScan(args[1], args.Length > 2 ? args[2] : "", args.Contains("--from") ? int.Parse(args[Array.IndexOf(args, "--from") + 1]) : 0),
        _ => Fail("unknown command " + args[0]),
    };
} catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }

static int Fail(string m) { Console.Error.WriteLine(m); return 2; }

static int MeshImport(string[] a) {
    if (a.Length < 3) return Fail("usage: mesh-import in.glb out.mesh [--colors] [--config DD2]");
    var cfgName = a.Contains("--config") ? a[Array.IndexOf(a, "--config") + 1] : "DD2";
    var config = GameConfig.CreateFromRepository("dd2");
    config.GamePath = Environment.GetEnvironmentVariable("REE_GAME_DIR") ?? @"D:\SteamLibrary\steamapps\common\Dragons Dogma 2";
    var ws = new Workspace(config);
    using var fs = File.OpenRead(a[1]);
    var target = new CommonMeshResource(Path.GetFileNameWithoutExtension(a[2]), ws);
    var mesh = CommonMeshResource.ImportMeshFromGltf(target, fs, cfgName, ws, a[1], out var warnings);
    Console.WriteLine("warnings: " + warnings);
    if (mesh == null) return Fail("import returned null");
    Console.WriteLine($"verts={mesh.TotalVertexCount} tris={mesh.TotalTriangleCount} mats={string.Join(",", mesh.MaterialNames)}");
    // Fix 1: ReeLib writes header.bufferCount = 1 + StreamingBuffers?.Count, i.e. 0 when null, and the game's mesh loader crashes on that.
    mesh.StreamingBuffers ??= new List<ReeLib.Mesh.MeshBuffer>();
    // Fix 2 (opt-in --colors): the importer tests for attribute "COLOR" (glTF's is COLOR_0), so colors never import. VFX-style meshes
    // need a color stream; weapon-layout meshes must NOT have one. --colors adds opaque white.
    if (a.Contains("--colors") && (mesh.MeshBuffer.Colors == null || mesh.MeshBuffer.Colors.Length == 0)) {
        var white = new ReeLib.via.Color(); white.R = white.G = white.B = white.A = 255;
        mesh.MeshBuffer.Colors = Enumerable.Repeat(white, mesh.MeshBuffer.Positions.Length).ToArray();
        mesh.Header.flags |= ReeLib.Mesh.ContentFlags.HasVertexColor;
    }
    Console.WriteLine($"written={mesh.WriteTo(a[2])}");
    return 0;
}

static int MeshRoundTrip(string[] a) {
    var f = new MeshFile(new FileHandler(a[1], false));
    if (!f.Read()) return Fail("read failed");
    Console.WriteLine($"written={f.WriteTo(a[2])}");
    return 0;
}

static int MdfInfo(string path) {
    var f = new MdfFile(new FileHandler(path, false));
    if (!f.Read()) return Fail("read failed");
    foreach (var m in f.Materials) Console.WriteLine($"{m.Name} master={m.MasterMaterial} textures={m.Textures.Count} params={m.Parameters.Count}");
    return 0;
}

static int MdfScan(string dir, string regex, int perMaster) {
    var re = regex == "" ? null : new System.Text.RegularExpressions.Regex(regex, System.Text.RegularExpressions.RegexOptions.IgnoreCase);
    int files = 0, bad = 0;
    var masters = new SortedDictionary<string, List<string>>();
    foreach (var p in Directory.EnumerateFiles(dir, "*.mdf2.*", SearchOption.AllDirectories)) {
        files++;
        try {
            var f = new MdfFile(new FileHandler(p, false));
            if (!f.Read()) { bad++; continue; }
            foreach (var m in f.Materials) {
                if (re != null && !re.IsMatch(m.MasterMaterial ?? "")) continue;
                if (!masters.TryGetValue(m.MasterMaterial ?? "?", out var l)) masters[m.MasterMaterial ?? "?"] = l = new List<string>();
                l.Add($"{Path.GetRelativePath(dir, p)} [{f.Materials.Count} mat] {m.Name}");
            }
        } catch { bad++; }
    }
    foreach (var kv in masters) {
        Console.WriteLine($"{kv.Key}  ({kv.Value.Count} materials)");
        foreach (var x in kv.Value.Take(perMaster)) Console.WriteLine("    " + x);
    }
    Console.WriteLine($"scanned {files} mdf2 ({bad} unreadable), {masters.Count} distinct masters");
    return 0;
}

static int MeshInfo(string path) {
    var f = new MeshFile(new FileHandler(path, false));
    if (!f.Read()) return Fail("read failed");
    Dump("Header", f.Header, 0);
    foreach (var p in typeof(MeshFile).GetProperties()) {
        if (p.Name is "Header" or "FileHandler") continue;
        var v = p.GetValue(f);
        if (v is IList l) Console.WriteLine($"{p.Name}: list[{l.Count}]");
        else if (v == null) Console.WriteLine($"{p.Name}: null");
        else if (v.GetType().IsPrimitive || v is string) Console.WriteLine($"{p.Name}: {v}");
        else Dump(p.Name, v, 0);
    }
    return 0;
}

static void Dump(string name, object o, int d) {
    var pad = new string(' ', d * 2);
    Console.WriteLine($"{pad}{name}:");
    foreach (var m in o.GetType().GetMembers(BindingFlags.Public | BindingFlags.Instance).Where(m => m is FieldInfo || m is PropertyInfo)) {
        object v; try { v = m is FieldInfo fi ? fi.GetValue(o) : ((PropertyInfo)m).GetValue(o); } catch { continue; }
        if (v is IList l) Console.WriteLine($"{pad}  {m.Name}: list[{l.Count}]");
        else if (v == null || v.GetType().IsPrimitive || v is string || v.GetType().IsEnum) Console.WriteLine($"{pad}  {m.Name}: {v}");
        else if (d < 1) Dump(m.Name, v, d + 1); else Console.WriteLine($"{pad}  {m.Name}: {v}");
    }
}

static string Str(object o, string field) {
    var f = o.GetType().GetField(field);
    return f?.GetValue(o) as string;
}

static Workspace Ws() {
    if (State.Ws != null) return State.Ws;
    EfxFormat.RegisterVersionTables();
    var config = GameConfig.CreateFromRepository("dd2");
    config.GamePath = Environment.GetEnvironmentVariable("REE_GAME_DIR") ?? @"D:\SteamLibrary\steamapps\common\Dragons Dogma 2";
    return State.Ws = new Workspace(config);
}

static EfxFile LoadEfx(string path) {
    Ws(); // creating the workspace registers the game's efx attribute type tables
    var raw = File.ReadAllBytes(path);
    var data = EfxFormat.Normalize(raw, path);
    var fh = new FileHandler(new MemoryStream(data), path);
    var f = new EfxFile(fh);
    if (!f.Read()) throw new InvalidDataException("efx read failed: " + path);
    return f;
}

static int EfxInfo(string path, bool all) {
    var f = LoadEfx(path);
    Console.WriteLine($"version={f.Header.Version} entries={f.Entries.Count} actions={f.Actions.Count} expressionParams={f.ExpressionParameters.Count} bones={f.Bones.Count} groups={f.EffectGroups.Count}");
    foreach (var e in f.Entries) {
        Console.WriteLine($"entry #{e.index} '{e.name}' type={e.TypeAttribute?.GetType().Name} attrs={e.Attributes.Count} groups=[{string.Join(",", e.Groups)}]");
        foreach (var at in e.Attributes) {
            var mp = Str(at, "meshPath"); var mdf = Str(at, "mdfPath");
            var extra = mp != null || mdf != null ? $"  mesh='{mp}' mdf='{mdf}'" : "";
            if (all || extra != "") Console.WriteLine($"    {at.GetType().Name}{extra}");
        }
    }
    return 0;
}

static int EfxScan(string dir, string filter, int from) {
    int files = 0, hits = 0;
    var all = Directory.EnumerateFiles(dir, "*.efx.*", SearchOption.AllDirectories).OrderBy(x => x, StringComparer.Ordinal).ToList();
    for (int fi = from; fi < all.Count; fi++) {
        var p = all[fi];
        Console.WriteLine($"#{fi} {p}"); Console.Out.Flush(); // lets the caller resume after a process-killing file (stack overflow)
        files++;
        EfxFile f;
        try { f = LoadEfx(p); } catch (Exception ex) { Console.WriteLine($"! {p}: {ex.Message}"); continue; }
        var found = new List<string>();
        foreach (var e in f.Entries) foreach (var at in e.Attributes) {
            var n = at.GetType().Name;
            if (filter == "" || n.Contains(filter, StringComparison.OrdinalIgnoreCase)) {
                var mp = Str(at, "meshPath");
                found.Add(n + (mp != null ? $"[{mp} | {Str(at, "mdfPath")}]" : ""));
            }
        }
        if (found.Count > 0) { hits++; Console.WriteLine($"{p}\n   " + string.Join("\n   ", found.Distinct().Take(12))); }
    }
    Console.WriteLine($"scanned {files} efx (of {all.Count}), {hits} match '{filter}'");
    return 0;
}


static int EfxDebug(string path) {
    Ws();
    var fh = new FileHandler(path, false);
    Console.WriteLine($"file size={fh.FileSize()} FileVersion={fh.FileVersion}");
    var f = new EfxFile(fh);
    var h = f.Header;
    fh.Seek(0);
    h.Read(fh);
    Console.WriteLine($"header version={h.Version} dim={h.dimensionType} entries={h.entryCount} strLen={h.stringTableLength} actions={h.actionCount} fieldParams={h.fieldParameterCount} exprParams={h.expressionParameterCount} groups={h.effectGroupsCount} groupsLen={h.effectGroupsLength} bones={h.boneCount} boneAttr={h.boneAttributeEntryCount} propBind={h.propBindingIndexCount}  pos after header={fh.Tell()}");
    fh.Seek(0);
    var raw = new byte[Math.Min(0x80, (int)fh.FileSize())];
    fh.ReadBytes(raw);
    Console.WriteLine("first bytes: " + BitConverter.ToString(raw).Replace("-", " "));
    fh.Seek(0); h.Read(fh);
    var st = new ReeLib.Efx.Strings(h);
    st.Read(fh);
    Console.WriteLine($"strings start={st.Start} pos after strings={fh.Tell()} -> seek {st.Start + h.stringTableLength}");
    fh.Seek(st.Start + h.stringTableLength);
    Console.WriteLine($"expression params begin at {fh.Tell()} ({h.expressionParameterCount} items)");
    for (int i = 0; i < h.expressionParameterCount; i++) { var p = new ReeLib.Efx.EFXExpressionParameter(); p.name = st.ExpressionParameterNames[i]; p.Read(fh); Console.WriteLine($"  param {i} '{p.name}' ends at {fh.Tell()}"); }
    Console.WriteLine($"bones begin at {fh.Tell()}");
    { var keep = fh.Tell(); var l = new List<string>(); for (int i = 0; i < 24; i++) { l.Add(fh.Read<short>().ToString()); } fh.Seek(keep);
      Console.WriteLine("shorts from bones start: " + string.Join(" ", l)); }
    if (h.boneCount > 0) Console.WriteLine("(bones present: skipping not implemented)");
    for (int i = 0; i < h.boneAttributeEntryCount; i++) fh.Read<short>();
    Console.WriteLine($"actions begin at {fh.Tell()} (count {h.actionCount})");
    var at = fh.Tell();
    var ints = new List<string>();
    for (int i = 0; i < 40; i++) { if (fh.Tell() + 4 > fh.FileSize()) break; ints.Add(fh.Read<int>().ToString()); }
    Console.WriteLine($"next 40 int32 from {at}: " + string.Join(" ", ints));
    return 0;
}

static int EfxNormalizeCmd(string input, string output) {
    Ws();
    var raw = File.ReadAllBytes(input);
    var data = EfxFormat.Normalize(raw, input);
    File.WriteAllBytes(output, data);
    Console.WriteLine($"{raw.Length} -> {data.Length} bytes (removed {raw.Length - data.Length})");
    return 0;
}

static class State { public static Workspace Ws; }

// This game build (DD2 title update 3.2 / Expansion era) writes one extra int32 in front of every efx entry (equal to the entry's
// attribute count). REE-Lib 0.8.1 does not know it, so every entry after the first is misread. Attributes carry their own sizes
// (typeId, size, seqNum, payload), so entries can be walked without knowing attribute types.
static class EfxFormat {
    // REE-Lib has no attribute-type id table for EfxVersion.DD2New (the version this game build writes). Hypothesis under test: it uses
    // the ids of the neighbouring version (Pragmata); inject them into the remapper's private lookup before anything is read.
    public static void RegisterVersionTables() {
        var rem = typeof(ReeLib.Efx.EfxAttributeTypeRemapper);
        var f = rem.GetField("AttributeTypeIDs", BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static);
        var ids = f.GetValue(null) as System.Collections.IDictionary;
        int added = 0;
        foreach (System.Collections.DictionaryEntry kv in ids) {
            var perVersion = (Dictionary<ReeLib.Efx.EfxVersion, int>)kv.Value;
            var srcName = Environment.GetEnvironmentVariable("REETOOL_EFX_IDS") ?? "DD2";
            var source = Enum.Parse<ReeLib.Efx.EfxVersion>(srcName);
            if (perVersion.TryGetValue(source, out var id)) { perVersion[ReeLib.Efx.EfxVersion.DD2New] = id; added++; }
        }
        if (Environment.GetEnvironmentVariable("REETOOL_TRACE") == "1") Console.WriteLine($"registered {added} DD2New attribute ids");
    }

    public static byte[] Normalize(byte[] raw, string path) {
        var fh = new FileHandler(new MemoryStream(raw), path);
        var f = new EfxFile(fh);
        long entriesStart = EntriesStart(f, fh);
        var o = new MemoryStream();
        o.Write(raw, 0, (int)entriesStart);
        int pos = (int)entriesStart;
        int entryCount = f.Header.entryCount;
        for (int e = 0; e < entryCount; e++) {
            int extra = BitConverter.ToInt32(raw, pos);
            int attrCount = BitConverter.ToInt32(raw, pos + 16);
            if (Environment.GetEnvironmentVariable("REETOOL_TRACE") == "1") {
                var types = new List<string>(); int q = pos + 20;
                for (int a2 = 0; a2 < attrCount; a2++) { types.Add(BitConverter.ToInt32(raw, q) + ":" + BitConverter.ToInt32(raw, q + 8) + "/" + BitConverter.ToInt32(raw, q + 4)); q += 8 + BitConverter.ToInt32(raw, q + 4); }
                Console.WriteLine($"entry {e}: extra={extra} index={BitConverter.ToInt32(raw, pos + 4)} assign={BitConverter.ToInt32(raw, pos + 12)} attrs={attrCount} types=[{string.Join(",", types)}]");
            }
            o.Write(raw, pos + 4, 16); // index, nameHash, entryAssignment, attributeCount
            pos += 20;
            for (int a = 0; a < attrCount; a++) {
                int size = BitConverter.ToInt32(raw, pos + 4);
                int len = 8 + size; // typeId + size + (seqNum + payload counted by size)
                o.Write(raw, pos, len);
                pos += len;
            }
        }
        o.Write(raw, pos, raw.Length - pos);
        return o.ToArray();
    }

    // Replicates EfxFile.DoRead up to the first entry (header, strings, expression params, bones, relations, actions, field params).
    static long EntriesStart(EfxFile f, FileHandler fh) {
        var h = f.Header;
        fh.Seek(0); h.Read(fh);
        var st = new ReeLib.Efx.Strings(h); st.Read(fh);
        fh.Seek(st.Start + h.stringTableLength);
        for (int i = 0; i < h.expressionParameterCount; i++) { var p = new ReeLib.Efx.EFXExpressionParameter(); p.name = st.ExpressionParameterNames[i]; p.Read(fh); }
        if (h.boneCount > 0) throw new NotSupportedException("efx with bones: entry start walk not implemented");
        for (int i = 0; i < h.boneAttributeEntryCount; i++) fh.Read<short>();
        if (h.actionCount > 0 || h.fieldParameterCount > 0) {
            // actions / field parameters: let REE-Lib parse them through its private reader
            typeof(EfxFile).GetField("Strings")?.SetValue(f, st);
            var ra = typeof(EfxFile).GetMethod("ReadActions", BindingFlags.NonPublic | BindingFlags.Instance);
            if (ra == null) throw new MissingMethodException("EfxFile.ReadActions");
            if (h.Version > ReeLib.Efx.EfxVersion.RE7) ra.Invoke(f, new object[] { fh });
            for (int i = 0; i < h.fieldParameterCount; i++) { var p = new ReeLib.Efx.EFXFieldParameterValue { Version = h.Version, name = st.FieldParameterNames[i] }; p.Read(fh); }
        }
        return fh.Tell();
    }
}
