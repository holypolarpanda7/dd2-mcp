"""Shared helpers for the headless Blender scripts (Blender 5 layered actions)."""
def fcurves(act, arm=None):
    try:
        return act.fcurves
    except AttributeError:
        from bpy_extras import anim_utils
        slot = arm.animation_data.action_slot if arm and arm.animation_data and arm.animation_data.action_slot else act.slots[0]
        cb = anim_utils.action_get_channelbag_for_slot(act, slot)
        return cb.fcurves if cb else []

def set_action(arm, act):
    if not arm.animation_data:
        arm.animation_data_create()
    arm.animation_data.action = act
    try:
        if act.slots and arm.animation_data.action_slot is None:
            arm.animation_data.action_slot = act.slots[0]
    except AttributeError:
        pass
