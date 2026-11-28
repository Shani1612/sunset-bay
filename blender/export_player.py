"""The dressed character as the game's player: animations, cloth bones, a light .glb.

    blender -b -P blender/export_player.py -- characters/human_dressed.blend city/assets/player.glb [preview_dir]

What it does:
  * a game-weight copy of the character: garments decimated, hidden teeth and
    tongue removed (the mouth stays closed), textures at 1024 px,
  * cloth bones: the T-shirt's hem (front, back, left, right), the sleeves and
    the lower jeans legs, weighted into the cloth, so the cloth can swing on its
    own (baked into the animations, and driven by springs in the game); and hem
    bones at the hips, so the front of the hem rises over a thigh that comes
    forward and hangs over one that goes back,
  * animations on the skeleton (all keyed from the A-pose rest):
      Idle  - breathing, a slight weight shift, arms hanging straight
      Walk  - 1.34 m per cycle, feet planted by leg IK (no sliding), straight arms swinging
      Run   - 2.4 m per cycle, flight phase, elbows bent about 90 deg, loose fists
      Drive - sitting, hands on the wheel
      Jump  - in the air
  * shape keys on the shirt pressing its sides in under the arms: ArmsDown for
    Idle and Walk, ArmsDownRun for Run (the game sets them by those weights),
  * checks every animation frame, and the game's cloth springs at their limits:
    no skin through the clothes, no garment through the one over it,
  * exports a skinned .glb (no Draco: it loads anywhere without a decoder), with
    at most 4 bones per vertex, as glTF keeps (the checks see the same).

The city's people (make_npc_bodies.py, dress_character.py, make_hair.py) go
through the same steps with --npc:

    blender -b -P blender/export_player.py -- <id>.blend city/assets/npc/<id>.glb [preview_dir] --npc

  * every measure above that was set on the player's body (the hem's height,
    where the sleeves start, the feet's places...) is carried over to this
    body by its skeleton's landmarks; the stride by the length of its legs (and
    the cycle's time by its square root, as people's gaits scale), and the arms
    hang as far from the hips as the player's do,
  * animations Idle, Walk, Run, Drive (in a car) and Lie (knocked down, on the
    back) - no Jump,
  * the hair is checked with the clothes: no scalp through it, no skin into it,
    none of it through the shirt,
  * two levels of detail in one file, on one skeleton: LOD0 (garments and body
    lighter than the player's; 5 draw calls) and LOD1 for the distance (about
    a sixth of the triangles; 3 draw calls), checked with a 1 cm tolerance.
    The garments, the eyes and the nails share one material: colours per
    vertex times a small palette texture (u = slot: 0 shirt, 1 jeans, 2 shoes,
    the rest white), so the game gives each pedestrian their own colours by
    giving them their own palette.
"""
import bpy, bmesh, math, os, sys, json
import numpy as np
from mathutils import Vector, Matrix, Quaternion
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from mathutils import geometry
from collections import defaultdict

A = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
NPC = '--npc' in A
A = [a for a in A if a != '--npc']
SRC = os.path.abspath(A[0] if A else 'characters/human_dressed.blend')
OUT = os.path.abspath(A[1] if len(A) > 1 else 'city/assets/player.glb')
PREVIEW = os.path.abspath(A[2]) if len(A) > 2 else None
FPS = 30
REPORT = {}


def log(*a):
    print('[npc]' if NPC else '[player]', *a, flush=True)


def ss(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


bpy.ops.wm.open_mainfile(filepath=SRC)
scene = bpy.context.scene
scene.render.fps = FPS
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
body = next(o for o in bpy.data.objects if o.type == 'MESH' and 'visible' in o.vertex_groups)
VL = bpy.context.view_layer
MESHES = [o for o in bpy.data.objects if o.type == 'MESH']
HAIR = bpy.data.objects.get('Hair')


# ------------------------------------------------------------------ this body's measures
def body_measures():
    """The skeleton's landmarks and a few widths of the skin (as in
    dress_character.py: Z are the sole, ankle, knee, hip joint, chest, shoulder
    joint, head and crown heights)."""
    me = body.data
    sk = {i for i, m in enumerate(me.materials) if m.name in ('MBLab_skin3', 'MBlab_generic')}
    sv = np.zeros(len(me.vertices), bool)
    for p in me.polygons:
        if p.material_index in sk:
            sv[list(p.vertices)] = True
    CO = np.array([v.co[:] for v in me.vertices])[sv]
    B = {b.name: (b.head_local.copy(), b.tail_local.copy()) for b in arm.data.bones}
    mid = lambda n, k: (B[n + '_L'][k] + B[n + '_R'][k]) / 2
    half = lambda n, k: (abs(B[n + '_L'][k].x) + abs(B[n + '_R'][k].x)) / 2

    def band(z, xmax):
        return CO[(np.abs(CO[:, 2] - z) < 0.012) & (np.abs(CO[:, 0]) < xmax)]
    hip = mid('thigh', 0); ch = B['spine03'][0].z
    d = B['upperarm_L'][1] - B['upperarm_L'][0]
    tb = band(hip.z + 0.05, 0.1)
    return dict(
        Z=[float(CO[:, 2].min()), mid('calf', 1).z, mid('calf', 0).z, hip.z, ch, mid('upperarm', 0).z, B['head'][0].z,
           float(CO[:, 2].max())],
        shx=half('upperarm', 0), hipx=half('thigh', 0),
        chest=float(np.abs(band(ch, half('upperarm', 0) + 0.05)[:, 0]).max()), hipw=float(np.abs(band(hip.z, 0.4)[:, 0]).max()),
        hip_y=hip.y, torso_y=float((tb[:, 1].min() + tb[:, 1].max()) / 2), ank_y=mid('foot', 0).y, ank_z=mid('foot', 0).z,
        leg=float(np.mean([(B['calf_' + s][0] - B['thigh_' + s][0]).length + (B['foot_' + s][0] - B['calf_' + s][0]).length
                           for s in 'LR'])),
        foot=float(np.mean([(B['toes_' + s][0] - B['foot_' + s][0]).length for s in 'LR'])),
        arm_a0=math.degrees(math.atan2(abs(d.x), -d.z)),
        arm_len=float(np.mean([(B['upperarm_' + s][1] - B['upperarm_' + s][0]).length +
                               (B['lowerarm_' + s][1] - B['lowerarm_' + s][0]).length for s in 'LR'])))


# the player's (characters/Human.glb), by body_measures()
REF_BODY = dict(Z=[-0.0029, 0.104, 0.5324, 0.9902, 1.3101, 1.4784, 1.6519, 1.8303], shx=0.1812, hipx=0.1165, chest=0.165,
                hipw=0.1719, hip_y=-0.0071, torso_y=-0.0348, ank_y=0.0277, ank_z=0.104, leg=0.8903, foot=0.1674,
                arm_a0=57.7484, arm_len=0.54)
IDLE_DOWN = 50.0                    # the player's arms in Idle: lowered 50 deg from the A-pose
if NPC:
    BM = body_measures()
    REPORT['body'] = {k: ([round(float(x), 4) for x in v] if isinstance(v, list) else round(float(v), 4)) for k, v in BM.items()}

    def pchip(xk, yk):
        """A monotone piecewise-cubic through the knots (as dress_character.py's), linear beyond the ends."""
        xk = np.asarray(xk, float); yk = np.asarray(yk, float)
        h = np.diff(xk); d = np.diff(yk) / h
        m = np.zeros(len(xk))
        for i in range(1, len(xk) - 1):
            if d[i - 1] * d[i] > 0:
                w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
                m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
        m[0], m[-1] = d[0], d[-1]

        def f(x):
            x = np.asarray(x, float)
            i = np.clip(np.searchsorted(xk, x) - 1, 0, len(h) - 1)
            t = np.clip((x - xk[i]) / h[i], 0, 1)
            t2, t3 = t * t, t * t * t
            y = ((2 * t3 - 3 * t2 + 1) * yk[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
                 + (-2 * t3 + 3 * t2) * yk[i + 1] + (t3 - t2) * h[i] * m[i + 1])
            y = np.where(x < xk[0], yk[0] + m[0] * (x - xk[0]), y)
            y = np.where(x > xk[-1], yk[-1] + m[-1] * (x - xk[-1]), y)
            return float(y) if y.ndim == 0 else y
        return f
    ZN = pchip(REF_BODY['Z'], BM['Z'])            # a height on the player -> the same place on this body

    def ZD(z0, z1, d):
        """The span between two of the player's heights (d) on this body."""
        return ZN(z0) - ZN(z1)
    XS = BM['shx'] / REF_BODY['shx']              # widths: shoulders, hip joints, the pelvis, the chest
    XH = BM['hipx'] / REF_BODY['hipx']
    XP = BM['hipw'] / REF_BODY['hipw']
    XC = BM['chest'] / REF_BODY['chest']
    TY = BM['torso_y'] - REF_BODY['torso_y']      # the torso's front-back middle
    YO = BM['hip_y'] - REF_BODY['hip_y']          # the hip joints'
    DY_ANK = BM['ank_y'] - REF_BODY['ank_y']
    FL = BM['foot'] / REF_BODY['foot']; AZ = BM['ank_z'] / REF_BODY['ank_z']
    K_LEG = BM['leg'] / REF_BODY['leg']
    # the hands hang as far outside the hips as the player's: the arm's angle
    # from straight down that gives this body the player's clearance (the
    # shoulder joint's x + the arm's reach - the pelvis's half width)

    def clear_x(b, phi):
        return b['shx'] + b['arm_len'] * math.sin(math.radians(phi)) - b['hipw']
    PHI0 = REF_BODY['arm_a0'] - IDLE_DOWN
    s_phi = (clear_x(REF_BODY, PHI0) - BM['shx'] + BM['hipw']) / BM['arm_len']
    PHI = max(PHI0, math.degrees(math.asin(min(max(s_phi, 0.0), 0.5))))
    PHI = min(PHI, 13.0)
    DARM = (BM['arm_a0'] - PHI) - IDLE_DOWN       # added to every pose's "down" of the arms
    REPORT['mapping'] = dict(XS=round(XS, 4), XH=round(XH, 4), XP=round(XP, 4), XC=round(XC, 4), TY=round(TY, 4),
                             YO=round(YO, 4), leg=round(K_LEG, 4), arm_from_vertical=round(PHI, 2), arm_down_extra=round(DARM, 2))
    log('measures', json.dumps(REPORT['body']))
    log('mapping', json.dumps(REPORT['mapping']))
else:
    def ZN(z):
        return z

    def ZD(z0, z1, d):
        return d
    XS = XH = XP = XC = FL = AZ = K_LEG = 1.0
    TY = YO = DY_ANK = DARM = 0.0
    BM = REF_BODY                     # (the player is the reference body)

# ------------------------------------------------------------------ game-weight copy
# the mouth is closed: teeth and tongue can never be seen
me = body.data
# (a city person's eyes have no cornea: its glassy highlight is lost at their size,
#  and it's a transparent layer to sort)
drop = {i for i, m in enumerate(me.materials) if m.name in ('MBlab_human_teeth', 'MBLab_tongue') +
        (('MBlab_cornea',) if NPC else ())}
bm = bmesh.new(); bm.from_mesh(me)
bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in drop], context='FACES')
if NPC:
    # skin the clothes and the hair hide is gone for good (the player keeps it, masked)
    dl = bm.verts.layers.deform.active
    vgi = body.vertex_groups['visible'].index
    hidden = [f for f in bm.faces if any(v[dl].get(vgi, 0.0) <= 0.5 for v in f.verts)]
    log('body: %d faces under the clothes and the hair removed' % len(hidden))
    bmesh.ops.delete(bm, geom=hidden, context='FACES')
bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
bm.to_mesh(me); bm.free()


