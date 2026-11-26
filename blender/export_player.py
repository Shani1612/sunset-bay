"""The dressed character as the game's player: animations, cloth bones, a light .glb.

    blender -b -P blender/export_player.py -- characters/human_dressed.blend city/assets/player.glb [preview_dir]

What it does:
  * a game-weight copy of the character: garments decimated, hidden teeth and
    tongue removed (the mouth stays closed), textures at 1024 px,
  * cloth bones: the T-shirt's hem (front, back, left, right), the sleeves and
    the lower jeans legs, weighted into the cloth, so the cloth can swing on its
    own (baked into the animations, and driven by springs in the game),
  * animations on the skeleton (all keyed from the A-pose rest):
      Idle  - breathing, a slight weight shift, arms relaxed
      Walk  - 1.34 m per cycle, feet planted by leg IK (no sliding)
      Run   - 3.0 m per cycle, flight phase, arms bent
      Drive - sitting, hands on the wheel
      Jump  - in the air
  * checks every animation frame: no skin through the clothes, no garment
    through the one over it,
  * exports a skinned .glb (no Draco: it loads anywhere without a decoder).
"""
import bpy, bmesh, math, os, sys, json
import numpy as np
from mathutils import Vector, Matrix, Quaternion
from mathutils.bvhtree import BVHTree

A = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
SRC = os.path.abspath(A[0] if A else 'characters/human_dressed.blend')
OUT = os.path.abspath(A[1] if len(A) > 1 else 'city/assets/player.glb')
PREVIEW = os.path.abspath(A[2]) if len(A) > 2 else None
FPS = 30
REPORT = {}


def log(*a):
    print('[player]', *a, flush=True)


def ss(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


bpy.ops.wm.open_mainfile(filepath=SRC)
scene = bpy.context.scene
scene.render.fps = FPS
arm = bpy.data.objects['m_ca01_skeleton']
body = bpy.data.objects['m_ca01']
VL = bpy.context.view_layer
MESHES = [o for o in bpy.data.objects if o.type == 'MESH']

# ------------------------------------------------------------------ game-weight copy
# the mouth is closed: teeth and tongue can never be seen
me = body.data
drop = {i for i, m in enumerate(me.materials) if m.name in ('MBlab_human_teeth', 'MBLab_tongue')}
bm = bmesh.new(); bm.from_mesh(me)
bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in drop], context='FACES')
bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
bm.to_mesh(me); bm.free()
for img in bpy.data.images:
    if img.size[0] > 1024:
        img.scale(1024, 1024)
    elif img.size[0] > 512 and 'albedo' not in img.name:
        img.scale(512, 512)
# three.js (r128) reads one UV set: the sclera's texture uses its own set, so copy
# that set into the main one for the eye faces, then drop the extra sets
own_uv = {}
for i, m in enumerate(me.materials):
    for n in m.node_tree.nodes:
        if n.type == 'TEX_IMAGE' and n.inputs['Vector'].links:
            src = n.inputs['Vector'].links[0].from_node
            if src.type == 'UVMAP' and src.uv_map and src.uv_map != me.uv_layers[0].name:
                own_uv[i] = src.uv_map
                m.node_tree.links.remove(n.inputs['Vector'].links[0])
main_uv = me.uv_layers[0]
for p in me.polygons:
    lay = own_uv.get(p.material_index)
    if lay:
        other = me.uv_layers[lay]
        for li in p.loop_indices:
            main_uv.data[li].uv = other.data[li].uv
while len(me.uv_layers) > 1:
    me.uv_layers.remove(me.uv_layers[len(me.uv_layers) - 1])
# (the shirt and the jeans keep their full mesh: decimating them would chew the hems)
DECIMATE = {'Shoe_': 0.22, 'ShoeCollar_': 0.4, 'Laces_': 0.6}
for o in MESHES:
    key = next((k for k in sorted(DECIMATE, key=len, reverse=True) if o.name.startswith(k)), None)
    if key is None:
        continue
    d = o.modifiers.new('Decimate', 'DECIMATE'); d.ratio = DECIMATE[key]; d.use_collapse_triangulate = False
    with bpy.context.temp_override(object=o, active_object=o):
        bpy.ops.object.modifier_move_to_index(modifier='Decimate', index=0)
