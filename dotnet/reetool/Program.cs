// reetool: headless helpers around REE-Lib / the REE Content Editor, used by the dd2-mcp tools dd2_mesh and dd2_efx.
//   mesh-import <in.glb> <out.mesh> [--colors] [--config DD2]   glTF -> DD2 .mesh (Content Editor importer + the fixes below)
//   mesh-info <file>                                              dump a .mesh header/buffers
//   mesh-rt <in> <out>                                            read + rewrite a .mesh (round-trip check)
//   mdf-info <file>                                               list the materials of an .mdf2
//   efx-info <file> [--all]                                       entries and attributes of an .efx (mesh/material paths shown)
//   mdf-graft base donor out baseMat donorMat|- [p=x,y,z,w]..   material variant: a donor material (renamed) into a base .mdf2, params set
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
        "mdf-info" => MdfInfo(args[1], args.Length > 2 && args[2] == "--full"),
        "mdf-scan" => MdfScan(args[1], args.Length > 2 ? args[2] : "", args.Length > 3 ? int.Parse(args[3]) : 3),
        "efx-debug" => EfxDebug(args[1]),
        "efx-normalize" => EfxNormalizeCmd(args[1], args[2]),
        "efx-info" => EfxInfo(args[1], args.Contains("--all")),
        "efx-scan" => EfxScan(args[1], args.Length > 2 ? args[2] : "", args.Contains("--from") ? int.Parse(args[Array.IndexOf(args, "--from") + 1]) : 0),
        "pfb-info" => PfbInfo(args[1], args.Length > 3 && args[2] == "--fields" ? args[3] : null),
        "pfb-addmsg" => PfbAddMsg(args),
        "pfb-swap" => PfbSwap(args),
        "scn-find" => ScnFind(args),
        "reflect" => Reflect(args[1]),
        "mdf-graft" => MdfGraft(args),
        "pfb-add-weaponreg" => PfbAddWeaponReg(args),
        "user-swap" => UserSwap(args),
        "user-list" => UserList(args),
        _ => Fail("unknown command " + args[0]),
    };
} catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }

static int Fail(string m) { Console.Error.WriteLine(m); return 2; }

static ReeLib.PfbFile LoadPfb(string path) {
    var pfb = new ReeLib.PfbFile(Ws().RszFileOption, new FileHandler(path, false));
    if (!pfb.Read()) throw new Exception("could not read " + path);
    pfb.SetupGameObjects();
    return pfb;
}

static void PrintGo(ReeLib.Pfb.PfbGameObject go, string ind) {
    Console.WriteLine($"{ind}GO {go.Instance?.Values[0]} components=[{string.Join(", ", go.Components.Select(c => c.RszClass.name))}]");
    foreach (var c in go.Components)
        if (c.RszClass.name == "via.gui.AdditionalMessage")
            Console.WriteLine($"{ind}  MessageList = [{string.Join(", ", ((List<object>)c.Values[0]).Select(x => x?.ToString()))}]");
    foreach (var ch in go.Children) PrintGo(ch, ind + "  ");
}

// Print a prefab's game objects, components and resources.
// --fields <regex>: also print the field values of every component whose class name matches (e.g. "app.Weapon$").
static int PfbInfo(string path, string? fields = null) {
    var pfb = LoadPfb(path);
    foreach (var go in pfb.GameObjects) PrintGo(go, "");
    if (fields != null) {
        var rx = new System.Text.RegularExpressions.Regex(fields);
        void Dump(ReeLib.Pfb.PfbGameObject go, string ind) {
            foreach (var c in go.Components) {
                var ci = c;
                if (!rx.IsMatch(ci.RszClass.name)) continue;
                Console.WriteLine($"{ind}{ci.RszClass.name}:");
                for (int i = 0; i < ci.RszClass.fields.Length && i < ci.Values.Length; i++) {
                    var v = ci.Values[i];
                    var txt = v is RszInstance ri ? "<" + ri.RszClass.name + ">" : v is System.Collections.IList l ? $"[{l.Count}]" : v?.ToString();
                    Console.WriteLine($"{ind}  {ci.RszClass.fields[i].name} = {txt}");
                }
            }
            foreach (var ch in go.Children) Dump(ch, ind + "  ");
        }
        foreach (var go in pfb.GameObjects) Dump(go, "");
    }
    Console.WriteLine("resources: " + string.Join(", ", pfb.ResourceInfoList.Select(r => r.Path)));
    return 0;
}

