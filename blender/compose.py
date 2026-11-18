"""Headless Blender: compose NEW motions from frame ranges of game motions (cut, reverse, re-time, crossfade) and export FBX for
`dd2_anim import` (graft onto a base motion for its event clips).
blender --background --factory-startup --python compose.py -- <in.fbx>[,<in2.fbx>...] <out.fbx> <spec.json>
spec: [ {"name": "<new action name>",
         "xfade": 12,                       frames of crossfade between segments (default 12)
         "root": "hold" | "keep",           hold (default): root and Hip translation of the first frame, rotation kept per segment
                                            only for "keep"; an in-place idle must not drift
         "lower": "hold",                   optional: hips + legs keep the first frame's pose (no leg swing at the seams)
         "segments": [ {"src": "<action name substring>", "from": f0, "to": f1, "speed": 1.0}, ...] }, ...]
Frames are GAME frames (0 = the motion's first frame). from > to plays the range backwards (a take-out reversed = a put-back).
speed 0.5 = half speed (twice as many frames). Every frame of the result is keyed (60 fps). Quaternions slerp in the crossfade,
translations lerp."""
import bpy, sys, os, json, math
from mathutils import Quaternion, Vector
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bl_common import fcurves, set_action
argv = sys.argv[sys.argv.index("--") + 1:]
srcs, dst, specp = argv[0].split(","), argv[1], argv[2]
spec = json.load(open(specp))
bpy.ops.wm.read_factory_settings(use_empty=True)
arm = None
for s in srcs:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=s, automatic_bone_orientation=False, ignore_leaf_bones=False)
    new = [o for o in bpy.data.objects if o not in before]
    if arm is None:
        arm = next(o for o in new if o.type == "ARMATURE")
    else:                                   # keep one armature: the actions are what we need
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
originals = list(bpy.data.actions)


def channels(act):
    """{bone: {"rot": [4 fcurves], "loc": [3 fcurves]}} of an action."""
    set_action(arm, act)
    out = {}
    for fc in fcurves(act, arm):
        p = fc.data_path
        if not p.startswith('pose.bones["'):
            continue
        b = p[12:p.index('"]')]
        kind = "rot" if p.endswith("rotation_quaternion") else "loc" if p.endswith("location") else None
        if kind:
            out.setdefault(b, {}).setdefault(kind, [None] * (4 if kind == "rot" else 3))[fc.array_index] = fc
    return out


def sample(ch, f):
    """Pose of one action at Blender frame f: {bone: (Quaternion | None, Vector | None)}."""
    pose = {}
    for b, c in ch.items():
        r = c.get("rot"); l = c.get("loc")
        q = Quaternion([x.evaluate(f) for x in r]).normalized() if r and all(r) else None
        v = Vector([x.evaluate(f) for x in l]) if l and all(l) else None
        pose[b] = (q, v)
    return pose


def mix(a, b, w):
    out = {}
    for k in a:
        qa, va = a[k]; qb, vb = b.get(k, (None, None))
        q = qa.slerp(qb, w) if qa is not None and qb is not None else qa
        v = va.lerp(vb, w) if va is not None and vb is not None else va
        out[k] = (q, v)
    return out


def ease(k):
    k = max(0.0, min(1.0, k)); return k * k * (3 - 2 * k)