log('decimated:', {o.name: [m.type for m in o.modifiers] for o in MESHES if o.name.startswith(('Shoe_', 'Socks_'))})

# ------------------------------------------------------------------ cloth bones
REST = {b.name: (b.head_local.copy(), b.tail_local.copy()) for b in arm.data.bones}
VL.objects.active = arm; arm.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
EB = arm.data.edit_bones
CLOTH = {}


def cloth_bone(name, parent, head, tail):
    b = EB.new(name); b.head = Vector(head); b.tail = Vector(tail); b.roll = 0.0
    b.parent = EB[parent]; b.use_deform = True
    CLOTH[name] = parent


C0 = Vector((0, -0.02, 0))
for nm, (dx, dy) in {'F': (0, -1), 'B': (0, 1), 'L': (1, 0), 'R': (-1, 0)}.items():
    r = 0.115 if dy else 0.15
    cloth_bone('cloth_shirt_' + nm, 'pelvis', (dx * r, -0.02 + dy * r, 1.12), (dx * (r + 0.02), -0.02 + dy * (r + 0.02), 0.92))
for s in 'LR':
    J, E = REST['upperarm_' + s]
    cloth_bone('cloth_sleeve_' + s, 'upperarm_' + s, J + (E - J) * 0.25, J + (E - J) * 0.6)
    K, An = REST['calf_' + s]
    cloth_bone('cloth_jeans_' + s, 'calf_' + s, K + (An - K) * 0.1, K + (An - K) * 0.9)
bpy.ops.object.mode_set(mode='OBJECT')
REST = {b.name: (b.head_local.copy(), b.tail_local.copy()) for b in arm.data.bones}


def blend_into(o, name, c):
    """Move a share c (per vertex) of every vertex's weight to bone `name`."""
    vg = o.vertex_groups.get(name) or o.vertex_groups.new(name=name)
    for v, k in zip(o.data.vertices, c):
        if k <= 0:
            continue
        for g in v.groups:
            g.weight *= (1 - k)
        vg.add([v.index], float(k), 'ADD')


shirt, jeans = bpy.data.objects['TShirt'], bpy.data.objects['Jeans']
Ps = np.array([v.co[:] for v in shirt.data.vertices])
th = np.arctan2(Ps[:, 0], -(Ps[:, 1] + 0.02))                 # 0 front, +-pi back, +pi/2 left (x>0)
# the hem's front and sides follow the jeans and the thighs (see dress_character.py);
# the back of the hem gets its own cloth bone, so it can swing behind the walk
HEM_SHARE = {'F': 0.0, 'L': 0.0, 'R': 0.0, 'B': 0.45}
hem = np.clip((1.12 - Ps[:, 2]) / 0.2, 0, 1) ** 1.2 * (np.abs(Ps[:, 0]) < 0.2)
dirs = {'F': 0.0, 'L': math.pi / 2, 'B': math.pi, 'R': -math.pi / 2}
wq = {k: np.maximum(np.cos(th - a), 0) ** 2 for k, a in dirs.items()}
tot = sum(wq.values()) + 1e-9
for k in dirs:
    if HEM_SHARE[k] > 0:
        blend_into(shirt, 'cloth_shirt_' + k, HEM_SHARE[k] * hem * wq[k] / tot)
for s, sx in (('L', 1), ('R', -1)):
    J, E = REST['upperarm_' + s]; a = np.array((E - J).normalized()); Ln = (E - J).length
    d = Ps - np.array(J); t = (d @ a) / Ln
    r = np.linalg.norm(d - np.outer(d @ a, a), axis=1)            # only the sleeve: near the arm's axis
    c = 0.55 * np.clip((t - 0.25) / 0.25, 0, 1) * (Ps[:, 0] * sx > 0.2) * np.clip((0.11 - r) / 0.025, 0, 1)
    blend_into(shirt, 'cloth_sleeve_' + s, c)
Pj = np.array([v.co[:] for v in jeans.data.vertices])
for s, sx in (('L', 1), ('R', -1)):
    side = Pj[:, 0] * sx > 0.02
    c = 0.3 * np.clip((0.46 - Pj[:, 2]) / 0.2, 0, 1) * np.clip((Pj[:, 2] - 0.14) / 0.08, 0, 1) * side
    blend_into(jeans, 'cloth_jeans_' + s, c)
    blend_into(jeans, 'foot_' + s, 0.55 * np.clip((0.15 - Pj[:, 2]) / 0.07, 0, 1) * side)