def lash_texture(size=512):
    """MB-Lab's eyelash texture is grey cards with the lashes drawn darker on
    them, and no transparency: drawn as it is, the cards show as white strips
    along the eyelids. The game gets a dark texture whose alpha is the lashes
    (taken at full size, then each pixel the darkest of its block, so the thin
    strands survive the smaller size)."""
    m = bpy.data.materials.get('MBlab_eyelash')
    t = next((n for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image), None) if m else None
    if t is None:
        return
    src = t.image; w, h = src.size
    px = np.empty(w * h * 4, np.float32); src.pixels.foreach_get(px); px = px.reshape(h, w, 4)
    a = np.clip((0.5 - px[..., :3].mean(2)) / 0.15, 0.0, 1.0)
    k = max(1, w // size)
    a = a[:size * k, :size * k].reshape(size, k, size, k).max(axis=(1, 3))
    out = np.zeros((size, size, 4), np.float32); out[..., :3] = 0.05; out[..., 3] = a
    im = bpy.data.images.new('Eyelash_game', size, size, alpha=True)
    im.pixels.foreach_set(out.ravel()); im.file_format = 'PNG'; im.pack()
    t.image = im
    b = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    m.node_tree.links.new(t.outputs['Alpha'], b.inputs['Alpha'])
    log('eyelashes: %d of %d texels are lashes' % (int((a > 0.5).sum()), size * size))


def fill_skin_background(o):
    """The skin texture is white outside its UV islands: the smaller mipmap levels
    the game draws at a distance would bleed that white along the seams (dotted
    light lines at the jaw, the nose, the eyelids). Fill the background from the
    islands' edges outward (64 texels; beyond that, the skin's mean colour)."""
    m = bpy.data.materials.get('MBLab_skin3')
    t = next((n for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image), None) if m else None
    if t is None:
        return
    img = t.image; W, H = img.size
    px = np.empty(W * H * 4, np.float32); img.pixels.foreach_get(px); px = px.reshape(H, W, 4)
    me = o.data; uvl = me.uv_layers[0].data
    sk = {i for i, mm in enumerate(me.materials) if mm and mm.name.split('.')[0] in ('MBLab_skin3', 'MBlab_generic')}
    mask = np.zeros((H, W), bool)
    for p in me.polygons:
        if p.material_index not in sk:
            continue
        U = np.array([uvl[li].uv[:] for li in p.loop_indices]) * (W, H) - 0.5
        for j in range(1, len(U) - 1):
            T = U[[0, j, j + 1]]
            x0, y0 = np.floor(T.min(0)).astype(int) - 1; x1, y1 = np.ceil(T.max(0)).astype(int) + 1
            x0, y0 = max(x0, 0), max(y0, 0); x1, y1 = min(x1, W - 1), min(y1, H - 1)
            if x1 < x0 or y1 < y0:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
            (ax, ay), (bx, by), (cx, cy) = T
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-12:
                continue
            l1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
            l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
            inside = (l1 >= -0.02) & (l2 >= -0.02) & (1 - l1 - l2 >= -0.02)
            mask[gy[inside], gx[inside]] = True
    col = px[..., :3].copy(); filled = mask.copy()
    for _ in range(64):
        if filled.all():
            break
        acc = np.zeros_like(col); cnt = np.zeros((H, W))
        for sh in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            f = np.roll(filled, sh, axis=(0, 1))
            acc += np.roll(col, sh, axis=(0, 1)) * f[..., None]; cnt += f
        new = ~filled & (cnt > 0)
        col[new] = acc[new] / cnt[new][:, None]; filled |= new
    col[~filled] = px[..., :3][mask].mean(0)
    px[..., :3] = np.where(mask[..., None], px[..., :3], col)
    img.pixels.foreach_set(px.ravel()); img.update()
    log('skin texture: %.0f%% inside the UV islands, the rest filled from their edges' % (100 * mask.mean()))


lash_texture()
for img in bpy.data.images:
    if NPC and 'eyelash' in img.name:
        img.scale(256, 256)
    elif img.size[0] > 1024:
        img.scale(1024, 1024)
    elif img.size[0] > 512 and 'albedo' not in img.name:
        img.scale(512, 512)
fill_skin_background(body)
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


def boundary_dist(o):
    """Each vertex's distance (through the mesh, along edges) to the mesh's open edges."""
    bm = bmesh.new(); bm.from_mesh(o.data); bm.verts.ensure_lookup_table()
    D = np.full(len(bm.verts), 1e9)
    front = []
    for e in bm.edges:
        if e.is_boundary:
            for v in e.verts:
                D[v.index] = 0.0; front.append(v.index)
    E = np.array([(e.verts[0].index, e.verts[1].index) for e in bm.edges])
    L = np.array([e.calc_length() for e in bm.edges]); bm.free()
    for _ in range(60):                                  # (Bellman-Ford sweeps: enough for 2 cm here)
        a = D[E[:, 0]] + L; b = D[E[:, 1]] + L
        nd = D.copy()
        np.minimum.at(nd, E[:, 1], a); np.minimum.at(nd, E[:, 0], b)
        if np.allclose(nd, D):
            break
        D = nd
    return D


def decimate_now(o, ratio, keep=0.0, protect=None):
    """Collapse-decimate o for good (before any shape key), keeping its open
    edges (hems, the collar, the sleeves' ends, the waistband) and the cloth
    within `keep` of them at full detail; protect: per vertex, 1 = decimate
    freely, toward 0 = keep."""
    if keep > 0 or protect is not None:
        w = np.clip(boundary_dist(o) / keep - 1.0, 0.0, 1.0) if keep > 0 else np.ones(len(o.data.vertices))
        if protect is not None:
            w = np.minimum(w, protect)
        keep = 1.0
        vg = o.vertex_groups.new(name='_decimate')
        for i, x in enumerate(w):
            vg.add([i], float(x), 'REPLACE')
    n0 = len(o.data.polygons)
    d = o.modifiers.new('DecimateNow', 'DECIMATE'); d.ratio = ratio; d.use_collapse_triangulate = True
    if keep > 0:
        d.vertex_group = '_decimate'; d.vertex_group_factor = 1.0
    with bpy.context.temp_override(object=o, active_object=o):
        bpy.ops.object.modifier_move_to_index(modifier='DecimateNow', index=0)
        bpy.ops.object.modifier_apply(modifier='DecimateNow')
    if keep > 0:
        o.vertex_groups.remove(o.vertex_groups['_decimate'])
    log('decimated %s: %d -> %d faces' % (o.name, n0, len(o.data.polygons)))


def mean_edge(o):
    me = o.data
    return float(np.mean([(me.vertices[e.vertices[0]].co - me.vertices[e.vertices[1]].co).length for e in me.edges]))


SPREAD_IT = 25                      # (arms_down_key: how far the press spreads over the shirt, in edges)
if NPC:
    # a city person is seen from metres away: lighter garments (their hems, cuffs
    # and collar at full detail) and shoes
    e0 = mean_edge(bpy.data.objects['TShirt'])
    decimate_now(bpy.data.objects['TShirt'], 0.42, keep=0.02)
    # the press spreads as far over the lighter shirt as over the player's (a
    # diffusion's reach goes with the edge length times the square root of the steps)
    SPREAD_IT = max(4, round(25 * (e0 / mean_edge(bpy.data.objects['TShirt'])) ** 2))
    log('press spread: %d steps' % SPREAD_IT)
    decimate_now(bpy.data.objects['Jeans'], 0.3, keep=0.02)
    for s_ in 'LR':
        decimate_now(bpy.data.objects['Socks_' + s_], 0.5, keep=0.006)
    DECIMATE = {'Shoe_': 0.06, 'ShoeCollar_': 0.15, 'Laces_': 0.22, 'Sole_': 0.4, 'Eyelets_': 0.4}
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
    r = (0.115 if dy else 0.15) * XP
    cy = -0.02 + TY
    cloth_bone('cloth_shirt_' + nm, 'pelvis', (dx * r, cy + dy * r, ZN(1.12)), (dx * (r + 0.02), cy + dy * (r + 0.02), ZN(0.92)))
for s in 'LR':
    J, E = REST['upperarm_' + s]
    cloth_bone('cloth_sleeve_' + s, 'upperarm_' + s, J + (E - J) * 0.25, J + (E - J) * 0.6)
    K, An = REST['calf_' + s]
    cloth_bone('cloth_jeans_' + s, 'calf_' + s, K + (An - K) * 0.1, K + (An - K) * 0.9)
    cloth_bone('cloth_hem_' + s, 'pelvis', *REST['thigh_' + s])     # at the hip joint (see hem_bones)
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
th = np.arctan2(Ps[:, 0], -(Ps[:, 1] + 0.02 - TY))            # 0 front, +-pi back, +pi/2 left (x>0)
# the hem's front and sides follow the jeans and the thighs (see dress_character.py);
# the back of the hem gets its own cloth bone, so it can swing behind the walk
HEM_SHARE = {'F': 0.0, 'L': 0.0, 'R': 0.0, 'B': 0.45}
hem = np.clip((ZN(1.12) - Ps[:, 2]) / ZD(1.12, 0.92, 0.2), 0, 1) ** 1.2 * (np.abs(Ps[:, 0]) < 0.2 * XP)
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
    c = 0.55 * np.clip((t - 0.25) / 0.25, 0, 1) * (Ps[:, 0] * sx > 0.2 * XS) * np.clip((0.11 * XS - r) / 0.025, 0, 1)
    blend_into(shirt, 'cloth_sleeve_' + s, c)
Pj = np.array([v.co[:] for v in jeans.data.vertices])
for s, sx in (('L', 1), ('R', -1)):
    side = Pj[:, 0] * sx > 0.02
    c = 0.3 * np.clip((ZN(0.46) - Pj[:, 2]) / ZD(0.46, 0.26, 0.2), 0, 1) * np.clip((Pj[:, 2] - ZN(0.14)) / ZD(0.22, 0.14, 0.08), 0, 1) * side
    blend_into(jeans, 'cloth_jeans_' + s, c)
    blend_into(jeans, 'foot_' + s, 0.55 * np.clip((ZN(0.15) - Pj[:, 2]) / ZD(0.15, 0.08, 0.07), 0, 1) * side)

# The front of the hem rests on the thighs (dress_character.py weights it to
# them). Over a thigh that comes forward it rises with it, but over a thigh
# that goes back cloth just hangs. So the front's thigh weights go to the hem
# bones, which copy a thigh's turn only while it is forward (hem_bones below),
# and the change from the left thigh to the right one is spread over 18 cm
# (from the two thighs' own weights it was 7 cm: one leg forward and the other
# back tore the hem's middle into a step).
TH = {s: {shirt.vertex_groups[n].index for n in ('thigh_' + s, 'thigh_twist_' + s) if n in shirt.vertex_groups} for s in 'LR'}
HB = {s: shirt.vertex_groups.new(name='cloth_hem_' + s) for s in 'LR'}
n_hem = 0
for v in shirt.data.vertices:
    fr = ss((0.02 + TY - v.co.y) / 0.07)              # 0 at the side seam, 1 from 5 cm in front of it
    if fr <= 0:
        continue
    take = 0.0
    for g in v.groups:
        if g.group in TH['L'] or g.group in TH['R']:
            take += g.weight * fr; g.weight *= (1 - fr)
    if take < 1e-4:
        continue
    sl = ss((v.co.x + 0.09 * XP) / (0.18 * XP))
    for s, k in (('L', sl), ('R', 1 - sl)):
        if take * k >= 1e-4:
            HB[s].add([v.index], take * k, 'REPLACE')
    n_hem += 1
log('hem: %d shirt points on the hem bones' % n_hem)

# glTF keeps a vertex's 4 strongest bones: keep the same here, so the checks
# below see what the game will show
DEFORM = {b.name for b in arm.data.bones if b.use_deform}
cut = {}
for o in MESHES:
    names = {g.index: g.name for g in o.vertex_groups}
    rm = defaultdict(list)
    for v in o.data.vertices:
        ws = sorted(((g.weight, g.group) for g in v.groups if names[g.group] in DEFORM and g.weight > 0), reverse=True)
        for w, gi in ws[4:]:
            rm[gi].append(v.index)
    for gi, idx in rm.items():
        o.vertex_groups[gi].remove(idx)
    if rm:
        cut[o.name] = sum(len(i) for i in rm.values())
log('weights beyond the 4 strongest bones dropped:', cut)

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
HEEL_OFF = Vector((0, 0.055 * FL, -0.098 * AZ))                 # heel contact point from the ankle
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


def curl_fingers(s, base, tip, thumb=8.0, thumb_in=0.0):
    """Curl the fingers (deg at the base and the middle joints); thumb: turn
    the thumb out of the palm; thumb_in: fold it over the fingers (a fist)."""
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
    if thumb_in:
        for n, k in (('thumb01_', 0.6), ('thumb02_', 1.0), ('thumb03_', 0.5)):
            rot(n + s, PB[n + s].bone.matrix_local.col[0].to_3d(), -thumb_in * k)


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


clear()
FLEX0 = {s: thigh_flex(s) for s in 'LR'}


def hem_bones():
    """Each hem bone turns with its thigh (about the hip joint) while the thigh
    is forward of its rest, and stays with the pelvis while it is back."""
    pel = delta(PB['pelvis'])
    for s in 'LR':
        k = ss((thigh_flex(s) - FLEX0[s]) / 4.0)
        if k <= 0:
            continue
        q = Quaternion().slerp((pel.inverted() @ delta(PB['thigh_' + s])).to_quaternion(), k)
        h = REST['thigh_' + s][0]
        pb = PB['cloth_hem_' + s]
        pb.matrix = pel @ Matrix.Translation(h) @ q.to_matrix().to_4x4() @ Matrix.Translation(-h) @ pb.bone.matrix_local
    upd()


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
        a = Vector((SX[s] * 0.125 * XH, 0.03 + DY_ANK, ANK0[s].z))
        leg(s, a, 0.0)
        rot('clavicle_' + s, Y, -SX[s] * 0.6 * (1 + math.sin(2 * math.pi * 2 * t)))
        arm_pose(s, IDLE_DOWN + DARM, 3, 3)    # straight, hanging beside the body
        curl_fingers(s, 8, 12)
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
        front, back = YO - P['front'], YO - P['front'] + stride * beta
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
        curl_fingers(s, P['fingers'], P['fingers'] * 1.5, P['thumb'], P['thumb_in'])
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
            clear_pow=1.4, kick=0.04, arm_down=49.0, arm_swing=18.0, arm_fwd=2.0, elbow=3.0, elbow_swing=0.0,
            fingers=8.0, thumb=8.0, thumb_in=0.0, cloth=1.0, cycle=0.94, ride=0.6)