made = []
for e in spec:
    xf = int(e.get("xfade", 12))
    segs = []
    for sg in e["segments"]:
        act = next((a for a in originals if sg["src"] in a.name), None)
        if not act:
            print("NO SOURCE", sg["src"]); segs = None; break
        ch = channels(act)
        f0 = act.frame_range[0]
        a, b = sg["from"], sg["to"]
        sp = sg.get("speed", 1.0)
        n = int(round(abs(b - a) / sp)) + 1
        step = (b - a) / max(1, n - 1)
        segs.append([sample(ch, f0 + a + step * i) for i in range(n)])
    if not segs:
        continue
    frames = list(segs[0])
    for s in segs[1:]:                      # crossfade: the last xf frames of what we have blend into the first xf of s
        k = min(xf, len(frames), len(s))
        for i in range(k):
            w = ease((i + 1) / (k + 1))
            frames[len(frames) - k + i] = mix(frames[len(frames) - k + i], s[i], w)
        frames.extend(s[k:])
    if e.get("root", "hold") == "hold":
        for bn in ("root", "Hip"):
            v0 = frames[0].get(bn, (None, None))[1]
            q0 = frames[0].get(bn, (None, None))[0]
            for fr in frames:
                if bn in fr:
                    q, v = fr[bn]
                    fr[bn] = (q0 if bn == "root" else q, v0 if v0 is not None else v)
    # "lower": "hold" - hips and legs keep the FIRST frame's pose for the whole motion (sources stitched together each have their own
    # stance: blending between them swings the legs, "like the ground fell out"); Spine_0 takes over the Hip's rotation change so the
    # upper body moves as before. Pose quaternions are relative to rest: P_s' = R_s^-1 P_h0^-1 P_hi R_s P_si.
    if e.get("lower") == "hold":
        hip0 = frames[0]["Hip"][0]
        sb = arm.data.bones.get("Spine_0")
        rs = (sb.parent.matrix_local.inverted() @ sb.matrix_local).to_quaternion() if sb and sb.parent else Quaternion()
        legs = [b for b in frames[0] if "Leg" in b and frames[0][b][0] is not None]
        for fr in frames:
            qh, vh = fr["Hip"]
            if "Spine_0" in fr and fr["Spine_0"][0] is not None and qh is not None:
                qs, vs = fr["Spine_0"]
                fr["Spine_0"] = ((rs.inverted() @ hip0.inverted() @ qh @ rs @ qs).normalized(), vs)
            fr["Hip"] = (hip0, vh)
            for b in legs:
                fr[b] = (frames[0][b][0], fr[b][1])
        print("lower held:", ["Hip"] + legs)
    # Keyed offsets (gestures layered on top, e.g. a page turn): {"<bone>": [[frame, [["Y", deg], ["Z", deg]]], ...]} in the
    # bone's local axes, eased between keys, identity before the first and after the last key (start and end a gesture with a
    # key of [] = no offset).
    AX = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}

    def offq(rots):
        q = Quaternion()
        for axis, deg in rots:
            q = q @ Quaternion(AX[axis], math.radians(deg))
        return q
    for bone, keys in e.get("offset_keys", {}).items():
        ks = sorted(((k[0], offq(k[1])) for k in keys), key=lambda k: k[0])
        for i, fr in enumerate(frames):
            if bone not in fr or fr[bone][0] is None or i < ks[0][0] or i > ks[-1][0]:
                continue
            j = 0
            while j < len(ks) - 2 and i > ks[j + 1][0]:
                j += 1
            (fa, qa), (fb, qb) = ks[j], ks[j + 1]
            off = qa.slerp(qb, ease((i - fa) / max(1e-6, fb - fa)))
            q, v = fr[bone]
            fr[bone] = (q @ off, v)
    act = bpy.data.actions.new(e["name"])
    set_action(arm, act)
    for b in frames[0]:
        pb = arm.pose.bones.get(b)
        if not pb:
            continue
        pb.rotation_mode = "QUATERNION"
    for i, fr in enumerate(frames):
        for b, (q, v) in fr.items():
            pb = arm.pose.bones.get(b)
            if not pb:
                continue
            if q is not None:
                pb.rotation_quaternion = q
                pb.keyframe_insert("rotation_quaternion", frame=1 + i, options={"INSERTKEY_NEEDED"} if False else set())
            if v is not None:
                pb.location = v
                pb.keyframe_insert("location", frame=1 + i)
    act.use_frame_range = True; act.frame_start = 1; act.frame_end = len(frames)
    made.append(act)
    print("MADE", e["name"], len(frames), "frames from", [len(s) for s in segs])
for a in originals:
    bpy.data.actions.remove(a)
if not made:
    print("NOTHING MADE"); sys.exit(0)
set_action(arm, made[0])
bpy.context.scene.render.fps = 60
bpy.ops.export_scene.fbx(filepath=dst, use_selection=False, bake_anim=True, bake_anim_use_all_actions=True,
                         bake_anim_use_nla_strips=False, add_leaf_bones=False, armature_nodetype="NULL")
print("EXPORTED", dst, [a.name for a in made])