# ------------------------------------------------------------------ posing helpers
PB = arm.pose.bones
for pb in PB:
    pb.rotation_mode = 'QUATERNION'
# posing only needs the skeleton: switch the meshes' modifiers off meanwhile
SAVED = [(m, m.show_viewport) for o in MESHES for m in o.modifiers]
for m, _ in SAVED:
    m.show_viewport = False


def upd():
    VL.update()


def clear():
    for pb in PB:
        pb.matrix_basis = Matrix.Identity(4)
    upd()


def delta(pb):
    return pb.matrix @ pb.bone.matrix_local.inverted()


def rot(name, axis, deg, pivot=None):
    """Rotate a bone by deg about an axis given in the rest pose's world frame
    (carried along by the parent's current pose)."""
    pb = PB[name]; ax = Vector(axis)
    if pb.parent:
        ax = delta(pb.parent).to_3x3() @ ax
    ax.normalize()
    h = pb.head.copy() if pivot is None else Vector(pivot)
    pb.matrix = Matrix.Translation(h) @ Matrix.Rotation(math.radians(deg), 4, ax) @ Matrix.Translation(-h) @ pb.matrix
    upd()


def move(name, d):
    pb = PB[name]; pb.matrix = Matrix.Translation(Vector(d)) @ pb.matrix; upd()


def aim(name, point, target):
    """Rotate a bone about its head so that `point` (moving with it) heads for `target`."""
    pb = PB[name]; h = pb.head.copy()
    q = (Vector(point) - h).rotation_difference(Vector(target) - h)
    pb.matrix = Matrix.Translation(h) @ q.to_matrix().to_4x4() @ Matrix.Translation(-h) @ pb.matrix
    upd()


X, Y, Zax = (1, 0, 0), (0, 1, 0), (0, 0, 1)
FWD = Vector((0, -1, 0))                                       # the character faces -y
L1 = {s: (REST['calf_' + s][0] - REST['thigh_' + s][0]).length for s in 'LR'}
L2 = {s: (REST['foot_' + s][0] - REST['calf_' + s][0]).length for s in 'LR'}
ANK0 = {s: REST['foot_' + s][0] for s in 'LR'}                 # ankle
BALL0 = {s: REST['foot_' + s][1] for s in 'LR'}                # ball of the foot (toe joint)
HEEL_OFF = Vector((0, 0.055, -0.098))                           # heel contact point from the ankle
SX = {'L': 1.0, 'R': -1.0}


def ankle_for(s, flat, pitch):
    """Ankle position for a foot whose flat-foot ankle would be at `flat`,
    pitched by `pitch` deg (+ toes down: the heel lifts about the ball;
    - toes up: the foot rocks on the heel)."""
    flat = Vector(flat)
    if pitch >= 0:
        ball = flat + (BALL0[s] - ANK0[s])
        return ball + Matrix.Rotation(math.radians(pitch), 3, X) @ (ANK0[s] - BALL0[s])
    heel = flat + HEEL_OFF
    return heel + Matrix.Rotation(math.radians(pitch), 3, X) @ (-HEEL_OFF)


def leg(s, ankle, pitch, toes=0.0, rel=0.0):
    """Two-bone IK: put the ankle at `ankle` with the knee forward, then pitch the
    foot (+ toes down) and bend the toes."""
    th, ca, fo = PB['thigh_' + s], PB['calf_' + s], PB['foot_' + s]
    H = th.head.copy(); An = Vector(ankle)
    D = An - H; d = D.length; dmax = (L1[s] + L2[s]) * 0.9985
    if d > dmax:
        An = H + D.normalized() * dmax; D = An - H; d = dmax
    u = D.normalized()
    w = FWD - u * FWD.dot(u); w.normalize()
    a = (L1[s] ** 2 - L2[s] ** 2 + d * d) / (2 * d); p = math.sqrt(max(L1[s] ** 2 - a * a, 0.0))
    K = H + u * a + w * p
    aim('thigh_' + s, ca.head.copy(), K)
    aim('calf_' + s, fo.head.copy(), An)
    f0 = BALL0[s] - ANK0[s]
    want = Matrix.Rotation(math.radians(pitch), 3, X) @ f0
    if rel > 0:                                    # keep (partly) the foot's angle to the shin
        ride = (fo.tail - fo.head).normalized() * f0.length
        want = want.lerp(ride, rel)
    aim('foot_' + s, fo.tail.copy(), fo.head + want)
    if toes:
        rot('toes_' + s, X, toes)