RUN = dict(run=True, stride=2.4, beta=0.38, front=0.23, width=0.125, drop=0.05, bob=0.03, sway=0.008, bob_phase=0.19,
           yaw=6.0, roll=2.5, lean=7.0, strike=-6.0, toe_off=28.0, flat_at=0.06, heel_off=0.2, clear=0.19,
           clear_pow=0.8, kick=0.17, arm_down=42.0, arm_swing=32.0, arm_fwd=4.0, elbow=78.0, elbow_swing=16.0,
           fingers=55.0, thumb=8.0, thumb_in=45.0, cloth=2.0, cycle=0.6, ride=0.95)
if NPC:
    # distances by the length of the legs, the cycle's time by its square root
    # (people of different sizes walk alike at the same Froude number)
    for P in (WALK, RUN):
        for k in ('stride', 'front', 'clear', 'kick', 'drop', 'bob', 'sway'):
            P[k] *= K_LEG
        P['width'] *= XH; P['cycle'] *= math.sqrt(K_LEG); P['arm_down'] += DARM


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


# ---- fighting: a stance, a guard, a right straight, a front kick, taking a hit (each 0.4-0.7 s, from and back
# to the guard, so the game can chain them)
def lerp(a, b, t):
    return a + (b - a) * t


def stance(k=1.0):
    """Knees a little bent, the left foot forward and the right one back, turned a little (k: how far into it)."""
    move('pelvis', (0, 0, -0.035 * k * K_LEG))
    rot('pelvis', Zax, 10 * k)
    for s, f in (('L', 0.12), ('R', -0.09)):
        leg(s, Vector((SX[s] * 0.13 * XH, 0.03 + DY_ANK - f * k * K_LEG, ANK0[s].z)), 0.0)


def guard_arm(s, k=1.0):
    """The fist up by the chin, the elbow in front of the ribs."""
    arm_pose(s, lerp(IDLE_DOWN, IDLE_DOWN - 6, k) + DARM, 55 * k, 100 * k)
    curl_fingers(s, 85 * k, 95 * k, 8.0, 50 * k)


def pose_punch(t):
    """A right straight (0.5 s): the hips and shoulders turn into it as the arm shoots out (at 0.15 s) and back."""
    e = ss(t / 0.3) if t < 0.3 else (1.0 if t < 0.42 else 1 - ss((t - 0.42) / 0.5))
    stance()
    rot('spine02', Zax, -12 * e); rot('spine03', Zax, -10 * e); rot('neck', Zax, 14 * e)
    rot('clavicle_R', Zax, -10 * e)
    guard_arm('L')
    arm_pose('R', lerp(IDLE_DOWN - 6, IDLE_DOWN - 4, e) + DARM, lerp(55, 86, e), lerp(100, 6, e))
    curl_fingers('R', 85, 95, 8.0, 50)
    hem_over_thighs()


def pose_kick(t):
    """A right front kick (0.7 s): the knee comes up, the leg snaps out low (0.3 s) and back down."""
    up = ss(t / 0.3) if t < 0.3 else (1.0 if t < 0.55 else 1 - ss((t - 0.55) / 0.45))
    ext = ss((t - 0.18) / 0.17) if t < 0.35 else (1.0 if t < 0.43 else 1 - ss((t - 0.43) / 0.17))
    stance(1 - up)
    rot('pelvis', X, 9 * up)                                  # leaning back as the leg comes up
    rot('spine02', X, -4 * up)
    leg('L', Vector((SX['L'] * 0.13 * XH, 0.03 + DY_ANK - 0.05 * up * K_LEG, ANK0['L'].z)), 0.0)
    H = PB['thigh_R'].head.copy(); Ln = L1['R'] + L2['R']
    rest = Vector((SX['R'] * 0.13 * XH, 0.03 + DY_ANK + 0.09 * (1 - up) * K_LEG, ANK0['R'].z))
    # (a low kick, at the knees: the thigh comes up to about 45 degrees. Higher, the jeans' waist bunches up
    # through the shirt's front at the hip's crease, as at the wheel)
    chamber = H + Vector((0, -0.20 * Ln, -0.88 * Ln))          # the knee comes up, the foot under it
    out = H + Vector((0, -0.55 * Ln, -0.81 * Ln))              # the leg out in front, the foot at the knees' height
    a = rest.lerp(chamber, up).lerp(out, ext)
    leg('R', a, lerp(0.0, -25.0, ext))
    for s_ in 'LR':
        guard_arm(s_)
    hem_over_thighs()
    cloth('cloth_shirt_F', X, -14 * up); cloth('cloth_shirt_R', X, -18 * up)   # the hem lifted clear of the jeans' bunching


def pose_hit(t):
    """Hit (0.4 s): the head and the chest snap back, a half step back, the guard kept up."""
    h = math.sin(math.pi * min(1.0, t / 0.75)) if t < 0.75 else 0.0
    stance()
    move('pelvis', (0, 0.05 * h * K_LEG, 0.0))
    rot('spine02', X, 4 * h); rot('spine03', X, 3 * h); rot('neck', X, 6 * h); rot('head', X, 12 * h)
    rot('head', Zax, 10 * h)
    for s in 'LR':
        guard_arm(s)                       # (the guard kept: lowered, a heavy body's arms press into the shirt's sides)
    hem_over_thighs()


LIE = dict(spine=-4.0, neck=-6.0, swing=-6.0, legs=0.0, drop=0.0)


def pose_lie(t):
    """Knocked down: on the back, head towards where they were facing away from,
    the legs a little apart and turned out, the arms beside the body, the head
    turned to a side. The pose rests on the ground (lie_solve)."""
    rot('pelvis', X, -90)
    rot('spine01', X, 0.4 * LIE['spine']); rot('spine02', X, 0.3 * LIE['spine']); rot('spine03', X, 0.3 * LIE['spine'])
    rot('neck', X, 0.5 * LIE['neck']); rot('head', X, 0.5 * LIE['neck'])
    rot('head', Zax, 24)
    for s in 'LR':
        rot('thigh_' + s, X, LIE['legs'] + (1.5 if s == 'L' else -1.5))
        rot('thigh_' + s, Y, -SX[s] * 6); rot('thigh_' + s, Zax, SX[s] * 11)
        rot('calf_' + s, X, 5 if s == 'L' else 3)
        rot('foot_' + s, X, 16)
        arm_pose(s, IDLE_DOWN - 20 + DARM, LIE['swing'], 16)
        curl_fingers(s, 20, 26, 12)
    move('pelvis', (0, 0, -LIE['drop']))
    cloth('cloth_shirt_B', X, 3)


def lie_solve():
    """Lay the pose on the ground: the lowest point at 3 mm, and the upper back,
    the head, the arms and the heels down on it with the seat (by bending the
    spine, the neck, the shoulders and the hips), as a body lies."""
    for m, v in SAVED:
        m.show_viewport = v
    regions = {}
    for o in MESHES:
        R = np.array([v.co[:] for v in o.data.vertices])
        if len(R) == 0:
            continue
        z, ax = R[:, 2], np.abs(R[:, 0])
        sel = {'head': z > ZN(1.56), 'back': (z > ZN(1.15)) & (z < ZN(1.48)) & (ax < 0.15 * XS),
               'seat': (z > ZN(0.75)) & (z < ZN(1.05)) & (ax < 0.2 * XP), 'feet': z < ZN(0.2),
               'arms': (ax > BM['shx'] + 0.1) & (z > ZN(0.9)) & (z < ZN(1.45))}
        regions[o.name] = sel

    def contacts():
        clear(); pose_lie(0.0); hem_bones()
        low = dict.fromkeys(('head', 'back', 'seat', 'feet', 'arms'), 9.0); allmin = 9.0
        for o in MESHES:
            P, _ = evaluated(o)
            if len(P) == 0:
                continue
            M = np.array(o.matrix_world)
            Pz = (P @ M[:3, :3].T + M[:3, 3])[:, 2]              # (world: the ground is z = 0)
            allmin = min(allmin, float(Pz.min()))
            sel = regions.get(o.name)
            if sel is None or len(sel['head']) != len(P):     # (the decimated shoes: all feet)
                sel = {'feet': np.ones(len(P), bool)} if o.name.startswith(FOOTWEAR) else {}
            for k, s in sel.items():
                if s.any():
                    low[k] = min(low[k], float(Pz[s].min()))
        return low, allmin
    for it in range(8):
        low, allmin = contacts()
        LIE['drop'] += allmin - 0.003
        LIE['spine'] = min(max(LIE['spine'] - math.degrees((low['back'] - low['seat']) / (0.33 * K_LEG)), -22.0), 8.0)
        LIE['neck'] = min(max(LIE['neck'] - math.degrees((low['head'] - low['back']) / 0.16), -30.0), 32.0)
        LIE['swing'] = min(max(LIE['swing'] - math.degrees((low['arms'] - low['back']) / 0.42), -30.0), 10.0)
        LIE['legs'] = min(max(LIE['legs'] + math.degrees((low['feet'] - low['seat']) / (0.8 * K_LEG)), -20.0), 12.0)
    low, allmin = contacts()
    LIE['drop'] += allmin - 0.003
    low, allmin = contacts()
    REPORT['lie'] = {'angles': {k: round(v, 2) for k, v in LIE.items()},
                     'lowest_mm': {k: round(v * 1000, 1) for k, v in low.items()}, 'ground_mm': round(allmin * 1000, 1)}
    log('lie: %s' % json.dumps(REPORT['lie']))
    clear()
    for m, _ in SAVED:
        m.show_viewport = False


# (the fighting moves play once; they start and end in the guard, so they are baked and checked as loops are)
FIGHT = [('Punch', pose_punch, 15, 'once'), ('Kick', pose_kick, 21, 'once'), ('Hit', pose_hit, 12, 'once')]
if NPC:
    ANIMS = [('Idle', pose_idle, 120, True), ('Walk', lambda t: gait(t, WALK), round(WALK['cycle'] * FPS), True),
             ('Run', lambda t: gait(t, RUN), round(RUN['cycle'] * FPS), True),
             ('Drive', pose_drive, 2, False), ('Lie', pose_lie, 2, False)] + FIGHT
else:
    ANIMS = [('Idle', pose_idle, 120, True), ('Walk', lambda t: gait(t, WALK), round(WALK['cycle'] * FPS), True),
             ('Run', lambda t: gait(t, RUN), round(RUN['cycle'] * FPS), True),
             ('Drive', pose_drive, 2, False), ('Jump', pose_jump, 2, False), ('Lie', pose_lie, 2, False)] + FIGHT