// Copy a donor .user with every string value equal to <old> (or every *.pfb path when old is "*.pfb") replaced by
// <new>, then rebuild its resource table. Prints the root class and the strings it changed.
static int UserSwap(string[] a) {
    if (a.Length < 5) return Fail("usage: user-swap donor.user out.user <old|*.pfb> <new>");
    var u = new ReeLib.UserFile(Ws().RszFileOption, new FileHandler(a[1], false));
    if (!u.Read()) return Fail("could not read " + a[1]);
    int n = 0;
    void Walk(object? v, Action<object> set) {
        if (v is string str && (a[3] == "*.pfb" ? str.EndsWith(".pfb", StringComparison.OrdinalIgnoreCase) : str == a[3])) { Console.WriteLine($"  {str} -> {a[4]}"); set(a[4]); n++; }
        else if (v is List<object> list) for (int i = 0; i < list.Count; i++) { var k = i; Walk(list[k], x => list[k] = x); }
    }
    foreach (var inst in u.RSZ.InstanceList)
        if (inst?.Values != null)
            for (int i = 0; i < inst.Values.Length; i++) { var k = i; var target = inst; Walk(inst.Values[k], x => target.Values[k] = x); }
    Console.WriteLine($"root {u.Instance?.RszClass.name}: {n} value(s) swapped");
    u.RebuildInfoTable();
    if (!u.WriteTo(a[2])) return Fail("write failed");
    var back = new ReeLib.UserFile(Ws().RszFileOption, new FileHandler(a[2], false)); back.Read();
    Console.WriteLine("resources: " + string.Join(", ", back.ResourceInfoList.Select(r => r.Path)));
    return 0;
}