def curl_fingers(s, base, tip, thumb=8.0):
    hand_z = PB['hand_' + s].bone.matrix_local.col[2].to_3d()
    for f in ('index', 'middle', 'ring', 'pinky'):
        for k, deg in ((1, base), (2, tip), (3, tip * 0.7)):
            n = '%s%02d_%s' % (f, k, s); pb = PB[n]
            ax = pb.bone.matrix_local.col[0].to_3d()
            h, t = pb.bone.head_local, pb.bone.tail_local
            moved = (Matrix.Rotation(0.3, 3, ax) @ (t - h)) - (t - h)
            sign = 1.0 if moved.dot(hand_z) > 0 else -1.0
            rot(n, ax, sign * deg)
    rot('thumb02_' + s, PB['thumb02_' + s].bone.matrix_local.col[0].to_3d(), thumb)


def arm_pose(s, down, swing, elbow, out=0.0):
    """down: lower the arm from the A-pose (deg); swing: + forward; elbow: flexion."""
    J, E = REST['upperarm_' + s]
    rot('upperarm_' + s, Y, down * SX[s])
    if out:
        rot('upperarm_' + s, Y, -out * SX[s])
    rot('upperarm_' + s, X, -swing)
    arm_dir = (E - J).normalized()
    eax = arm_dir.cross(Vector(Y)).normalized()
    if s == 'R':
        eax = -eax
    # flexion brings the hand forward: pick the sign that does
    J2, E2 = REST['lowerarm_' + s]
    test = Matrix.Rotation(0.3, 3, eax) @ (E2 - J2)
    sign = -1.0 if test.y > (E2 - J2).y else 1.0
    rot('lowerarm_' + s, eax, sign * elbow)


def cloth(name, axis, deg):
    rot(name, axis, deg)


def thigh_flex(s):
    """How far forward a thigh points (deg, 0 = straight down)."""
    H, K = PB['thigh_' + s].head, PB['calf_' + s].head
    return math.degrees(math.atan2(-(K.y - H.y), -(K.z - H.z)))


def hem_over_thighs():
    """The legs push the shirt's hem: forward when a thigh lifts, back when a
    leg reaches behind (as cloth would)."""
    fl = {s: thigh_flex(s) for s in 'LR'}
    up = {s: max(0.0, fl[s] - 4.0) for s in 'LR'}
    back = max(0.0, -min(fl.values()) - 4.0)
    rot('cloth_shirt_F', X, -0.6 * max(up.values()))
    rot('cloth_shirt_L', X, -0.5 * up['L'] + 0.3 * max(0.0, -fl['L'] - 4.0))
    rot('cloth_shirt_R', X, -0.5 * up['R'] + 0.3 * max(0.0, -fl['R'] - 4.0))
    rot('cloth_shirt_B', X, 0.5 * back)


# ------------------------------------------------------------------ the animations
def pose_idle(t):
    """t in [0,1): a 4 s breathing / weight-shift loop."""
    w = math.sin(2 * math.pi * t)
    move('pelvis', (0.008 * w, 0, -0.012))
    rot('spine02', X, 0.6 * math.sin(2 * math.pi * 2 * t))
    rot('spine03', X, 0.8 * math.sin(2 * math.pi * 2 * t + 0.4))
    rot('neck', X, -1.2 * math.sin(2 * math.pi * 2 * t + 0.4))
    rot('head', Zax, 2.0 * math.sin(2 * math.pi * t + 1.0))
    for s in 'LR':
        a = Vector((SX[s] * 0.125, 0.03, ANK0[s].z))
        leg(s, a, 0.0)
        rot('clavicle_' + s, Y, -SX[s] * 0.6 * (1 + math.sin(2 * math.pi * 2 * t)))
        arm_pose(s, 28, 8, 14)
        curl_fingers(s, 14, 22)
    for k in 'FB':
        cloth('cloth_shirt_' + k, X, 0.3 * math.sin(2 * math.pi * 2 * t))