N_WALK, N_RUN = round(WALK['cycle'] * FPS), round(RUN['cycle'] * FPS)
FOOTWEAR = ('Shoe_', 'ShoeCollar_', 'Laces_', 'Sole_', 'Eyelets_', 'Socks_')
lie_solve()
arm.animation_data_create()
ACTIONS = {}
for name, fn, n, loop in ANIMS:
    act = bpy.data.actions.new(name); act.use_fake_user = True
    arm.animation_data.action = act
    frames = range(n + 1)
    for f in frames:
        clear()
        fn((f % n) / n if loop else 0.0)
        hem_bones()
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

# ------------------------------------------------------------------ the shirt gives way under the arms
# With the arms hanging straight down, the inner arm rests against the side of
# the body, where the shirt stands about a centimetre off it. Cloth there is
# pressed in: a shape key moves the shirt's sides inward just enough to clear
# the arm and the sleeve in every frame (the body under the shirt is hidden by
# the mask, so the cloth may go into it). ArmsDown is for Idle and Walk, where
# the hands hang by the hips; ArmsDownRun for Run, where the elbows are bent and
# only the upper arm is by the body (ArmsDown there would show a dent). The game
# sets each to its animations' weight; Drive and Jump hold the arms away.
PRESS = {'ArmsDown': ('Idle', 'Walk'), 'ArmsDownRun': ('Run',)}
PRESS_FRAMES = {'Idle': range(0, 120, 10), 'Walk': range(0, N_WALK, 2), 'Run': range(N_RUN)}
GAP = 0.008                         # (more than the thickness of the gaps between the cloth points)


def skin_rotation(o, vi):
    """The 3x3 rotation the armature currently applies to vertex vi of o."""
    M = np.zeros((3, 3)); tot = 0.0
    for g in o.data.vertices[vi].groups:
        pb = PB.get(o.vertex_groups[g.group].name)
        if pb is None or g.weight <= 0:
            continue
        M += np.array((pb.matrix @ pb.bone.matrix_local.inverted()).to_3x3()) * g.weight; tot += g.weight
    return M / tot if tot > 0 else np.eye(3)


def arms_down_key(name):
    SP0 = np.array([v.co[:] for v in shirt.data.vertices])
    spolys = [tuple(p.vertices) for p in shirt.data.polygons]
    # the sleeves stay as they are (cut as the checks cut them): only the shirt's
    # body under the arms gives way
    sf = sleeve_faces(SP0, spolys, shirt)
    sleeve = np.zeros(len(SP0), bool)
    for s in 'LR':
        for i in np.where(sf[s])[0]:
            sleeve[list(spolys[i])] = True
    sleeve_polys = {s: [spolys[i] for i in np.where(sf[s])[0]] for s in 'LR'}
    region = (np.abs(SP0[:, 0]) > 0.10 * XC) & (SP0[:, 2] > ZN(1.04)) & (SP0[:, 2] < ZN(1.40)) & ~sleeve
    cand = np.where(region)[0]
    arm_g = {s: {i for i, g in enumerate(body.vertex_groups) if g.name.endswith('_' + s) and
                 any(k in g.name for k in ('upperarm', 'lowerarm', 'hand', 'thumb', 'index', 'middle', 'ring', 'pinky'))} for s in 'LR'}
    armw = {s: np.zeros(len(body.data.vertices)) for s in 'LR'}
    for v in body.data.vertices:
        for g in v.groups:
            for s in 'LR':
                if g.group in arm_g[s]:
                    armw[s][v.index] += g.weight
    # every visible bit of skin that moves with the arm, the armpit's included
    arm_polys = {s: [tuple(p.vertices) for p in body.data.polygons if p.material_index in skins
                     and armw[s][list(p.vertices)].max() > 0.15 and VIS[list(p.vertices)].all()] for s in 'LR'}
    need = np.zeros((len(SP0), 3)); need_len = np.zeros(len(SP0))
    samples = [(a, f) for a in PRESS[name] for f in PRESS_FRAMES[a]]
    for act, f in samples:
        arm.animation_data.action = ACTIONS[act]; scene.frame_set(f)
        Pb, _ = evaluated(body); Ps, _ = evaluated(shirt)
        obst = {s: [BVHTree.FromPolygons([Vector(p) for p in Pb], arm_polys[s]),
                    BVHTree.FromPolygons([Vector(p) for p in Ps], sleeve_polys[s])] for s in 'LR'}

        def clearance(co, s):
            c = 1.0
            for bvh in obst[s]:
                loc, nrm, idx, d = bvh.find_nearest(co, 0.05)
                if loc is None or d >= GAP:
                    continue
                sd = (co - loc).dot(nrm)
                c = min(c, sd if (d < 1e-5 or abs(sd) >= 0.9 * d) else d)
            return c
        for i in cand:
            s = 'L' if SP0[i, 0] > 0 else 'R'
            co = Vector(Ps[i]); p0 = co.copy(); inward = Vector((-SX[s], 0, 0))
            for _ in range(30):
                if clearance(co, s) >= GAP:
                    break
                co = co + inward * 0.002
            if (co - p0).length < 1e-6:
                continue
            rd = np.linalg.solve(skin_rotation(shirt, i), np.array(co - p0))
            if np.linalg.norm(rd) > need_len[i]:
                need[i] = rd; need_len[i] = np.linalg.norm(rd)
    arm.animation_data.action = None; scene.frame_set(0); clear()
    bm = bmesh.new(); bm.from_mesh(shirt.data); bm.verts.ensure_lookup_table()
    E = np.array([(e.verts[0].index, e.verts[1].index) for e in bm.edges]); bm.free()

    def spread():
        """The push spread over the cloth around it, never below what each point needs."""
        D = need.copy()
        for _ in range(SPREAD_IT):
            S = np.zeros_like(D); cnt = np.zeros(len(D))
            np.add.at(S, E[:, 0], D[E[:, 1]]); np.add.at(S, E[:, 1], D[E[:, 0]])
            np.add.at(cnt, E[:, 0], 1); np.add.at(cnt, E[:, 1], 1)
            D = 0.5 * D + 0.5 * S / np.maximum(cnt, 1)[:, None]
            L = np.linalg.norm(D, axis=1)
            short = (need_len > 0) & (L < need_len)
            D[short] *= (need_len[short] / np.maximum(L[short], 1e-9))[:, None]
        return D
    if not shirt.data.shape_keys:
        shirt.shape_key_add(name='Basis', from_mix=False)
    key = shirt.shape_key_add(name=name, from_mix=False)
    for kb in shirt.data.shape_keys.key_blocks[1:]:
        kb.value = 0.0
    # The push above is measured at the cloth's corners: skin can still bulge
    # into a face between them. Measure the arm's skin against the faces, as the
    # checks do, and push those faces' corners further until it clears them.
    press_faces = [f for f in spolys if region[list(f)].all()]
    body_bvh0 = BVHTree.FromPolygons([Vector(p) for p in SP0], [spolys[i] for i in range(len(spolys)) if not (sf['L'][i] or sf['R'][i])])
    skin_pts = {}
    for s in 'LR':
        vs = sorted({v for f in arm_polys[s] for v in f})
        pts = [('v', v) for v in vs] + [('f', f) for f in arm_polys[s]]
        rest = [CO0[v] if k == 'v' else CO0[list(v)].mean(0) for k, v in pts]
        # (skin that is under the shirt's body at rest, in the armpit, is not the arm pressing on it)
        skin_pts[s] = [p for p, q in zip(pts, rest) if not under_at_rest(body_bvh0, Vector(q))]
    SKIN_GAP = 0.003
    for rnd in range(6):
        D = spread()
        for i, v in enumerate(key.data):
            v.co = Vector(SP0[i] + D[i])
        key.value = 1.0
        extra = {}
        for act, f in samples:
            arm.animation_data.action = ACTIONS[act]; scene.frame_set(f)
            Pb, _ = evaluated(body); Ps, _ = evaluated(shirt)
            bvh = BVHTree.FromPolygons([Vector(p) for p in Ps], press_faces)
            for s in 'LR':
                for k, v in skin_pts[s]:
                    q = Vector(Pb[v] if k == 'v' else Pb[list(v)].mean(0))
                    loc, nrm, fi, d = bvh.find_nearest(q, SKIN_GAP + 0.002)
                    if loc is None:
                        continue
                    sd = (q - loc).dot(nrm)
                    if d > 1e-5 and abs(sd) < 0.5 * d:
                        continue                               # beside the cloth's edge, not on it
                    if sd >= SKIN_GAP:
                        continue
                    push = -nrm * (SKIN_GAP - sd + 0.001)
                    for c in press_faces[fi]:
                        rd = np.linalg.solve(skin_rotation(shirt, c), np.array(push))
                        if c not in extra or np.linalg.norm(rd) > np.linalg.norm(extra[c]):
                            extra[c] = rd
        arm.animation_data.action = None; scene.frame_set(0); clear(); key.value = 0.0
        if not extra:
            break
        for c, rd in extra.items():
            need[c] = need[c] + rd; need_len[c] = np.linalg.norm(need[c])
        log('%s: round %d, %d cloth points pushed further' % (name, rnd + 1, len(extra)))
    log('%s: %d shirt points pushed, up to %.1f mm, %d moved after spreading%s' %
        (name, (need_len > 0).sum(), need_len.max() * 1000, (np.linalg.norm(D, axis=1) > 1e-4).sum(),
         '' if not extra else ' (STILL SHORT)'))
    REPORT.setdefault('press_mm', {})[name] = round(float(need_len.max() * 1000), 1)


def set_arms_down(action_name):
    for name, acts in PRESS.items():
        kb = shirt.data.shape_keys.key_blocks.get(name) if shirt.data.shape_keys else None
        if kb is not None:
            kb.value = 1.0 if action_name in acts else 0.0

# ------------------------------------------------------------------ the game's mask
# The .blend keeps a band of skin inside the sleeves, for looking into them from
# below. The game's camera is always above and behind the player, with the
# sleeves hanging down, so it never sees in: there, hide the skin under the
# sleeves and in the armpits up to 6 mm from the sleeve's edge (with the arms
# down, that skin would press out through the cloth).
skins = {i for i, m in enumerate(body.data.materials) if m.name in ('MBLab_skin3', 'MBlab_generic')}


def game_mask():
    clear(); arm.animation_data.action = None
    Ps = np.array([v.co[:] for v in shirt.data.vertices])
    spolys = [tuple(p.vertices) for p in shirt.data.polygons]
    bvh = BVHTree.FromPolygons([Vector(p) for p in Ps], spolys)
    cnt = defaultdict(int)
    for f in spolys:
        for k in range(len(f)):
            cnt[tuple(sorted((f[k], f[(k + 1) % len(f)])))] += 1
    hem = sorted({v for e, c in cnt.items() if c == 1 for v in e if abs(Ps[v][0]) > 0.2 * XS})
    kd = KDTree(len(hem))
    for j, v in enumerate(hem):
        kd.insert(Vector(Ps[v]), j)
    kd.balance()
    skin_v = np.zeros(len(body.data.vertices), bool)
    for p in body.data.polygons:
        if p.material_index in skins:
            skin_v[list(p.vertices)] = True
    bmn = bmesh.new(); bmn.from_mesh(body.data); bmn.verts.ensure_lookup_table(); bmn.normal_update()
    nz = np.array([v.normal.z for v in bmn.verts]); bmn.free()
    # (glTF splits vertices along UV seams: decide by position, so the copies agree)
    pos_down = defaultdict(list)
    for v in body.data.vertices:
        pos_down[tuple(np.round(v.co[:], 6))].append(nz[v.index])
    vg = body.vertex_groups['visible']
    hide = []
    for v in body.data.vertices:
        co = v.co
        if not skin_v[v.index] or abs(co.x) < 0.17 * XS or co.z < ZN(1.2):
            continue
        if min(pos_down[tuple(np.round(co[:], 6))]) >= -0.3:
            continue                                   # only the underside of the arm and the armpit
        loc, nrm, idx, d = bvh.find_nearest(co, 0.04)
        if loc is None:
            continue
        sd = (co - loc).dot(nrm)
        if sd >= 0 or (d > 1e-5 and abs(sd) < 0.9 * d):
            continue                                   # not under the shirt
        if kd.find(co)[2] < 0.006:
            continue                                   # the strip right inside the sleeve's edge
        hide.append(v.index)
    vg.remove(hide)
    log('game mask: %d more skin vertices hidden (the arms\' underside inside the sleeves)' % len(hide))


game_mask()