// Build a .user whose root holds our own entries: from a donor, replace each named root list field with deep copies of
// entry <template> of that list, one per spec item, each with the named fields set. Values: JSON numbers/bools, arrays of
// numbers, or arrays of objects for a field that holds objects (each a copy of that field's first element with its fields set).
// spec.json: {"lists": [{"field": "_Params", "template": 0, "items": [{"_Id": 9001, "_MaterialA": [1, 2]}, ...]}, ...]}
// An item's "__template": n copies donor entry n instead of the list's template (e.g. one weapon row per staff tier).
static int UserList(string[] a) {
    if (a.Length < 4) return Fail("usage: user-list donor.user out.user spec.json");
    var u = new ReeLib.UserFile(Ws().RszFileOption, new FileHandler(a[1], false));
    if (!u.Read()) return Fail("could not read " + a[1]);
    var root = u.Instance ?? throw new Exception("no root");
    var spec = System.Text.Json.JsonDocument.Parse(File.ReadAllText(a[3])).RootElement;
    RszInstance Fill(RszInstance tpl, System.Text.Json.JsonElement item) {
        var c = tpl.Clone();
        foreach (var p in item.EnumerateObject()) {
            if (p.Name == "__template") continue;
            if (!c.RszClass.fields.Any(f => f.name == p.Name)) throw new Exception("no field " + p.Name + " in " + c.RszClass.name);
            c.SetFieldValue(p.Name, Conv(p.Value, c.GetFieldValue(p.Name), p.Name));
        }
        return c;
    }
    object Conv(System.Text.Json.JsonElement v, object? old, string name) {
        if (v.ValueKind == System.Text.Json.JsonValueKind.Array && old is System.Numerics.Vector3) {     // [x, y, z]
            var f = v.EnumerateArray().Select(e => (float)e.GetDouble()).ToArray();
            return new System.Numerics.Vector3(f[0], f[1], f[2]);
        }
        if (v.ValueKind == System.Text.Json.JsonValueKind.Array && old is System.Numerics.Quaternion) {  // [x, y, z, w]
            var f = v.EnumerateArray().Select(e => (float)e.GetDouble()).ToArray();
            return new System.Numerics.Quaternion(f[0], f[1], f[2], f[3]);
        }
        if (v.ValueKind == System.Text.Json.JsonValueKind.Array) {
            var l = new List<object>(); var proto = (old as List<object>)?.FirstOrDefault();
            foreach (var e in v.EnumerateArray()) {
                if (e.ValueKind == System.Text.Json.JsonValueKind.Object) {
                    if (proto is not RszInstance pi) throw new Exception(name + ": no object in the donor list to copy");
                    l.Add(Fill(pi, e));
                } else {
                    if (proto == null) throw new Exception(name + ": donor list is empty, cannot infer the element type");
                    l.Add(Conv(e, proto, name));
                }
            }
            return l;
        }
        if (v.ValueKind == System.Text.Json.JsonValueKind.Object) {
            if (old is not RszInstance oi) throw new Exception(name + " is not an object field");
            return Fill(oi, v);
        }
        if (v.ValueKind is System.Text.Json.JsonValueKind.True or System.Text.Json.JsonValueKind.False) return v.GetBoolean();
        if (v.ValueKind == System.Text.Json.JsonValueKind.String) return v.GetString()!;   // paths, joint names
        return Convert.ChangeType(v.GetDouble(), old?.GetType() ?? typeof(int));
    }
    foreach (var ls in spec.GetProperty("lists").EnumerateArray()) {
        var fname = ls.GetProperty("field").GetString()!;
        if (root.GetFieldValue(fname) is not List<object> list) return Fail("root field " + fname + " is not a list; fields: " + string.Join(", ", root.RszClass.fields.Select(f => f.name)));
        var tpl = (RszInstance)list[ls.TryGetProperty("template", out var t) ? t.GetInt32() : 0];
        // an item may name its own template entry ("__template": index into the donor list); the key is not a field
        var donor = list.ToList();
        var made = ls.GetProperty("items").EnumerateArray().Select(it => {
            var own = it.TryGetProperty("__template", out var ti) ? (RszInstance)donor[ti.GetInt32()] : tpl;
            return (object)Fill(own, it);
        }).ToList();
        list.Clear(); list.AddRange(made);
    }
    u.RebuildInfoTable();
    if (!u.WriteTo(a[2])) return Fail("write failed");
    var back = new ReeLib.UserFile(Ws().RszFileOption, new FileHandler(a[2], false)); back.Read();
    Console.WriteLine(back.Instance!.Stringify(back.RSZ.InstanceList));
    return 0;
}

// The game registers extra .msg files through a via.gui.AdditionalMessage component (its MessageList of message
// resources) found in game data. Make a one-object prefab from a donor: keep its root and Transform, drop the rest,
// add the component listing our msg paths (e.g. "wizard/text/wizard_text.msg").
static int PfbAddMsg(string[] a) {
    if (a.Length < 4) return Fail("usage: pfb-addmsg donor.pfb out.pfb <msg path>...");
    var pfb = LoadPfb(a[1]);
    var root = pfb.Root ?? throw new Exception("donor has no root");
    root.Children.Clear();
    foreach (var c in root.Components.ToList())
        if (c.RszClass.name != "via.Transform") root.Components.Remove(c);
    while (pfb.GameObjects.Count > 1) pfb.GameObjects.RemoveAt(1);
    if (root.Instance != null && root.Instance.Values[0] is string) root.Instance.Values[0] = "DnDClassesText";   // via.GameObject v0 = name
    var comp = pfb.RSZ.CreateInstance("via.gui.AdditionalMessage");
    comp.Values[0] = a.Skip(3).Cast<object>().ToList();
    pfb.AddComponent(root, comp);
    pfb.RebuildInfoTables();
    if (!pfb.WriteTo(a[2])) return Fail("write failed");
    foreach (var go in LoadPfb(a[2]).GameObjects) PrintGo(go, "");
    return 0;
}