def gait(t, P):
    """A walk or a run. t in [0,1) is the left foot's phase (0 = heel strike)."""
    stride, beta = P['stride'], P['beta']
    lean = P['lean']
    # pelvis: lower, bobbing twice a cycle, swaying over the standing foot, turning with the legs
    bob = -math.cos(4 * math.pi * t) if P['run'] is False else math.cos(4 * math.pi * (t - P['bob_phase']))
    move('pelvis', (P['sway'] * math.sin(2 * math.pi * t), 0, -P['drop'] + P['bob'] * bob))
    rot('pelvis', Zax, P['yaw'] * math.sin(2 * math.pi * t))
    rot('pelvis', Y, -P['roll'] * math.sin(2 * math.pi * t))
    rot('spine01', X, lean)
    rot('spine02', Zax, -P['yaw'] * 1.4 * math.sin(2 * math.pi * t))
    rot('spine03', Zax, -P['yaw'] * 0.6 * math.sin(2 * math.pi * t))
    rot('neck', X, -lean * 0.8)
    rot('head', Zax, P['yaw'] * 0.5 * math.sin(2 * math.pi * t))
    hipz = ANK0['L'].z
    for s, ph in (('L', t), ('R', (t + 0.5) % 1.0)):
        front, back = -P['front'], -P['front'] + stride * beta
        x = SX[s] * P['width']
        if ph < beta:                                         # stance: the foot is planted
            u = ph / beta
            y = front + (back - front) * u
            if ph < P['flat_at']:
                pitch = P['strike'] * (1 - ss(ph / P['flat_at']))
            elif ph > P['heel_off']:
                pitch = P['toe_off'] * ss((ph - P['heel_off']) / (beta - P['heel_off']))
            else:
                pitch = 0.0
            an = ankle_for(s, (x, y, hipz), pitch)
            leg(s, an, pitch, -pitch if pitch > 0 else 0.0)
        else:                                                 # swing
            u = (ph - beta) / (1 - beta)
            a0 = ankle_for(s, (x, back, hipz), P['toe_off'])
            a1 = ankle_for(s, (x, front, hipz), P['strike'])
            e = ss(u)
            pos = a0.lerp(a1, e)
            pos.z += P['clear'] * math.sin(math.pi * u) ** P['clear_pow']
            pos.y += P['kick'] * math.sin(math.pi * min(1.0, u * 1.6)) * (1 - u)
            pitch = P['toe_off'] + (P['strike'] - P['toe_off']) * ss(u * 1.3)
            leg(s, pos, pitch, -P['toe_off'] * (1 - ss(u * 2.5)), rel=P['ride'] * math.sin(math.pi * u) ** 0.7)
    for s, ph in (('L', (t + 0.5) % 1.0), ('R', t)):          # each arm swings with the opposite leg
        sw = P['arm_swing'] * math.cos(2 * math.pi * ph)
        arm_pose(s, P['arm_down'], sw + P['arm_fwd'], P['elbow'] + P['elbow_swing'] * max(0.0, math.cos(2 * math.pi * ph)))
        curl_fingers(s, P['fingers'], P['fingers'] * 1.5)
    hem_over_thighs()
    # the cloth swings a little behind the body
    k = P['cloth']
    cloth('cloth_shirt_F', X, -1.6 * k * math.sin(4 * math.pi * t - 1.3))
    cloth('cloth_shirt_B', X, -1.6 * k * math.sin(4 * math.pi * t - 1.3))
    cloth('cloth_shirt_L', Y, 2.0 * k * math.sin(2 * math.pi * t - 1.1))
    cloth('cloth_shirt_R', Y, 2.0 * k * math.sin(2 * math.pi * t - 1.1))
    for s, ph in (('L', (t + 0.5) % 1.0), ('R', t)):
        cloth('cloth_sleeve_' + s, X, -3.0 * k * math.cos(2 * math.pi * ph - 1.2))
    for s, ph in (('L', t), ('R', (t + 0.5) % 1.0)):
        cloth('cloth_jeans_' + s, X, 1.3 * k * math.sin(2 * math.pi * ph - 1.4))