# ------------------------------------------------------------------ checks on every frame
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
PAIRS = [('TShirt', 'Jeans'), ('Jeans', 'Socks_L'), ('Jeans', 'Socks_R'), ('Jeans', 'Shoe_L'), ('Jeans', 'Shoe_R')]
if HAIR is not None:
    # the hair is cloth to the checks: scalp under it stays under it, other skin
    # (the face, the ears, the neck) stays out of it, and none of it goes into the shirt
    GARM['Hair'] = HAIR
    PAIRS.append(('Hair', 'TShirt'))
THICK = {n: next((m.thickness for m in o.modifiers if m.type == 'SOLIDIFY'), 0.0) for n, o in GARM.items()}
TOL = 0.0                           # (LOD1 is checked with a tolerance)


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
FLAG = None                         # (auto_mask: the skin points under the cloth that come out of it)


def sleeve_faces(P, polys, o=None):
    """The shirt's faces that are the left and right sleeves (by the faces'
    rest positions along and around the upper arm). (A city person's by the
    shirt's own weights: the arm's bones over half - on a heavy body the
    torso's side by the armpit is within reach of the arm's axis.)"""
    if NPC:
        names = {g.index: g.name for g in o.vertex_groups}
        out = {}
        for s in 'LR':
            arm_bones = {'upperarm_' + s, 'upperarm_twist_' + s, 'lowerarm_' + s, 'lowerarm_twist_' + s, 'hand_' + s, 'cloth_sleeve_' + s}
            w = np.zeros(len(o.data.vertices))
            for v in o.data.vertices:
                w[v.index] = sum(g.weight for g in v.groups if names[g.group] in arm_bones)
            out[s] = np.array([w[list(f)].mean() > 0.5 for f in polys])
        return out
    C = np.array([P[list(f)].mean(0) for f in polys]); out = {}
    for s in 'LR':
        J, E = REST['upperarm_' + s]; a = np.array((E - J).normalized()); Ln = (E - J).length
        d = C - np.array(J); t = (d @ a) / Ln
        r = np.linalg.norm(d - np.outer(d @ a, a), axis=1)
        out[s] = (t > 0.05) & (r < 0.12 * XS) & (C[:, 0] * SX[s] > 0.12 * XS)
    return out


def cloth_pieces():
    """Each garment as pieces of cloth (the shirt: its body and the two sleeves),
    as face lists of the evaluated mesh."""
    out = {}
    for n, o in GARM.items():
        P, polys = evaluated(o)
        if n == 'TShirt':
            sf = sleeve_faces(P, polys, o)
            for s in 'LR':
                out['TShirt/sleeve_' + s] = (o, [i for i in range(len(polys)) if sf[s][i]])
            taken = set(out['TShirt/sleeve_L'][1]) | set(out['TShirt/sleeve_R'][1])
            out['TShirt/body'] = (o, [i for i in range(len(polys)) if i not in taken])
        else:
            out[n] = (o, list(range(len(polys))))
    return out


def under_at_rest(bvh, co):
    """On the inner side of the nearest cloth, also where that's a fold's edge
    seen at an angle (as on top of the shoulder, where the sleeve is cut from
    the shirt's body)."""
    loc, nrm, idx, d = bvh.find_nearest(co, 0.03)
    return loc is not None and (co - loc).dot(nrm) < -0.5 * max(d, 1e-9)


def patch_nearest(P, fl, idxs, co):
    """The nearest point of the faces fl[j], j in idxs (posed at P): (point, normal, distance)."""
    best = None
    for j in idxs:
        V = [Vector(P[v]) for v in fl[j]]
        n = geometry.normal(V)
        for a in range(1, len(V) - 1):
            cp = geometry.closest_point_on_tri(co, V[0], V[a], V[a + 1])
            d = (co - cp).length
            if best is None or d < best[2]:
                best = (cp, n, d)
    return best


def rest_normals():
    """The skin's outward normal at each check point at rest (vertices', faces')."""
    bm = bmesh.new(); bm.from_mesh(body.data); bm.verts.ensure_lookup_table(); bm.normal_update()
    VN = np.array([v.normal[:] for v in bm.verts]); bm.free()
    out = [VN[i] for i in vis_idx]
    for f in vis_faces:
        n = geometry.normal([Vector(CO0[v]) for v in f])
        out.append(np.array(n[:]))
    return np.array(out)


def ray_under():
    """(The city's people.) Skin whose outward ray meets a piece of cloth from
    inside within 3 cm, that piece being the first cloth it meets, is under it
    (as dress_character.py decides it): skin in a loose sleeve, under a collar
    that stands off the neck. Returns {point: (piece, face of the piece)}."""
    allP, allF, owner = [], [], []
    for name, (o, faces) in PIECES.items():
        P, polys = evaluated(o)
        base = len(allP); allP.extend(Vector(p) for p in P)
        for j, i in enumerate(faces):
            allF.append(tuple(base + v for v in polys[i])); owner.append((name, j))
    bvh = BVHTree.FromPolygons(allP, allF)
    N = rest_normals()
    hit = {}
    for k, (q, n) in enumerate(zip(REST_PTS, N)):
        n = Vector(n)
        if n.length < 1e-9:
            continue
        n.normalize()
        loc, nrm, fi, d = bvh.ray_cast(Vector(q) + n * 1e-4, n, 0.03)
        if loc is not None and nrm.dot(n) > 0:
            hit[k] = owner[fi]
    return hit


def skin_check_setup():
    """In the rest pose, which piece of cloth each visible skin point is under,
    and the patch of that cloth over it (its faces within 3 cm)."""
    clear(); arm.animation_data.action = None
    global PIECES, UNDER, EDGE, PATCH
    PIECES = cloth_pieces()
    UNDER, EDGE, PATCH = {}, {}, {}
    disagree = 0
    HIT = ray_under() if NPC else {}
    for name, (o, faces) in PIECES.items():
        P, polys = evaluated(o)
        fl = [polys[i] for i in faces]
        bvh = BVHTree.FromPolygons([Vector(p) for p in P], fl)
        UNDER[name] = np.array([under_at_rest(bvh, Vector(q)) for q in REST_PTS])
        for k, (nm, j) in HIT.items():
            if nm == name:
                UNDER[name][k] = True
        kdf = KDTree(len(fl))
        for j, f in enumerate(fl):
            kdf.insert(Vector(P[list(f)].mean(0)), j)
        kdf.balance()
        PATCH[name] = {}
        for k in np.where(UNDER[name])[0]:
            PATCH[name][k] = {j for _, j, _ in kdf.find_range(Vector(REST_PTS[k]), 0.03)} | {bvh.find_nearest(Vector(REST_PTS[k]), 0.03)[2]}
            if k in HIT and HIT[k][0] == name:
                PATCH[name][k].add(HIT[k][1])
            PATCH[name][k].discard(None)
            r = patch_nearest(P, fl, PATCH[name][k], Vector(REST_PTS[k]))
            if r is None or (Vector(REST_PTS[k]) - r[0]).dot(r[1]) >= 0:
                disagree += 1
        # skin within 1.5 cm of the piece's edge (a hem, a cut, where a sleeve
        # meets the shirt): "under" and "outside" are not defined there
        cnt = defaultdict(int)
        for i in faces:
            f = polys[i]
            for k in range(len(f)):
                cnt[tuple(sorted((f[k], f[(k + 1) % len(f)])))] += 1
        bv = sorted({v for e, c in cnt.items() if c == 1 for v in e})
        kd = KDTree(max(len(bv), 1))
        for j, v in enumerate(bv):
            kd.insert(Vector(P[v]), j)
        kd.balance()
        EDGE[name] = np.array([len(bv) > 0 and kd.find(Vector(q))[2] < 0.015 for q in REST_PTS])
        if NPC and len(bv):
            # (a city person's) skin outside the piece at rest whose nearest point
            # of it is its edge, within 2.5 cm: that skin is by the edge, and
            # moving it goes over or under the edge (a loose sleeve's hem, a collar
            # standing off the neck), not through the cloth's face
            for k, q in enumerate(REST_PTS):
                if UNDER[name][k] or EDGE[name][k]:
                    continue
                co = Vector(q)
                loc, nrm, fi, d = bvh.find_nearest(co, 0.03)
                if loc is not None and abs((co - loc).dot(nrm)) < 0.95 * d and kd.find(co)[2] < 0.025:
                    EDGE[name][k] = True
    log('skin under the cloth at rest: %d points; the patch test disagrees with the nearest-face test on %d' %
        (sum(int(u.sum()) for u in UNDER.values()), disagree))
    # (skin under some piece at rest, 1.5 cm or more inside its edges: the cloth covers it)
    global COVERED
    COVERED = np.zeros(len(REST_PTS), bool)
    for name in PIECES:
        COVERED |= UNDER[name] & ~EDGE[name]


def check_frame():
    Pb, _ = evaluated(body)
    pts = np.vstack([Pb[vis_idx], np.array([Pb[f].mean(0) for f in vis_faces])])
    S = {}
    for n, o in GARM.items():
        P, polys = evaluated(o)
        S[n] = (P, BVHTree.FromPolygons([Vector(p) for p in P], polys), polys)
    out = {}
    # skin: a point under a piece of cloth stays under it; any other skin (an
    # arm resting on the shirt's side, a hand by the hip) stays outside it
    for name, (o, faces) in PIECES.items():
        g = name.split('/')[0]
        P, _, polys = S[g]
        fl = [polys[i] for i in faces]
        bvh = BVHTree.FromPolygons([Vector(p) for p in P], fl)
        n, worst, where = 0, 0.0, WHERE.setdefault('body>' + name, [])
        under, edge = UNDER[name], EDGE[name]
        for k, q in enumerate(pts):
            if edge[k]:
                continue
            co = Vector(q)
            loc, nrm, fi, d = bvh.find_nearest(co, 0.03 if under[k] else 0.02)
            if loc is None:
                continue
            if under[k] and fi not in PATCH[name][k]:
                # skin under the cloth is measured against the cloth that was over
                # it, not a fold of the same piece pressed close (as the underarm
                # of a sleeve, folded between the arm and the body)
                loc, nrm, d = patch_nearest(P, fl, PATCH[name][k], co)
            sd = (co - loc).dot(nrm)
            if d > 1e-5 and abs(sd) < 0.95 * d:
                continue                                 # beyond the cloth's edge
            bad = ((sd + 0.0003) if under[k] else (-sd)) - TOL
            if FLAG is not None and COVERED[k] and bad > -0.0008:
                FLAG.add(k)
            if bad > 0:
                n += 1; worst = max(worst, bad)
                if len(where) < 3:
                    where.append(tuple(round(float(x), 3) for x in REST_PTS[k]) +
                                 ((('under' if under[k] else 'outside'), round(float(sd), 4), round(float(d), 4)) if NPC else ()))
        out['body>' + name] = (n, worst)
    for a, b in PAIRS:
        ra = np.array([v.co[:] for v in GARM[a].data.vertices])
        out[a + '>' + b] = through(S[a][0], S[b][1], THICK[b] + 0.0002 - TOL, outside=True, maxd=0.02,
                                   rest=ra if len(ra) == len(S[a][0]) else None, where=WHERE.setdefault(a + '>' + b, []))
    return out


for name in PRESS:
    arms_down_key(name)


def check_setup_points():
    global VIS, vis_faces, vis_idx, REST_PTS
    VIS = np.zeros(len(body.data.vertices), bool)
    for v in body.data.vertices:
        for g in v.groups:
            if g.group == vis_g and g.weight > 0.5:
                VIS[v.index] = True
    vis_faces = [list(p.vertices) for p in body.data.polygons if p.material_index in skins and VIS[list(p.vertices)].all()]
    vis_idx = np.where(VIS & SKV)[0]
    REST_PTS = np.vstack([CO0[vis_idx], np.array([CO0[f].mean(0) for f in vis_faces])])