// Copy a prefab with string values swapped (old=new, exact match, anywhere in any instance, also inside lists) and
// typed fields set (--set Class.field=value: int/uint/float/bool, matched by the class's field name), then rebuild its
// resource tables. Used for a mod weapon: a game weapon prefab with its mesh/material paths, name and app.Weapon ID changed.
static int PfbSwap(string[] a) {
    if (a.Length < 4) return Fail("usage: pfb-swap in.pfb out.pfb old=new... [--set Class.field=value]...");
    var pfb = LoadPfb(a[1]);
    var swaps = new Dictionary<string, string>(); var sets = new List<(string cls, string field, string val)>();
    for (int i = 3; i < a.Length; i++) {
        if (a[i] == "--set") { var kv = a[++i].Split('=', 2); var cf = kv[0].Split('.'); sets.Add((string.Join('.', cf[..^1]), cf[^1], kv[1])); continue; }
        var p = a[i].Split('=', 2); swaps[p[0]] = p[1];
    }
    int changed = 0;
    object Swap(object v) {
        if (v is string s && swaps.TryGetValue(s, out var n)) { changed++; Console.WriteLine($"  \"{s}\" -> \"{n}\""); return n; }
        if (v is IList l) { for (int k = 0; k < l.Count; k++) l[k] = Swap(l[k]); }
        return v;
    }
    // user-data file references (e.g. a WwiseContainerApp's sound container lists) are not instance strings: they sit on the
    // instance's RSZUserData and in the file's UserdataInfoList
    string? PathOf(object o) => (o.GetType().GetProperty("Path")?.GetValue(o) ?? o.GetType().GetField("Path")?.GetValue(o)) as string;
    void SetPath(object o, string v) {
        var pr = o.GetType().GetProperty("Path");
        if (pr != null && pr.CanWrite) pr.SetValue(o, v); else o.GetType().GetField("Path")?.SetValue(o, v);
    }
    foreach (var inst in pfb.RSZ.InstanceList)
        if (inst?.RSZUserData is object ud && PathOf(ud) is string up && swaps.TryGetValue(up, out var un)) {
            SetPath(ud, un); changed++; Console.WriteLine($"  userdata \"{up}\" -> \"{un}\"");
        }
    foreach (var ui in pfb.UserdataInfoList)
        if (PathOf(ui) is string up2 && swaps.TryGetValue(up2, out var un2)) SetPath(ui, un2);
    foreach (var inst in pfb.RSZ.InstanceList) {
        if (inst?.Values == null) continue;
        for (int k = 0; k < inst.Values.Length; k++) inst.Values[k] = Swap(inst.Values[k]);
        foreach (var (cls, field, val) in sets) {
            if (inst.RszClass.name != cls) continue;
            int idx = inst.RszClass.IndexOfField(field);
            if (idx < 0) throw new Exception($"{cls} has no field {field}");
            var old = inst.Values[idx];
            inst.Values[idx] = old switch {
                int => int.Parse(val), uint => uint.Parse(val), float => float.Parse(val, System.Globalization.CultureInfo.InvariantCulture),
                bool => bool.Parse(val), long => long.Parse(val), ulong => ulong.Parse(val), _ => throw new Exception($"{cls}.{field}: unsupported type {old?.GetType()}") };
            changed++; Console.WriteLine($"  {cls}.{field}: {old} -> {inst.Values[idx]}");
        }
    }
    if (changed == 0) return Fail("nothing matched");
    pfb.RebuildInfoTables();
    if (!pfb.WriteTo(a[2])) return Fail("write failed");
    var back = LoadPfb(a[2]);
    foreach (var go in back.GameObjects) PrintGo(go, "");
    Console.WriteLine("resources: " + string.Join(", ", back.ResourceInfoList.Select(r => r.Path)));
    return 0;
}

