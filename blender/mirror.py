"""Headless Blender: MIRROR game motions left <-> right (a left-hand gesture becomes a right-hand one) and export FBX for
`dd2_anim import` (graft onto the game's base motion: clips, IK and props stay the game's).
blender --background --python mirror.py -- <in.fbx> <out.fbx> <action substring> <new name> [axis=auto]

Math (no pose operators: they need a UI context): per frame, each bone's armature-space pose M is read; the mirrored pose of bone b
is  M'_b = S @ M_twin(b) @ C_b  with S = reflection across the body's side axis and C_b = (S @ Rest_twin(b))^-1 @ Rest_b (the bone's
own rest frame; a centre bone is its own twin). Local keys are rebuilt parent first: basis = rest_rel^-1 @ M'_parent^-1 @ M'_b.
Twins: L_/R_ prefixes (DD2 humans: L_Arm_Upper / R_Arm_Upper), also _L/_R suffixes. The side axis is the armature-space axis along
which the twins' rest heads differ (printed: AXIS, MAXERR = how symmetric the rest skeleton is)."""
import bpy, sys, os
from mathutils import Matrix, Vector
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bl_common import fcurves, set_action
argv = sys.argv[sys.argv.index("--") + 1:]
src, dst, actsub, newname = argv[:4]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src, automatic_bone_orientation=False, ignore_leaf_bones=False)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
originals = list(bpy.data.actions)
base = next(a for a in originals if actsub in a.name)


def twin_name(n):
    """L_/R_ as a prefix or after a '_' (SHAPEKEY_L_Pec_A), else _L/_R as a suffix."""
    import re
    m = re.match(r"^(.*?(?:^|_))([LR])(_.*)$", n)
    if m:
        return m.group(1) + ("R" if m.group(2) == "L" else "L") + m.group(3)
    for a, b in (("_L", "_R"), ("_R", "_L")):
        if n.endswith(a):
            return n[:-2] + b
    return n


bones = arm.data.bones
twin = {b.name: (twin_name(b.name) if twin_name(b.name) in bones else b.name) for b in bones}
# side axis: where L/R rest heads differ most
pairs = [(b, twin[b]) for b in twin if twin[b] != b]
diff = Vector((0, 0, 0))
for a, b in pairs:
    d = bones[a].head_local - bones[b].head_local
    diff += Vector((abs(d.x), abs(d.y), abs(d.z)))
axis = max(range(3), key=lambda i: diff[i])
S = Matrix.Identity(4); S[axis][axis] = -1
err = 0.0
for a, b in pairs:
    p = bones[a].head_local.copy(); p[axis] = -p[axis]
    err = max(err, (p - bones[b].head_local).length)
print("AXIS", "XYZ"[axis], "PAIRS", len(pairs), "MAXERR %.4f" % err)

R = {b.name: b.matrix_local.copy() for b in bones}
C = {n: (S @ R[twin[n]]).inverted() @ R[n] for n in R}
order = []
def visit(b):
    order.append(b.name)
    for c in b.children:
        visit(c)
for b in bones:
    if b.parent is None:
        visit(b)

act = base.copy(); act.name = newname
set_action(arm, act)
f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
sc = bpy.context.scene
frames = {}
for f in range(f0, f1 + 1):
    sc.frame_set(f)
    frames[f] = {pb.name: pb.matrix.copy() for pb in arm.pose.bones}
# drop the copied curves, key the mirrored pose
fcs = fcurves(act, arm)
for fc in list(fcs):
    fcs.remove(fc)
for pb in arm.pose.bones:
    pb.rotation_mode = "QUATERNION"
for f in range(f0, f1 + 1):
    M = frames[f]
    Mm = {n: S @ M[twin[n]] @ C[n] for n in M}
    for n in order:
        pb = arm.pose.bones[n]
        b = bones[n]
        if b.parent:
            rest_rel = R[b.parent.name].inverted() @ R[n]
            basis = rest_rel.inverted() @ Mm[b.parent.name].inverted() @ Mm[n]
        else:
            basis = R[n].inverted() @ Mm[n]
        loc, rot, _ = basis.decompose()
        pb.rotation_quaternion = rot
        pb.keyframe_insert("rotation_quaternion", frame=f)
        if b.parent is None or n in ("Hip", "root", "Root") or loc.length > 1e-4:
            pb.location = loc
            pb.keyframe_insert("location", frame=f)
# self-check: the keyed pose must equal the mirrored target (armature space)
fc_ = (f0 + f1) // 2
sc.frame_set(fc_)
Mm = {n: S @ frames[fc_][twin[n]] @ C[n] for n in frames[fc_]}
errs = sorted(((Mm[n].to_translation() - arm.pose.bones[n].matrix.to_translation()).length, n) for n in Mm)[::-1]
print("CHECK frame", fc_, "max pos err %.4f" % errs[0][0], errs[:5])
for a in originals:
    bpy.data.actions.remove(a)
set_action(arm, act)
sc.render.fps = 60
bpy.ops.export_scene.fbx(filepath=dst, use_selection=False, bake_anim=True, bake_anim_use_all_actions=True,
                         bake_anim_use_nla_strips=False, add_leaf_bones=False, armature_nodetype="NULL")
print("EXPORTED", dst, newname, f0, f1)