WALK = dict(run=False, stride=1.34, beta=0.62, front=0.30, width=0.12, drop=0.030, bob=0.012, sway=0.012,
            yaw=4.0, roll=2.5, lean=2.0, strike=-10.0, toe_off=24.0, flat_at=0.1, heel_off=0.38, clear=0.055,
            clear_pow=1.4, kick=0.04, arm_down=28.0, arm_swing=16.0, arm_fwd=6.0, elbow=14.0, elbow_swing=14.0,
            fingers=12.0, cloth=1.0, cycle=0.94, ride=0.6)
RUN = dict(run=True, stride=2.4, beta=0.38, front=0.23, width=0.125, drop=0.05, bob=0.03, sway=0.008, bob_phase=0.19,
           yaw=6.0, roll=2.5, lean=7.0, strike=-6.0, toe_off=28.0, flat_at=0.06, heel_off=0.2, clear=0.19,
           clear_pow=0.8, kick=0.17, arm_down=28.0, arm_swing=30.0, arm_fwd=10.0, elbow=80.0, elbow_swing=14.0,
           fingers=34.0, cloth=2.0, cycle=0.6, ride=0.95)


def pose_drive(t):
    rot('pelvis', X, -14)
    rot('spine01', X, 5); rot('spine02', X, 4)
    rot('neck', X, 6)
    for s in 'LR':
        # thighs level, legs reaching forward under the dashboard (the game's car
        # seats are low: shins hanging down would put the feet through the floor)
        rot('thigh_' + s, X, -76); rot('thigh_' + s, Y, SX[s] * 4)
        rot('calf_' + s, X, 18)
        rot('foot_' + s, X, -12)
        arm_pose(s, 28, 22, 96, out=0)
        curl_fingers(s, 55, 60, 25)
    hem_over_thighs()
    cloth('cloth_shirt_B', X, 4)


def pose_jump(t):
    move('pelvis', (0, 0, -0.03))
    for s in 'LR':
        rot('thigh_' + s, X, -34 if s == 'L' else -12)
        rot('calf_' + s, X, 62 if s == 'L' else 48)
        rot('foot_' + s, X, 18)
        arm_pose(s, 30, 18, 32, out=4)
        curl_fingers(s, 15, 20)
    hem_over_thighs()
    cloth('cloth_shirt_B', X, -5)
    cloth('cloth_shirt_L', Y, -3); cloth('cloth_shirt_R', Y, 3)
    for s in 'LR':
        cloth('cloth_sleeve_' + s, X, 5)


ANIMS = [('Idle', pose_idle, 120, True), ('Walk', lambda t: gait(t, WALK), round(WALK['cycle'] * FPS), True),
         ('Run', lambda t: gait(t, RUN), round(RUN['cycle'] * FPS), True),
         ('Drive', pose_drive, 2, False), ('Jump', pose_jump, 2, False)]
arm.animation_data_create()
ACTIONS = {}
for name, fn, n, loop in ANIMS:
    act = bpy.data.actions.new(name); act.use_fake_user = True
    arm.animation_data.action = act
    frames = range(n + 1)
    for f in frames:
        clear()
        fn((f % n) / n if loop else 0.0)
        for pb in PB:
            pb.keyframe_insert('rotation_quaternion', frame=f)
            if pb.name == 'pelvis':
                pb.keyframe_insert('location', frame=f)
    ACTIONS[name] = act
    log('action', name, n, 'frames', '(%.2f s)' % (n / FPS))
arm.animation_data.action = None
clear()
for m, v in SAVED:
    m.show_viewport = v
REPORT['cycles'] = {'Walk': {'metres': WALK['stride'], 'seconds': round(round(WALK['cycle'] * FPS) / FPS, 4)},
                    'Run': {'metres': RUN['stride'], 'seconds': round(round(RUN['cycle'] * FPS) / FPS, 4)}}

# ------------------------------------------------------------------ checks on every frame
skins = {i for i, m in enumerate(body.data.materials) if m.name in ('MBLab_skin3', 'MBlab_generic')}
vis_g = body.vertex_groups['visible'].index
VIS = np.zeros(len(body.data.vertices), bool)
for v in body.data.vertices:
    for g in v.groups:
        if g.group == vis_g and g.weight > 0.5:
            VIS[v.index] = True
SKV = np.zeros(len(body.data.vertices), bool)
for p in body.data.polygons:
    if p.material_index in skins:
        SKV[list(p.vertices)] = True