// Print every instance of a class in a scene (or prefab): its field names, values and value types (user-data references
// show their class and path), to see how the game wires a component before copying it into a mod prefab.
static int ScnFind(string[] a) {
    if (a.Length < 3) return Fail("usage: scn-find file.scn|file.pfb ClassName");
    ReeLib.RSZFile rsz;
    if (a[1].Contains(".pfb")) rsz = LoadPfb(a[1]).RSZ;
    else { var scn = new ReeLib.ScnFile(Ws().RszFileOption, new FileHandler(a[1], false)); if (!scn.Read()) return Fail("read failed"); rsz = scn.RSZ; }
    foreach (var inst in rsz.InstanceList) {
        if (inst == null || inst.RszClass.name != a[2]) continue;
        Console.WriteLine($"{inst.RszClass.name} #{inst.Index}");
        for (int k = 0; k < inst.Values.Length; k++) {
            var v = inst.Values[k]; var f = inst.RszClass.fields[k];
            string desc = v switch {
                RszInstance ri => $"instance {ri.RszClass.name} #{ri.Index}" + (ri.RSZUserData != null ? $" userdata path={ri.RSZUserData.Path}" : ""),
                IList l => $"list[{l.Count}]",
                _ => $"{v} ({v?.GetType().Name})" };
            Console.WriteLine($"  {f.name} [{f.type}/{f.original_type}] = {desc}");
        }
    }
    return 0;
}

// Add an app.WeaponCatalogRegister to a prefab's root (as the DLC scene appdata/extracontents/contents_0014.scn wires it):
// Enabled, CatalogData + WeaponSetting as references to .user files (engine paths, e.g. "DnDClasses/Weapons/X.user").
// On awake it registers the catalog (WeaponID -> prefab) and the weapon offsets with the EquipmentManager.
static int PfbAddWeaponReg(string[] a) {
    if (a.Length < 5) return Fail("usage: pfb-add-weaponreg in.pfb out.pfb catalog.user setting.user");
    var pfb = LoadPfb(a[1]);
    var root = pfb.Root ?? throw new Exception("no root");
    RszInstance Ref(string cls, string path) {
        var tmp = pfb.RSZ.CreateInstance(cls);
        return new RszInstance(tmp.RszClass, new RSZUserDataInfo { Path = path, ClassName = cls, typeId = tmp.RszClass.typeId });
    }
    var comp = pfb.RSZ.CreateInstance("app.WeaponCatalogRegister");
    comp.SetFieldValue("Enabled", true);
    comp.SetFieldValue("CatalogData", Ref("app.WeaponCatalogData", a[3]));
    comp.SetFieldValue("WeaponSetting", Ref("app.WeaponSetting", a[4]));
    pfb.AddComponent(root, comp);
    pfb.RebuildInfoTables();
    if (!pfb.WriteTo(a[2])) return Fail("write failed");
    var back = LoadPfb(a[2]);
    foreach (var go in back.GameObjects) PrintGo(go, "");
    return ScnFind(new[] { "scn-find", a[2], "app.WeaponCatalogRegister" });
}