def auto_mask():
    """Skin that is under the clothes or the hair at rest and
    comes out through them, or within 0.5 mm of it, in a frame of an animation
    or of a Walk/Run blend (the armpits of a heavy body as the arms come down,
    a point of the chest breathing) is hidden by the mask, as game_mask hides
    the player's arms' underside; so is skin under a sleeve at rest that goes
    behind the shirt's side (a heavy body's armpit). Only skin 1.5 cm or more
    inside a piece of cloth's edges at rest (COVERED), so the cloth still
    covers where it was; skin outside the cloth is never hidden."""
    global FLAG
    skin_check_setup()
    FLAG = set()
    poses = 0
    for name, fn, n, loop in ANIMS:
        arm.animation_data.action = ACTIONS[name]; set_arms_down(name)
        for f in range(0, n, 2 if n > 20 else 1):
            scene.frame_set(f); check_frame(); poses += 1
    keys = shirt.data.shape_keys.key_blocks
    for t in (0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875):
        pose = {}
        for act, nf in (('Walk', N_WALK), ('Run', N_RUN)):
            arm.animation_data.action = ACTIONS[act]; scene.frame_set(round(t * nf))
            pose[act] = {pb.name: (pb.rotation_quaternion.copy(), pb.location.copy()) for pb in PB}
        arm.animation_data.action = None
        for w in (0.25, 0.5, 0.75):
            for pb in PB:
                (qa, la), (qb, lb) = pose['Walk'][pb.name], pose['Run'][pb.name]
                pb.rotation_quaternion = qa.slerp(qb, w); pb.location = la.lerp(lb, w)
            keys['ArmsDown'].value = 1 - w; keys['ArmsDownRun'].value = w
            upd(); check_frame(); poses += 1
    arm.animation_data.action = None; scene.frame_set(0); clear(); set_arms_down('Rest')
    WHERE.clear()
    nv = len(vis_idx); hide = set()
    for k in FLAG:
        if k < nv:
            hide.add(int(vis_idx[k]))
        else:
            hide.update(int(v) for v in vis_faces[k - nv])
    FLAG = None
    body.vertex_groups['visible'].remove(sorted(hide))
    check_setup_points()
    REPORT['auto_mask'] = {'poses': poses, 'skin_points': len(hide)}
    log('auto mask: %d skin points under the cloth that come out of it (in %d poses) hidden' % (len(hide), poses))


auto_mask()                          # (the player too: a guard or a kick bends the arms and the hips further than walking)
skin_check_setup()
summary = {}
for name, fn, n, loop in [('Rest', None, 1, False)] + ANIMS:
    arm.animation_data.action = ACTIONS.get(name)
    set_arms_down(name)
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
set_arms_down('Rest')
REPORT['checks'] = summary

# ------------------------------------------------------------------ the cloth springs' reach
# In the game, springs swing the cloth bones further when the player speeds up,
# stops or turns (city/index.html, CLOTH_SPRINGS: keep the two tables the
# same). Each swing is held within these limits, in radians: p about the
# character's right (+: the cloth swings back), r about his forward axis
# (applied first). Cloth can't swing into the body, so the back of the hem swings
# back freely but forward hardly at all; the sleeves and the jeans legs have the
# arm or the leg inside, so they only sway. Every spring at its limits together
# is checked on frames of Idle, Walk and Run.
SPRINGS = {'cloth_shirt_B': ((-0.04, 0.35), (-0.12, 0.12)),
           'cloth_sleeve_L': ((-0.07, 0.1), (-0.06, 0.06)), 'cloth_sleeve_R': ((-0.07, 0.1), (-0.06, 0.06)),
           'cloth_jeans_L': ((-0.08, 0.08), (-0.05, 0.05)), 'cloth_jeans_R': ((-0.08, 0.08), (-0.05, 0.05))}
REPORT['springs'] = {n: {'p': list(p), 'r': list(r)} for n, (p, r) in SPRINGS.items()}


def spring_reach():
    frames = [('Idle', 0), ('Idle', 60)] + [('Walk', f) for f in range(0, N_WALK, 4)] + [('Run', f) for f in range(0, N_RUN, 3)]
    combos = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    right, fwd = Vector((1, 0, 0)), Vector((0, -1, 0))
    worst = {}
    for act, f in frames:
        arm.animation_data.action = ACTIONS[act]; set_arms_down(act); scene.frame_set(f)
        basis = {pb.name: pb.matrix_basis.copy() for pb in PB}
        arm.animation_data.action = None
        for sp, sr in combos:
            for n, mb in basis.items():
                PB[n].matrix_basis = mb
            upd()
            for n, (pl, rl) in SPRINGS.items():
                a = pl[1] if sp > 0 else (pl[0] if sp < 0 else 0.0)
                b = rl[1] if sr > 0 else (rl[0] if sr < 0 else 0.0)
                pb = PB[n]; h = pb.head.copy()
                pb.matrix = Matrix.Translation(h) @ Matrix.Rotation(a, 4, right) @ Matrix.Rotation(b, 4, fwd) @ Matrix.Translation(-h) @ pb.matrix
            upd()
            for k, (cnt, w) in check_frame().items():
                if cnt:
                    key = '%s p%+d r%+d' % (k, sp, sr)
                    c0, w0 = worst.get(key, (0, 0.0)); worst[key] = (c0 + cnt, max(w0, w))
    clear(); set_arms_down('Rest')
    log('check springs at their limits, %d frames x %d: %s' % (len(frames), len(combos), 'OK' if not worst else
        {k: (c, round(w * 1000, 1)) for k, (c, w) in sorted(worst.items())}))
    for k, v in WHERE.items():
        if v:
            log('      %s at rest positions %s' % (k, v))
    WHERE.clear()
    REPORT['checks']['springs at their limits'] = {k: dict(points=c, worst_mm=round(w * 1000, 1)) for k, (c, w) in worst.items()}


# (the city's people have no springs in the game: their cloth swings as baked in the animations)
if not NPC:
    spring_reach()


def blend_check():
    """Speeding up or slowing down, the game blends Walk and Run, in step (as its
    animation mixer does: rotations slerped, the pelvis's position lerped), each
    press shape at its animation's weight."""
    nW, nR = round(WALK['cycle'] * FPS), round(RUN['cycle'] * FPS)
    worst, n = {}, 0
    keys = shirt.data.shape_keys.key_blocks
    for t in (0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875):
        pose = {}
        for act, nf in (('Walk', nW), ('Run', nR)):
            arm.animation_data.action = ACTIONS[act]; scene.frame_set(round(t * nf))
            pose[act] = {pb.name: (pb.rotation_quaternion.copy(), pb.location.copy()) for pb in PB}
        arm.animation_data.action = None
        for w in (0.25, 0.5, 0.75):
            for pb in PB:
                (qa, la), (qb, lb) = pose['Walk'][pb.name], pose['Run'][pb.name]
                pb.rotation_quaternion = qa.slerp(qb, w); pb.location = la.lerp(lb, w)
            keys['ArmsDown'].value = 1 - w; keys['ArmsDownRun'].value = w
            upd(); n += 1
            for k, (cnt, wv) in check_frame().items():
                if cnt:
                    c0, w0 = worst.get(k, (0, 0.0)); worst[k] = (c0 + cnt, max(w0, wv))
    clear(); set_arms_down('Rest')
    log('check Walk/Run blends, %d poses: %s' % (n, 'OK' if not worst else {k: (c, round(v * 1000, 1)) for k, (c, v) in worst.items()}))
    for k, v in WHERE.items():
        if v:
            log('      %s at rest positions %s' % (k, v))
    WHERE.clear()
    REPORT['checks']['Walk/Run blends'] = {k: dict(points=c, worst_mm=round(v * 1000, 1)) for k, (c, v) in worst.items()}


blend_check()
if os.environ.get('PLAYER_DEBUG_BLEND'):                 # (for looking at the checks' findings)
    bpy.ops.wm.save_as_mainfile(filepath=os.environ['PLAYER_DEBUG_BLEND'], copy=True)

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
        set_arms_down(name)
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
    set_arms_down('Rest')
    bpy.data.objects.remove(cam); bpy.data.objects.remove(sun)

# ------------------------------------------------------------------ a city person's two levels of detail
PALETTE_N = 8                        # palette slots: 0 shirt, 1 jeans, 2 shoes, the rest white
SLOT_COLOR = {0: (0.70, 0.70, 0.68), 1: (0.02, 0.04, 0.10), 2: (0.80, 0.80, 0.78)}
CLOTH_COLOR = {'Tee_Cotton': (0, (1, 1, 1)), 'Denim_Indigo': (1, (1, 1, 1)), 'Sneaker_Leather': (2, (1, 1, 1)),
               'Sock_Cotton': (7, (0.42, 0.42, 0.43)), 'Sole_Rubber': (7, (0.86, 0.85, 0.80)),
               'Insole': (7, (0.06, 0.06, 0.065)), 'Lace_Cotton': (7, (0.86, 0.86, 0.85)),
               'Eyelet_Metal': (7, (0.62, 0.62, 0.64)), 'Collar_Padding': (7, (0.55, 0.56, 0.58)),
               'HairTie': (7, (0.03, 0.03, 0.035))}
EYE_MATS = ('MBlab_human_eyes', 'MBLab_Iris_V4', 'MBlab_pupil')


def dup(o, name):
    o2 = o.copy(); o2.data = o.data.copy(); o2.name = name; o2.data.name = name
    scene.collection.objects.link(o2)
    return o2


def keep_faces(o, pred):
    """Delete o's faces for which pred(face) is false (and the vertices left alone)."""
    bm = bmesh.new(); bm.from_mesh(o.data)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if not pred(f)], context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bm.to_mesh(o.data); bm.free()


def mat_ids(o, names):
    return {i for i, m in enumerate(o.data.materials) if m and m.name.split('.')[0] in names}


def apply_modifiers(o, types):
    for m in [m for m in o.modifiers if m.type in types]:
        with bpy.context.temp_override(object=o, active_object=o):
            bpy.ops.object.modifier_apply(modifier=m.name)


def drop_modifiers(o, types):
    for m in [m for m in o.modifiers if m.type in types]:
        o.modifiers.remove(m)


def limit4(o):
    names = {g.index: g.name for g in o.vertex_groups}
    rm = defaultdict(list)
    for v in o.data.vertices:
        ws = sorted(((g.weight, g.group) for g in v.groups if names[g.group] in DEFORM and g.weight > 0), reverse=True)
        for w, gi in ws[4:]:
            rm[gi].append(v.index)
    for gi, idx in rm.items():
        o.vertex_groups[gi].remove(idx)


def tris_of(o, keep_mask=True):
    P, polys = evaluated(o, keep_mask=keep_mask)
    return sum(len(p) - 2 for p in polys)