vis_faces = [list(p.vertices) for p in body.data.polygons if p.material_index in skins and VIS[list(p.vertices)].all()]
vis_idx = np.where(VIS & SKV)[0]
GARM = {n: bpy.data.objects[n] for n in ('TShirt', 'Jeans', 'Socks_L', 'Socks_R', 'Shoe_L', 'Shoe_R')}
THICK = {n: next((m.thickness for m in o.modifiers if m.type == 'SOLIDIFY'), 0.0) for n, o in GARM.items()}
PAIRS = [('TShirt', 'Jeans'), ('Jeans', 'Socks_L'), ('Jeans', 'Socks_R'), ('Jeans', 'Shoe_L'), ('Jeans', 'Shoe_R')]


def evaluated(o, keep_mask=False):
    off = [(m, m.show_viewport) for m in o.modifiers if m.type == 'SOLIDIFY' or (m.type == 'MASK' and not keep_mask)]
    for m, _ in off:
        m.show_viewport = False
    dg = bpy.context.evaluated_depsgraph_get()
    ev = o.evaluated_get(dg); m2 = ev.to_mesh()
    P = np.empty(len(m2.vertices) * 3); m2.vertices.foreach_get('co', P); P = P.reshape(-1, 3)
    polys = [tuple(p.vertices) for p in m2.polygons]
    ev.to_mesh_clear()
    for m, v in off:
        m.show_viewport = v
    return P, polys


def through(points, bvh, need, outside=False, maxd=0.03, rest=None, where=None):
    n, worst = 0, 0.0
    for i, p in enumerate(points):
        co = Vector(p); loc, nrm, idx, d = bvh.find_nearest(co, maxd)
        if loc is None:
            continue
        s = (co - loc).dot(nrm)
        if d > 1e-5 and abs(s) < 0.95 * d:
            continue
        short = (need - s) if outside else (s + need)
        if short > 0:
            n += 1; worst = max(worst, short)
            if where is not None and rest is not None and len(where) < 3:
                where.append(tuple(round(float(x), 3) for x in rest[i]))
    return n, worst


CO0 = np.array([v.co[:] for v in body.data.vertices])
REST_PTS = np.vstack([CO0[vis_idx], np.array([CO0[f].mean(0) for f in vis_faces])])
WHERE = {}


def check_frame():
    Pb, _ = evaluated(body)
    pts = np.vstack([Pb[vis_idx], np.array([Pb[f].mean(0) for f in vis_faces])])
    S = {}
    for n, o in GARM.items():
        P, polys = evaluated(o)
        S[n] = (P, BVHTree.FromPolygons([Vector(p) for p in P], polys))
    out = {}
    for n in GARM:
        out['body>' + n] = through(pts, S[n][1], 0.0003, rest=REST_PTS, where=WHERE.setdefault('body>' + n, []))
    for a, b in PAIRS:
        ra = np.array([v.co[:] for v in GARM[a].data.vertices])
        out[a + '>' + b] = through(S[a][0], S[b][1], THICK[b] + 0.0002, outside=True, maxd=0.02,
                                   rest=ra if len(ra) == len(S[a][0]) else None, where=WHERE.setdefault(a + '>' + b, []))
    return out


summary = {}
for name, fn, n, loop in [('Rest', None, 1, False)] + ANIMS:
    arm.animation_data.action = ACTIONS.get(name)
    frames = [0] if name == 'Rest' else list(range(0, n, 2 if n > 20 else 1))
    worst = {}
    for f in frames:
        scene.frame_set(f)
        for k, (cnt, w) in check_frame().items():
            if cnt:
                c0, w0 = worst.get(k, (0, 0.0)); worst[k] = (c0 + cnt, max(w0, w))
    summary[name] = {k: dict(points=c, worst_mm=round(w * 1000, 1)) for k, (c, w) in worst.items()}
    log('check %-5s %2d frames: %s' % (name, len(frames), 'OK' if not worst else
        {k: (c, round(w * 1000, 1)) for k, (c, w) in worst.items()}))
    for k, v in WHERE.items():
        if v:
            log('      %s at rest positions %s' % (k, v))
    WHERE.clear()
arm.animation_data.action = None
scene.frame_set(0)
REPORT['checks'] = summary