// Members of a REE-Lib type (constructors, properties, fields, methods): for writing new commands against the library.
static int Reflect(string typeName) {
    var t = typeof(ReeLib.PfbFile).Assembly.GetTypes().FirstOrDefault(x => x.FullName == typeName || x.Name == typeName);
    if (t == null) return Fail("no type " + typeName);
    Console.WriteLine(t.FullName);
    foreach (var c in t.GetConstructors()) Console.WriteLine("  ctor(" + string.Join(", ", c.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name)) + ")");
    foreach (var m in t.GetMembers(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
        if (m is not ConstructorInfo && !(m is MethodInfo mi && mi.IsSpecialName)) Console.WriteLine("  " + m.MemberType + " " + m);
    return 0;
}

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

static int MdfInfo(string path, bool full) {
    var f = new MdfFile(new FileHandler(path, false));
    if (!f.Read()) return Fail("read failed");
    foreach (var m in f.Materials) {
        Console.WriteLine($"{m.Name} master={m.MasterMaterial} textures={m.Textures.Count} params={m.Parameters.Count}");
        if (!full) continue;
        // slot numbers are what Mesh:setMaterialTexture(material, slot, ...) and setMaterialFloat/Float4(material, index, ...) take at runtime
        for (int i = 0; i < m.Textures.Count; i++) Console.WriteLine($"  tex[{i}] {m.Textures[i].texType} = {m.Textures[i].texPath}");
        for (int i = 0; i < m.Parameters.Count; i++) { var p = m.Parameters[i]; Console.WriteLine($"  param[{i}] {p.paramName} x{p.componentCount} = ({p.parameter.X:0.###}, {p.parameter.Y:0.###}, {p.parameter.Z:0.###}, {p.parameter.W:0.###})"); }
    }
    return 0;
}

// Material variants: replace one material of a base .mdf2 with a (renamed) copy of a donor's material, then set parameters.
// usage: mdf-graft base.mdf2 donor.mdf2 out.mdf2 <baseMat> <donorMat|-> [param=x,y,z,w]...
// donorMat "-" keeps the base material and only sets parameters. The mesh binds materials by NAME, so the copy takes baseMat's name.
// Used for Spell Book tiers: the book's cover material takes a tinted skill-tome material (same texture atlas and UV layout).
static int MdfGraft(string[] a) {
    if (a.Length < 6) return Fail("usage: mdf-graft base.mdf2 donor.mdf2 out.mdf2 <baseMat> <donorMat|-> [param=x,y,z,w]...");
    var b = new MdfFile(new FileHandler(a[1], false)); if (!b.Read()) return Fail("read failed: " + a[1]);
    int bi = b.Materials.FindIndex(m => m.Name == a[4]);
    if (bi < 0) return Fail("no material " + a[4] + " in base; has: " + string.Join(", ", b.Materials.Select(m => m.Name)));
    if (a[5] != "-") {
        var d = new MdfFile(new FileHandler(a[2], false)); if (!d.Read()) return Fail("read failed: " + a[2]);
        var dm = d.Materials.FirstOrDefault(m => m.Name == a[5]);
        if (dm == null) return Fail("no material " + a[5] + " in donor; has: " + string.Join(", ", d.Materials.Select(m => m.Name)));
        var c = dm.Clone(); c.Name = a[4];
        b.Materials[bi] = c;
    }
    var mat = b.Materials[bi];
    foreach (var kv in a.Skip(6)) {
        var eq = kv.IndexOf('='); if (eq < 0) return Fail("bad param " + kv);
        var name = kv[..eq]; var v = kv[(eq + 1)..].Split(',').Select(x => float.Parse(x, System.Globalization.CultureInfo.InvariantCulture)).ToArray();
        var p = mat.Parameters.FirstOrDefault(x => x.paramName == name);
        if (p == null) return Fail("no parameter " + name + " in " + mat.Name);
        p.parameter = new System.Numerics.Vector4(v.ElementAtOrDefault(0), v.ElementAtOrDefault(1), v.ElementAtOrDefault(2), v.Length > 3 ? v[3] : 1f);
    }
    if (!b.WriteTo(a[3])) return Fail("write failed");
    return MdfInfo(a[3], true);
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