def transfer_keys(src, dst):
    """Give dst (a lighter copy of src's surface) src's shape keys: each point
    moves as the nearest point of src's surface does."""
    S0 = np.array([v.co[:] for v in src.data.vertices])
    tris = []
    for p in src.data.polygons:
        vs = list(p.vertices)
        for a in range(1, len(vs) - 1):
            tris.append((vs[0], vs[a], vs[a + 1]))
    bvh = BVHTree.FromPolygons([Vector(p) for p in S0], tris)
    D0 = np.array([v.co[:] for v in dst.data.vertices])
    bary = []
    for co in D0:
        loc, nrm, ti, d = bvh.find_nearest(Vector(co))
        a, b, c = (Vector(S0[i]) for i in tris[ti])
        w = geometry.barycentric_transform(loc, a, b, c, Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
        bary.append((tris[ti], np.clip(np.array(w[:]), 0, 1) / max(sum(np.clip(np.array(w[:]), 0, 1)), 1e-9)))
    dst.shape_key_add(name='Basis', from_mix=False)
    for kb in src.data.shape_keys.key_blocks[1:]:
        K = np.array([v.co[:] for v in kb.data]) - S0
        nk = dst.shape_key_add(name=kb.name, from_mix=False); nk.value = 0.0
        for i, (t, w) in enumerate(bary):
            nk.data[i].co = Vector(D0[i] + w[0] * K[t[0]] + w[1] * K[t[1]] + w[2] * K[t[2]])


def components(o):
    """o's connected pieces: lists of face indices, in the order of their first vertex."""
    me = o.data
    parent = list(range(len(me.vertices)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for p in me.polygons:
        vs = list(p.vertices)
        for v in vs[1:]:
            ra, rb = find(vs[0]), find(v)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    comp = defaultdict(list)
    for p in me.polygons:
        comp[find(p.vertices[0])].append(p.index)
    return [comp[k] for k in sorted(comp)]


def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def image_sampler(img):
    w, h = img.size
    px = np.empty(w * h * 4, np.float32); img.pixels.foreach_get(px); px = px.reshape(h, w, 4)[:, :, :3]

    def f(uv):
        x = np.clip((uv[:, 0] % 1.0) * w - 0.5, 0, w - 1.001); y = np.clip((uv[:, 1] % 1.0) * h - 0.5, 0, h - 1.001)
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int); fx, fy = (x - x0)[:, None], (y - y0)[:, None]
        c = (px[y0, x0] * (1 - fx) * (1 - fy) + px[y0, x0 + 1] * fx * (1 - fy) +
             px[y0 + 1, x0] * (1 - fx) * fy + px[y0 + 1, x0 + 1] * fx * fy)
        return srgb_to_linear(c)
    return f


def paint(o, colors, slots):
    """Per-corner colours (n_loops x 3, linear) into the 'Col' attribute, and
    the palette slot of each corner's face into the only UV map."""
    me = o.data
    for a in [a for a in me.color_attributes if a.name != 'Col']:
        me.color_attributes.remove(a)
    ca = me.color_attributes.get('Col') or me.color_attributes.new('Col', 'BYTE_COLOR', 'CORNER')
    if ca.domain != 'CORNER' or ca.data_type != 'BYTE_COLOR':
        me.color_attributes.remove(ca); ca = me.color_attributes.new('Col', 'BYTE_COLOR', 'CORNER')
    rgba = np.ones((len(me.loops), 4)); rgba[:, :3] = colors
    ca.data.foreach_set('color', rgba.ravel())
    while len(me.uv_layers) > 1:
        me.uv_layers.remove(me.uv_layers[len(me.uv_layers) - 1])
    uvl = me.uv_layers[0] if me.uv_layers else me.uv_layers.new(name='UVMap')
    uvl.name = 'UVMap'
    uv = np.zeros((len(me.loops), 2)); uv[:, 0] = (np.asarray(slots) + 0.5) / PALETTE_N; uv[:, 1] = 0.5
    uvl.data.foreach_set('uv', uv.ravel())


def loop_faces(me):
    lf = np.empty(len(me.loops), int)
    for p in me.polygons:
        lf[p.loop_start:p.loop_start + p.loop_total] = p.index
    return lf


def paint_cloth(o):
    """A garment or a part of a shoe: its material's colour and palette slot per face."""
    me = o.data; lf = loop_faces(me)
    fm = np.array([p.material_index for p in me.polygons], int) if len(me.polygons) else np.zeros(0, int)
    col = np.ones((len(me.loops), 3)); slot = np.full(len(me.loops), PALETTE_N - 1)
    for i, m in enumerate(me.materials):
        key = m.name.split('.')[0] if m else None
        sel = fm[lf] == i
        if not sel.any():
            continue
        if key not in CLOTH_COLOR:
            raise RuntimeError('no game colour for %s on %s' % (key, o.name))
        s, c = CLOTH_COLOR[key]
        col[sel] = c; slot[sel] = s
    paint(o, col, slot)


def paint_eyes_nails(o):
    """The eyes (sclera from its texture, the iris its colour darkening to a
    ring at its edge, the pupil) and the nails (from their texture), as colours."""
    me = o.data; lf = loop_faces(me)
    fm = np.array([p.material_index for p in me.polygons], int)
    uv = np.empty(len(me.loops) * 2); me.uv_layers[0].data.foreach_get('uv', uv); uv = uv.reshape(-1, 2)
    col = np.ones((len(me.loops), 3))
    LV = np.array([me.vertices[l.vertex_index].co[:] for l in me.loops]) if len(me.loops) else np.zeros((0, 3))
    for i, m in enumerate(me.materials):
        key = m.name.split('.')[0] if m else None
        sel = fm[lf] == i
        if not sel.any():
            continue
        if key == 'MBlab_human_eyes' or key == 'MBLab_nails':
            img = next(n.image for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image)
            col[sel] = image_sampler(img)(uv[sel])
            if key == 'MBlab_human_eyes':
                # (the game has no shadow in the eye's socket: the white of a bare
                #  texture glares; a sclera in a face's shade is about this)
                col[sel] *= np.array([0.6, 0.58, 0.56])
        elif key in ('MBLab_Iris_V4', 'MBlab_pupil'):
            b = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
            c = np.array(b.inputs['Base Color'].default_value[:3])
            if key == 'MBlab_pupil':
                col[sel] = c
            else:
                pm = mat_ids(o, ('MBlab_pupil',))
                for side in (1, -1):
                    s2 = sel & (LV[:, 0] * side > 0)
                    if not s2.any():
                        continue
                    pv = [me.vertices[v].co[:] for p in me.polygons if p.material_index in pm for v in p.vertices
                          if me.vertices[v].co.x * side > 0]
                    cen = np.mean(pv, axis=0) if pv else LV[s2].mean(0)
                    r = np.linalg.norm(LV[s2] - cen, axis=1); r = r / max(r.max(), 1e-9)
                    col[s2] = c[None, :] * (1.0 - 0.45 * np.clip((r[:, None] - 0.65) / 0.35, 0, 1) ** 2)
        else:
            raise RuntimeError('eyes/nails: unexpected material %s' % key)
    paint(o, col, np.full(len(me.loops), PALETTE_N - 1))


def set_materials(o, mats, assign=None):
    """o's material slots become mats; assign(face) -> slot (default 0)."""
    old = [m.name.split('.')[0] if m else None for m in o.data.materials]
    fm = [old[p.material_index] if old else None for p in o.data.polygons]
    o.data.materials.clear()
    for m in mats:
        o.data.materials.append(m)
    for p, k in zip(o.data.polygons, fm):
        p.material_index = assign(k) if assign else 0


def join(target, others):
    others = [o for o in others if o is not None]
    if not others:
        return target
    with bpy.context.temp_override(active_object=target, object=target, selected_objects=[target] + others,
                                   selected_editable_objects=[target] + others):
        bpy.ops.object.join()
    return target


def game_materials():
    """Plain glTF materials (the game sets its own from these): skin, eyelashes, cloth (palette x colour), hair."""
    def principled(name):
        m = bpy.data.materials.new(name); m.use_nodes = True
        b = m.node_tree.nodes['Principled BSDF']
        return m, b
    skin_img = next(n.image for n in bpy.data.materials['MBLab_skin3'].node_tree.nodes if n.type == 'TEX_IMAGE' and n.image)
    lash_img = next(n.image for n in bpy.data.materials['MBlab_eyelash'].node_tree.nodes if n.type == 'TEX_IMAGE' and n.image)
    hair_m = HAIR.data.materials[0]
    hair_img = next(n.image for n in hair_m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image)
    skin_img.file_format = 'JPEG'; lash_img.file_format = 'PNG'; hair_img.file_format = 'PNG'
    M = {}
    m, b = principled('NPC_Skin'); t = m.node_tree.nodes.new('ShaderNodeTexImage'); t.image = skin_img
    m.node_tree.links.new(t.outputs['Color'], b.inputs['Base Color']); b.inputs['Roughness'].default_value = 0.55
    M['skin'] = m
    m, b = principled('NPC_Eyelash'); t = m.node_tree.nodes.new('ShaderNodeTexImage'); t.image = lash_img
    m.node_tree.links.new(t.outputs['Color'], b.inputs['Base Color']); m.node_tree.links.new(t.outputs['Alpha'], b.inputs['Alpha'])
    M['lash'] = m
    pal = bpy.data.images.new('NPC_Palette', PALETTE_N, 1, alpha=False)
    px = np.ones((1, PALETTE_N, 4))
    for s, c in SLOT_COLOR.items():
        px[0, s, :3] = c
    px[0, :, :3] = np.where(px[0, :, :3] <= 0.0031308, px[0, :, :3] * 12.92, 1.055 * px[0, :, :3] ** (1 / 2.4) - 0.055)
    pal.pixels.foreach_set(px.ravel().astype(np.float32)); pal.file_format = 'PNG'; pal.pack()
    m, b = principled('NPC_Cloth'); nt = m.node_tree
    t = nt.nodes.new('ShaderNodeTexImage'); t.image = pal; t.interpolation = 'Closest'
    ca = nt.nodes.new('ShaderNodeVertexColor'); ca.layer_name = 'Col'
    mul = nt.nodes.new('ShaderNodeMix'); mul.data_type = 'RGBA'; mul.blend_type = 'MULTIPLY'; mul.inputs['Factor'].default_value = 1.0
    nt.links.new(t.outputs['Color'], mul.inputs[6]); nt.links.new(ca.outputs['Color'], mul.inputs[7])
    nt.links.new(mul.outputs[2], b.inputs['Base Color']); b.inputs['Roughness'].default_value = 0.85
    m.use_backface_culling = False
    M['cloth'] = m
    m, b = principled('NPC_Hair'); nt = m.node_tree
    t = nt.nodes.new('ShaderNodeTexImage'); t.image = hair_img
    mul = nt.nodes.new('ShaderNodeMix'); mul.data_type = 'RGBA'; mul.blend_type = 'MULTIPLY'; mul.inputs['Factor'].default_value = 1.0
    nt.links.new(t.outputs['Color'], mul.inputs[6]); mul.inputs[7].default_value = tuple(hair_m['hair_color']) + (1.0,)
    nt.links.new(mul.outputs[2], b.inputs['Base Color']); nt.links.new(t.outputs['Alpha'], b.inputs['Alpha'])
    b.inputs['Roughness'].default_value = 0.5
    m.use_backface_culling = False
    M['hair'] = m
    M['hair_color'] = [float(x) for x in hair_m['hair_color']]
    return M


def hair_base_layer(o):
    """Keep only each hair part's first (innermost) layer, opaque: the shells'
    strands are lost at a distance, and the scalp under them is gone."""
    comps = components(o)
    tie = mat_ids(o, ('HairTie',))
    keep, prev = set(), None
    for c in comps:
        if o.data.polygons[c[0]].material_index in tie:
            keep.update(c); prev = None
            continue
        if prev is None or len(c) != prev:            # a new part starts (its layers have the same faces)
            keep.update(c)
        prev = len(c)
    keep_faces(o, lambda f: f.index in keep)
    ca = o.data.color_attributes['Col']
    rgba = np.empty(len(ca.data) * 4); ca.data.foreach_get('color', rgba); rgba = rgba.reshape(-1, 4)
    rgba[:, 3] = 1.0; ca.data.foreach_set('color', rgba.ravel())


def split_off(o, names, name):
    """A copy of o with only the faces of these materials; o keeps the rest."""
    ids = mat_ids(o, names)
    part = dup(o, name)
    keep_faces(part, lambda f: f.material_index in ids)
    keep_faces(o, lambda f: f.material_index not in ids)
    return part if len(part.data.polygons) else None


def lod1_check(parts):
    """The checks again on LOD1, with a tolerance of 1 cm, on frames of every animation."""
    global body, shirt, GARM, VIS, SKV, vis_faces, vis_idx, CO0, REST_PTS, TOL
    saved = (body, shirt, GARM, VIS, SKV, vis_faces, vis_idx, CO0, REST_PTS)
    body, shirt = parts['Body'], parts['TShirt']
    GARM = {n: parts[n] for n in GARM}
    nv = len(body.data.vertices)
    VIS = np.ones(nv, bool)
    sk = mat_ids(body, ('MBLab_skin3', 'MBlab_generic'))
    SKV = np.zeros(nv, bool)
    for p in body.data.polygons:
        if p.material_index in sk:
            SKV[list(p.vertices)] = True
    vis_faces = [list(p.vertices) for p in body.data.polygons if p.material_index in sk]
    vis_idx = np.where(SKV)[0]
    CO0 = np.array([v.co[:] for v in body.data.vertices])
    REST_PTS = np.vstack([CO0[vis_idx], np.array([CO0[f].mean(0) for f in vis_faces])])
    TOL = 0.01
    skin_check_setup()
    plan = [('Rest', [0])] + [(n, list(range(0, nf, max(1, nf // 6))) if loop else [0]) for n, fn, nf, loop in ANIMS]
    summary = {}
    for name, frames in plan:
        arm.animation_data.action = ACTIONS.get(name); set_arms_down(name)
        worst = {}
        for f in frames:
            scene.frame_set(f)
            for k, (cnt, w) in check_frame().items():
                if cnt:
                    c0, w0 = worst.get(k, (0, 0.0)); worst[k] = (c0 + cnt, max(w0, w))
        summary[name] = {k: dict(points=c, worst_mm=round(w * 1000, 1)) for k, (c, w) in worst.items()}
        log('LOD1 check %-5s %2d frames: %s' % (name, len(frames), 'OK' if not worst else
            {k: (c, round(w * 1000, 1)) for k, (c, w) in worst.items()}))
        for k, v in WHERE.items():
            if v:
                log('      %s at rest positions %s' % (k, v))
        WHERE.clear()
    arm.animation_data.action = None; scene.frame_set(0); set_arms_down('Rest')
    REPORT['checks_lod1 (1 cm)'] = summary
    TOL = 0.0
    (body, shirt, GARM, VIS, SKV, vis_faces, vis_idx, CO0, REST_PTS) = saved


def compact_glb(path):
    """Smaller vertex data, as core glTF allows: skin weights as normalized
    bytes, texture coordinates in [0, 1] as normalized shorts, colours as
    normalized bytes (positions and normals stay floats). Returns the
    accessors changed per attribute."""
    import struct
    b = open(path, 'rb').read()
    assert b[:4] == b'glTF'
    jl = struct.unpack('<I', b[12:16])[0]
    J = json.loads(b[20:20 + jl])
    o = 20 + jl
    bl = struct.unpack('<I', b[o:o + 4])[0]
    BIN = b[o + 8:o + 8 + bl]
    acc, bvs = J['accessors'], J['bufferViews']
    users = defaultdict(int)
    for a in acc:
        if 'bufferView' in a:
            users[a['bufferView']] += 1
    NC = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}
    new_data, done = {}, defaultdict(int)
    for m in J['meshes']:
        for prim in m['primitives']:
            for k, ai in prim['attributes'].items():
                kind = k.split('_')[0]
                if kind not in ('WEIGHTS', 'TEXCOORD', 'COLOR'):
                    continue
                a = acc[ai]; vi = a['bufferView']; bv = bvs[vi]
                if a['componentType'] != 5126 or users[vi] != 1 or 'byteStride' in bv or vi in new_data:
                    continue
                n = a['count'] * NC[a['type']]
                start = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
                X = np.frombuffer(BIN[start:start + 4 * n], '<f4').reshape(a['count'], NC[a['type']])
                if kind == 'WEIGHTS':
                    Q = np.rint(np.clip(X, 0, 1) * 255).astype(np.int32)
                    big = np.argmax(Q, axis=1)                                 # each vertex's weights sum to 255
                    Q[np.arange(len(Q)), big] += 255 - Q.sum(1)
                    data, ct = Q.astype(np.uint8).tobytes(), 5121
                elif kind == 'COLOR':
                    data, ct = np.rint(np.clip(X, 0, 1) * 255).astype(np.uint8).tobytes(), 5121
                else:
                    if X.min() < 0.0 or X.max() > 1.0:
                        continue                                                # (the hair's wrapped u)
                    data, ct = np.rint(X * 65535).astype(np.uint16).tobytes(), 5123
                new_data[vi] = data
                a['componentType'] = ct; a['normalized'] = True; a.pop('byteOffset', None)
                a.pop('min', None); a.pop('max', None)
                done[kind] += 1
    out = bytearray()
    for vi, bv in enumerate(bvs):
        data = new_data.get(vi)
        if data is None:
            st = bv.get('byteOffset', 0); data = BIN[st:st + bv['byteLength']]
        while len(out) % 4:
            out.append(0)
        bv['byteOffset'] = len(out); bv['byteLength'] = len(data)
        out += data
    while len(out) % 4:
        out.append(0)
    J['buffers'][0]['byteLength'] = len(out)
    js = json.dumps(J, separators=(',', ':')).encode()
    js += b' ' * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(out)
    with open(path, 'wb') as f:
        f.write(b'glTF' + struct.pack('<II', 2, total))
        f.write(struct.pack('<I', len(js)) + b'JSON' + js)
        f.write(struct.pack('<I', len(out)) + b'BIN\x00' + bytes(out))
    log('compacted vertex data:', dict(done), '%.2f -> %.2f MB' % (len(b) / 1e6, total / 1e6))
    return dict(done)


def export_npc():
    arm.animation_data.action = None; scene.frame_set(0); clear(); set_arms_down('Rest')
    for m, v in SAVED:
        m.show_viewport = v
    shirt0 = shirt
    # ---- LOD1: lighter copies of everything (made before the LOD0 parts are merged)
    L1 = {}
    b1 = dup(body, 'LOD1_Body')
    vgi = b1.vertex_groups['visible'].index
    keep_faces(b1, lambda f: all(any(g.group == vgi and g.weight > 0.5 for g in b1.data.vertices[v.index].groups)
                                 for v in f.verts))
    drop_modifiers(b1, {'MASK'})
    if 'custom_normal' in b1.data.attributes:
        b1.data.attributes.remove(b1.data.attributes['custom_normal'])
    lash = mat_ids(b1, ('MBlab_eyelash',))
    keep_faces(b1, lambda f: f.material_index not in lash)
    eyes1 = split_off(b1, EYE_MATS, 'LOD1_eyes')
    skin_ids = mat_ids(b1, ('MBLab_skin3', 'MBlab_generic', 'MBLab_nails'))
    # (the body from glTF is split along its UV seams: welded first, the decimation
    #  doesn't tear the face along them; the head is kept finer than the arms)
    bm = bmesh.new(); bm.from_mesh(b1.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.to_mesh(b1.data); bm.free()
    hg = b1.vertex_groups['head'].index
    hw = np.zeros(len(b1.data.vertices))
    for v in b1.data.vertices:
        for g in v.groups:
            if g.group == hg:
                hw[v.index] = g.weight
    decimate_now(b1, min(1.0, 2700.0 / max(1, sum(len(p.vertices) - 2 for p in b1.data.polygons if p.material_index in skin_ids))),
                 keep=0.004, protect=1.0 - 0.6 * hw)
    limit4(b1)
    L1['Body'] = b1
    for n, ratio in (('TShirt', 1500.0), ('Jeans', 1200.0), ('Socks_L', 0.3), ('Socks_R', 0.3)):
        o = bpy.data.objects[n]; c = dup(o, 'LOD1_' + n)
        if c.data.shape_keys:
            c.shape_key_clear()
        r = ratio / len(c.data.polygons) if ratio > 1 else ratio
        decimate_now(c, min(1.0, r), keep=0.004); limit4(c)
        L1[n] = c
    transfer_keys(shirt0, L1['TShirt'])
    for n, ratio in (('Shoe_', 0.25), ('ShoeCollar_', 0.3), ('Sole_', 0.3)):
        for s in 'LR':
            c = dup(bpy.data.objects[n + s], 'LOD1_' + n + s)
            apply_modifiers(c, {'DECIMATE'})
            decimate_now(c, ratio, keep=0.0); limit4(c)
            L1[n + s] = c
    h1 = dup(HAIR, 'LOD1_Hair'); hair_base_layer(h1)
    decimate_now(h1, 0.45, keep=0.003); limit4(h1)
    L1['Hair'] = h1
    lod1_check({'Body': b1, 'TShirt': L1['TShirt'], 'Jeans': L1['Jeans'], 'Socks_L': L1['Socks_L'], 'Socks_R': L1['Socks_R'],
                'Shoe_L': L1['Shoe_L'], 'Shoe_R': L1['Shoe_R'], 'Hair': h1})
    # ---- materials and colours
    M = game_materials()
    eyes0 = split_off(body, EYE_MATS + ('MBLab_nails',), 'LOD0_eyes')
    for o in (eyes0, eyes1):
        if o is not None:
            drop_modifiers(o, {'MASK'}); paint_eyes_nails(o)
    tie0 = split_off(HAIR, ('HairTie',), 'LOD0_tie'); tie1 = split_off(h1, ('HairTie',), 'LOD1_tie')
    cloth0 = [bpy.data.objects[n] for n in ('Jeans', 'Socks_L', 'Socks_R')] + \
             [o for o in bpy.data.objects if o.type == 'MESH' and o.name.startswith(('Shoe_', 'ShoeCollar_', 'Laces_', 'Sole_', 'Eyelets_'))]
    cloth1 = [L1[n] for n in ('Jeans', 'Socks_L', 'Socks_R')] + [L1[n + s] for n in ('Shoe_', 'ShoeCollar_', 'Sole_') for s in 'LR']
    for o in cloth0 + cloth1 + [shirt0, L1['TShirt']] + [t for t in (tie0, tie1) if t is not None]:
        apply_modifiers(o, {'DECIMATE'}); drop_modifiers(o, {'SOLIDIFY'})
        paint_cloth(o)
    for o in cloth0 + cloth1 + [shirt0, L1['TShirt'], eyes0, eyes1, tie0, tie1]:
        if o is not None:
            set_materials(o, [M['cloth']])
            for a in [a for a in o.data.attributes if not a.name.startswith('.') and a.name not in
                      ('position', 'Col', 'UVMap', 'material_index', 'sharp_face', 'sharp_edge', 'custom_normal')
                      and a.domain == 'POINT' and a.data_type != 'FLOAT_VECTOR' or a.name == 'rest']:
                o.data.attributes.remove(a)
    # ---- the LOD objects
    lod = {}
    lod['LOD0_Shirt'] = shirt0
    lod['LOD0_Cloth'] = join(bpy.data.objects['Jeans'], [o for o in cloth0 if o.name != 'Jeans'] + [eyes0, tie0])
    set_materials(body, [M['skin'], M['lash']], assign=lambda k: 1 if k == 'MBlab_eyelash' else 0)
    lod['LOD0_Body'] = body
    set_materials(HAIR, [M['hair']]); lod['LOD0_Hair'] = HAIR
    lod['LOD1_Cloth'] = join(L1['TShirt'], [o for o in cloth1] + [eyes1, tie1])
    set_materials(b1, [M['skin']]); lod['LOD1_Body'] = b1
    set_materials(h1, [M['hair']]); lod['LOD1_Hair'] = h1
    for name, o in lod.items():
        o.name = o.data.name = name
    # nothing else goes out
    keep = set(lod.values()) | {arm}
    for o in list(bpy.data.objects):
        if o not in keep:
            bpy.data.objects.remove(o)
    tri = {}
    for name, o in lod.items():
        tri[name] = tris_of(o)
    calls = {'LOD0': sum(len(o.data.materials) for n, o in lod.items() if n.startswith('LOD0')),
             'LOD1': sum(len(o.data.materials) for n, o in lod.items() if n.startswith('LOD1'))}
    REPORT['triangles'] = tri
    REPORT['triangles_total'] = {'LOD0': sum(v for k, v in tri.items() if k.startswith('LOD0')),
                                 'LOD1': sum(v for k, v in tri.items() if k.startswith('LOD1'))}
    REPORT['draw_calls'] = calls
    log('triangles', tri, REPORT['triangles_total'], 'draw calls', calls)
    for o in bpy.data.objects:
        o.select_set(True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=OUT, export_format='GLB', use_selection=True, export_apply=True,
                              export_skins=True, export_animations=True, export_animation_mode='ACTIONS',
                              export_force_sampling=True, export_frame_step=1, export_morph=True, export_morph_normal=False,
                              export_morph_animation=False, export_yup=True, export_image_format='JPEG', export_image_quality=86,
                              export_vertex_color='NAME', export_vertex_color_name='Col',
                              export_draco_mesh_compression_enable=False, export_extras=False)
    REPORT['bytes_blender'] = os.path.getsize(OUT)
    REPORT['compacted'] = compact_glb(OUT)
    REPORT['bytes'] = os.path.getsize(OUT)
    nid = os.path.splitext(os.path.basename(OUT))[0]
    REPORT['npc'] = dict(id=nid, sex='female' if nid.startswith('f_') else 'male',
                         height=round(BM['Z'][-1] - BM['Z'][0], 3), hip=round(BM['Z'][3] + arm.location.z, 3),
                         walk=REPORT['cycles']['Walk'], run=REPORT['cycles']['Run'],
                         hair=[round(x, 4) for x in M['hair_color']],
                         lie=REPORT['lie'])
    with open(os.path.splitext(OUT)[0] + '_report.json', 'w') as f:
        json.dump(REPORT, f, indent=1)
    log('exported', OUT, '%.2f MB' % (REPORT['bytes'] / 1e6))


# ------------------------------------------------------------------ export
if NPC:
    export_npc()
else:
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
                              export_force_sampling=True, export_frame_step=1, export_morph=True, export_morph_animation=False, export_yup=True,
                              export_image_format='JPEG', export_jpeg_quality=86, export_draco_mesh_compression_enable=False,
                              export_extras=False)
    REPORT['bytes'] = os.path.getsize(OUT)
    with open(os.path.splitext(OUT)[0] + '_report.json', 'w') as f:
        json.dump(REPORT, f, indent=1)
    log('exported', OUT, '%.1f MB' % (REPORT['bytes'] / 1e6))