# ------------------------------------------------------------------ preview frames
if PREVIEW:
    os.makedirs(PREVIEW, exist_ok=True)
    sc = scene
    sc.render.engine = 'CYCLES'; sc.cycles.device = 'CPU'; sc.cycles.samples = 10; sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = 360, 480
    sc.render.image_settings.file_format = 'JPEG'
    w = bpy.data.worlds.new('W'); sc.world = w; w.use_nodes = True
    w.node_tree.nodes['Background'].inputs[0].default_value = (0.7, 0.72, 0.76, 1)
    sun = bpy.data.objects.new('Sun', bpy.data.lights.new('Sun', 'SUN')); sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(40), 0, math.radians(30)); sc.collection.objects.link(sun)
    cam = bpy.data.objects.new('C', bpy.data.cameras.new('C')); sc.collection.objects.link(cam); sc.camera = cam
    cam.data.lens = 50
    for name, fn, n, loop in ANIMS:
        arm.animation_data.action = ACTIONS[name]
        picks = [0] if not loop else [round(n * k / 6) for k in range(6)]
        for i, f in enumerate(picks):
            sc.frame_set(f)
            for tag, loc in (('side', (4.2, 0, 1.0)), ('q34', (2.6, -3.3, 1.2))):
                cam.location = loc
                cam.rotation_euler = (Vector((0, 0, 0.92)) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
                sc.render.filepath = os.path.join(PREVIEW, '%s_%d_%s.jpg' % (name, i, tag))
                bpy.ops.render.render(write_still=True)
    arm.animation_data.action = None
    sc.frame_set(0)
    bpy.data.objects.remove(cam); bpy.data.objects.remove(sun)

# ------------------------------------------------------------------ export
GLTF_COLOR = {'Tee_Cotton': ((0.70, 0.70, 0.68), 0.86, 0.0), 'Denim_Indigo': ((0.02, 0.04, 0.10), 0.82, 0.0),
              'Sock_Cotton': ((0.42, 0.42, 0.43), 0.9, 0.0), 'Sneaker_Leather': ((0.80, 0.80, 0.78), 0.42, 0.0),
              'Sole_Rubber': ((0.86, 0.85, 0.80), 0.62, 0.0), 'Insole': ((0.06, 0.06, 0.065), 0.8, 0.0),
              'Lace_Cotton': ((0.86, 0.86, 0.85), 0.9, 0.0), 'Eyelet_Metal': ((0.62, 0.62, 0.64), 0.3, 1.0),
              'Collar_Padding': ((0.55, 0.56, 0.58), 0.85, 0.0)}
for o in MESHES:
    for slot in o.material_slots:
        m = slot.material
        key = m.name.split('.')[0] if m else None
        if key in GLTF_COLOR:
            col, rough, metal = GLTF_COLOR[key]
            g = bpy.data.materials.get(key + '_game') or bpy.data.materials.new(key + '_game')
            g.use_nodes = True
            b = g.node_tree.nodes['Principled BSDF']
            b.inputs['Base Color'].default_value = col + (1,); b.inputs['Roughness'].default_value = rough
            b.inputs['Metallic'].default_value = metal
            slot.material = g
# the game gets single-layer cloth (double-sided): a thickness baked at rest
# would let the inner layer cross the outer one where the skin bends
for o in MESHES:
    if o.name.startswith(('TShirt', 'Jeans', 'Socks_')):
        for m in [m for m in o.modifiers if m.type == 'SOLIDIFY']:
            o.modifiers.remove(m)
        for slot in o.material_slots:
            if slot.material:
                slot.material.use_backface_culling = False
for o in bpy.data.objects:
    o.select_set(o.type in ('MESH', 'ARMATURE'))
os.makedirs(os.path.dirname(OUT), exist_ok=True)
bpy.ops.export_scene.gltf(filepath=OUT, export_format='GLB', use_selection=True, export_apply=True,
                          export_skins=True, export_animations=True, export_animation_mode='ACTIONS',
                          export_force_sampling=True, export_frame_step=1, export_morph=False, export_yup=True,
                          export_image_format='JPEG', export_jpeg_quality=86, export_draco_mesh_compression_enable=False,
                          export_extras=False)
REPORT['bytes'] = os.path.getsize(OUT)
with open(os.path.splitext(OUT)[0] + '_report.json', 'w') as f:
    json.dump(REPORT, f, indent=1)
log('exported', OUT, '%.1f MB' % (REPORT['bytes'] / 1e6))
