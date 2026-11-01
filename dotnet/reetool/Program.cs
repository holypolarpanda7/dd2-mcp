// reetool: headless helpers around REE-Lib / the REE Content Editor, used by the dd2-mcp tools dd2_mesh and dd2_efx.
//   mesh-import <in.glb> <out.mesh> [--colors] [--config DD2]   glTF -> DD2 .mesh (Content Editor importer + the fixes below)
//   mesh-info <file>                                              dump a .mesh header/buffers
//   mesh-rt <in> <out>                                            read + rewrite a .mesh (round-trip check)
//   mdf-info <file>                                               list the materials of an .mdf2
//   efx-info <file> [--all]                                       entries and attributes of an .efx (mesh/material paths shown)
//   mdf-scan <dir> [regex] [perMaster]                                       list .mdf2 materials (master shader + names) whose master matches the regex
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
        "efx-info" => EfxInfo(args[1], args.Contains("--all")),
        "efx-scan" => EfxScan(args[1], args.Length > 2 ? args[2] : ""),
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
    var config = GameConfig.CreateFromRepository("dd2");
    config.GamePath = Environment.GetEnvironmentVariable("REE_GAME_DIR") ?? @"D:\SteamLibrary\steamapps\common\Dragons Dogma 2";
    return State.Ws = new Workspace(config);
}

static EfxFile LoadEfx(string path) {
    Ws(); // creating the workspace registers the game's efx attribute type tables
    var f = new EfxFile(new FileHandler(path, false));
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

static int EfxScan(string dir, string filter) {
    int files = 0, hits = 0;
    foreach (var p in Directory.EnumerateFiles(dir, "*.efx.*", SearchOption.AllDirectories)) {
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
    Console.WriteLine($"scanned {files} efx, {hits} match '{filter}'");
    return 0;
}

static class State { public static Workspace Ws; }
