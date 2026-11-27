"""Hair for a dressed MB-Lab character, as meshes a game can draw.

    blender -b -P blender/make_hair.py -- <MB-Lab checkout> <id>_dressed.blend <out.blend> <style> <r,g,b>

MB-Lab's own hair is particle hair (render-only). Here hair is built as:
  * a cap: the scalp (MB-Lab's scalp region of the character's template,
    carried over by UV, so the hairline is anatomical), lifted off the skin by a
    thickness that depends on the direction around the head (sides, top, back,
    front) and tapers at the hairline,
  * shells: copies of the cap a little further out, drawn with a strand texture
    whose transparency grows outward (shell texturing): they give the hair
    volume, a soft outline and visible strands, at a few thousand triangles,
  * a style's own parts: a bob's hanging skirt (made like the clothes' hanging
    cloth: Hang, from dress_character.py), a ponytail (a tapering tube from a
    hair tie), a bun,
  * UVs from spherical coordinates around a pole (the crown, a part, the hair
    tie...), so the strands of the texture flow away from it,
  * procedural strand textures (straight, curly, short): grey, the colour is the
    material's; the vertex colour's alpha says which layer a point is in.
Everything is weighted to the head bone. The skin under the cap is hidden by the
body's mask (like the skin under the clothes).
"""
import bpy, bmesh, math, os, sys, json
import numpy as np
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

A = sys.argv[sys.argv.index('--') + 1:]
MBLAB, SRC, OUT, STYLE_NAME = A[0], os.path.abspath(A[1]), os.path.abspath(A[2]), A[3]
COLOR = tuple(float(x) for x in A[4].split(','))
REPORT = {'style': STYLE_NAME}


def log(*a):
    print('[hair]', *a, flush=True)


def ss(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


bpy.ops.wm.open_mainfile(filepath=SRC)
scene = bpy.context.scene
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
body = next(o for o in bpy.data.objects if o.type == 'MESH' and o.parent == arm
            and any(m and m.name == 'MBLab_skin3' for m in o.data.materials))
NID = body.name.split('.')[0]
me = body.data
CO = np.array([v.co[:] for v in me.vertices])
SKINM = {i for i, m in enumerate(me.materials) if m and m.name in ('MBLab_skin3', 'MBlab_generic')}
skin_faces = [tuple(p.vertices) for p in me.polygons if p.material_index in SKINM]
SKINV = np.zeros(len(CO), bool)
for f in skin_faces:
    SKINV[list(f)] = True
BODY_BVH = BVHTree.FromPolygons([Vector(c) for c in CO], skin_faces)
GI = {g.name: g.index for g in body.vertex_groups}
HEADW = np.zeros(len(CO))
for v in me.vertices:
    for g in v.groups:
        if g.group in (GI.get('head'), GI.get('neck')):
            HEADW[v.index] += g.weight
HEAD = arm.data.bones['head']
HEAD_Z = HEAD.head_local.z

# ---------------------------------------------------------------- the scalp (MB-Lab's, by UV)
tmpl = 'MBLab_human_female' if NID.startswith('f_') else 'MBLab_human_male'
_before = {k: set(getattr(bpy.data, k)) for k in ('materials', 'images', 'node_groups', 'textures')}
with bpy.data.libraries.load(os.path.join(MBLAB, 'data', 'humanoid_library.blend'), link=False) as (src, dst):
    dst.meshes = [tmpl]
tm = dst.meshes[0]
faces_json = os.path.join(MBLAB, 'data', 'Particle_Hair', NID + '_hair.json')
scalp_faces = json.load(open(faces_json))
tuv = tm.uv_layers[0].data
tris = []
for fi in scalp_faces:
    us = [tuv[l].uv[:] for l in tm.polygons[fi].loop_indices]
    for j in range(1, len(us) - 1):
        tris.append((us[0], us[j], us[j + 1]))
uvbvh = BVHTree.FromPolygons([Vector((u[0], u[1], 0)) for t in tris for u in t], [(3 * k, 3 * k + 1, 3 * k + 2) for k in range(len(tris))])
buv = me.uv_layers[0].data
SCALP = np.zeros(len(CO), bool)
for p in me.polygons:
    if p.material_index not in SKINM:
        continue
    for l in p.loop_indices:
        u = buv[l].uv
        if uvbvh.find_nearest(Vector((u[0], u[1], 0)), 1e-4)[0] is not None:
            SCALP[me.loops[l].vertex_index] = True
bpy.data.meshes.remove(tm)
for k, old in _before.items():                     # (and whatever came in with the template)
    coll = getattr(bpy.data, k)
    for x in [x for x in coll if x not in old]:
        coll.remove(x)
# (glTF split the skin along UV seams: the copies of a point agree)
key = {}
for i, c in enumerate(np.round(CO, 6)):
    key.setdefault(tuple(c), []).append(i)
for idx in key.values():
    if len(idx) > 1 and SCALP[idx].any():
        SCALP[idx] = True
log(NID, 'scalp', int(SCALP.sum()), 'skin points')

# the cranium as a sphere (least squares over the scalp)
S = CO[SCALP]
Am = np.c_[2 * S, np.ones(len(S))]; bm_ = (S ** 2).sum(1)
sol = np.linalg.lstsq(Am, bm_, rcond=None)[0]
C = sol[:3]; RAD = float(np.sqrt(sol[3] + C @ C))
log('cranium centre', np.round(C, 3), 'radius %.3f' % RAD)


def dirs(P):
    """Azimuth (deg, 0 = front, +90 = the character's left) and elevation (deg)
    of points as seen from the cranium's centre."""
    d = np.asarray(P, float) - C; n = np.linalg.norm(d, axis=1)
    return np.degrees(np.arctan2(d[:, 0], -d[:, 1])), np.degrees(np.arcsin(np.clip(d[:, 2] / n, -1, 1)))


def dir_of(az, el):
    a, e = math.radians(az), math.radians(el)
    return Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))


# ---------------------------------------------------------------- styles
# thickness (m) over the head: side (low), top, back; front: an extra lift at
# the front of the top (a quiff or fringe); taper: over how far from the
# hairline the hair reaches its thickness; pole: where the strands flow from;
# shells: layers over the cap, dh apart
STYLES = {
    'bob':       dict(tex='straight', side=0.009, top=0.013, back=0.012, front=0.003, taper=0.02,
                      pole=(0.0, 88.0), shells=3, dh=0.0022, skirt=True),
    'curls':     dict(tex='curly', side=0.030, top=0.040, back=0.032, front=0.0, taper=0.035,
                      pole=(0.0, 90.0), shells=4, dh=0.0035),
    'ponytail':  dict(tex='straight', side=0.0035, top=0.0045, back=0.004, front=0.0, taper=0.012,
                      pole=(180.0, 38.0), shells=2, dh=0.0014, tail=True),
    'bun':       dict(tex='straight', side=0.0035, top=0.0045, back=0.004, front=0.0, taper=0.012,
                      pole=(180.0, 8.0), shells=2, dh=0.0014, bun=True),
    'fade':      dict(tex='short', side=0.0015, top=0.0065, back=0.002, front=0.001, taper=0.018,
                      pole=(0.0, 90.0), shells=2, dh=0.0012, fade=True),
    'sidesweep': dict(tex='straight', side=0.0055, top=0.016, back=0.006, front=0.010, taper=0.022,
                      pole=(60.0, 62.0), shells=3, dh=0.002),
    'crew':      dict(tex='short', side=0.0045, top=0.011, back=0.005, front=0.004, taper=0.022,
                      pole=(0.0, 90.0), shells=3, dh=0.0016),
    'combed':    dict(tex='straight', side=0.006, top=0.010, back=0.007, front=0.002, taper=0.022,
                      pole=(0.0, 35.0), shells=3, dh=0.0018),
}
ST = STYLES[STYLE_NAME]


def thickness(P, dist_edge):
    az, el = dirs(P)
    top = ss(10.0, 55.0, el)
    back = ss(70.0, 150.0, np.abs(az)) * (1 - top)
    t = ST['side'] * (1 - top) + ST['top'] * top
    t = t * (1 - back) + ST['back'] * back
    t = t + ST['front'] * ss(70.0, 20.0, np.abs(az)) * ss(15.0, 45.0, el)
    return 0.0012 + (t - 0.0012) * ss(0.0, ST['taper'], dist_edge)


# ---------------------------------------------------------------- mesh helpers
def laplacian(bm):
    n = len(bm.verts)
    E = np.array([(e.verts[0].index, e.verts[1].index) for e in bm.edges])
    bnd = np.array([v.is_boundary for v in bm.verts])
    return E, bnd, n


def smooth(P, L, it, lam=0.5, fixed=None):
    E, bnd, n = L
    for _ in range(it):
        S = np.zeros_like(P); c = np.zeros(n)
        np.add.at(S, E[:, 0], P[E[:, 1]]); np.add.at(S, E[:, 1], P[E[:, 0]])
        np.add.at(c, E[:, 0], 1); np.add.at(c, E[:, 1], 1)
        d = S / np.maximum(c, 1)[:, None] - P
        if fixed is not None:
            d[fixed] = 0
        P = P + lam * d
    return P


def normals_of(bm, P):
    for v, p in zip(bm.verts, P):
        v.co = p
    bm.normal_update()
    return np.array([v.normal[:] for v in bm.verts])


def pushout(P, gap):
    """Each point at least gap (per point, or one for all) off the skin."""
    g = np.broadcast_to(np.asarray(gap, float), (len(P),))
    for i in range(len(P)):
        loc, nrm, idx, d = BODY_BVH.find_nearest(Vector(P[i]), 0.1)
        if loc is None:
            continue
        s = (Vector(P[i]) - loc).dot(nrm)
        if s < g[i]:
            P[i] = np.array(Vector(P[i]) + nrm * (g[i] - s))
    return P


def edge_dist(bm, P):
    bp = np.array([P[v.index] for v in bm.verts if v.is_boundary])
    kd = KDTree(len(bp))
    for i, p in enumerate(bp):
        kd.insert(p, i)
    kd.balance()
    return np.array([kd.find(p)[2] for p in P])


# ---------------------------------------------------------------- the cap
def make_cap():
    bm = bmesh.new()
    vmap = {}
    for f in skin_faces:
        if all(SCALP[v] for v in f):
            vs = []
            for v in f:
                if v not in vmap:
                    vmap[v] = bm.verts.new(CO[v])
                vs.append(vmap[v])
            try:
                bm.faces.new(vs)
            except ValueError:
                pass
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    # keep the largest piece (the scalp)
    seen, comps = set(), []
    for f in bm.faces:
        if f in seen:
            continue
        st, comp = [f], []; seen.add(f)
        while st:
            g = st.pop(); comp.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h); st.append(h)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    for c in comps[1:]:
        bmesh.ops.delete(bm, geom=c, context='FACES')
    if ST.get('subdiv'):                         # (only a thick style needs finer faces)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True, smooth=0.0)
    bm.verts.ensure_lookup_table(); bm.verts.index_update()
    P0 = np.array([v.co[:] for v in bm.verts])
    # (the subdivided points onto the skin)
    for i in range(len(P0)):
        loc = BODY_BVH.find_nearest(Vector(P0[i]), 0.05)[0]
        if loc is not None:
            P0[i] = np.array(loc)
    L = laplacian(bm)
    de = edge_dist(bm, P0)
    if ST.get('skirt'):                          # a bob: only the front hairline tapers, the hair goes on down
        az0, el0 = dirs(P0)
        front = np.array([v.is_boundary for v in bm.verts]) & (np.abs(az0) < 70)
        bp = P0[front]; kd = KDTree(len(bp))
        for i, p in enumerate(bp):
            kd.insert(p, i)
        kd.balance()
        de = np.array([kd.find(p)[2] for p in P0])
    # offset along the normals of a heavily smoothed copy (they never cross)
    N = normals_of(bm, smooth(P0.copy(), L, 30))
    t = thickness(P0, de)
    P = P0 + N * t[:, None]
    bnd = np.array([v.is_boundary for v in bm.verts])
    for _ in range(4):
        P = smooth(P, L, 6, fixed=bnd)
        P = pushout(P, np.maximum(t * 0.85, 0.0008))
    REPORT['cap_thickness_mm'] = [round(float(t.min() * 1000), 1), round(float(t.max() * 1000), 1)]
    return bm, P, N, de


def uv_from_pole(P, pole_az, pole_el, scale_u=4.0, scale_v=3.0):
    """Spherical coordinates around the cranium's centre, the pole where the
    strands start: u around the pole, v away from it."""
    p = np.array(dir_of(pole_az, pole_el))
    ref = np.array((0, 0, 1.0)) if abs(p[2]) < 0.9 else np.array((0, -1.0, 0))
    e1 = np.cross(ref, p); e1 /= np.linalg.norm(e1); e2 = np.cross(p, e1)
    d = P - C; d = d / np.linalg.norm(d, axis=1)[:, None]
    polar = np.arccos(np.clip(d @ p, -1, 1))
    az = np.arctan2(d @ e2, d @ e1)
    return az / (2 * np.pi) * scale_u, polar / np.pi * scale_v


# ---------------------------------------------------------------- textures
def strand_texture(kind, size=256, seed=7):
    """A tileable strand texture: RGB = brightness, A = the strand's "height"
    (0.56 between strands, up to 1 on a strand): a layer of vertex alpha a
    keeps the texels where A * a > 0.5."""
    rng = np.random.default_rng(seed)
    H = W = size
    y = np.arange(H)[:, None] / H; x = np.arange(W)[None, :]
    alpha = np.full((H, W), 0.56); bright = np.full((H, W), 0.42)
    n = {'straight': 420, 'curly': 300, 'short': 1400}[kind]
    for _ in range(n):
        x0 = rng.uniform(0, W); w = rng.uniform(0.7, 2.2); h = rng.uniform(0.62, 1.0); b = rng.uniform(0.55, 1.0)
        if kind == 'straight':
            amp, k = rng.uniform(0, 1.6), rng.integers(1, 3)
        elif kind == 'curly':
            amp, k = rng.uniform(2.5, 6.0), rng.integers(6, 11)
        else:
            amp, k = rng.uniform(0.5, 2.5), rng.integers(3, 7)
        ph = rng.uniform(0, 2 * np.pi)
        cx = x0 + amp * np.sin(2 * np.pi * k * y + ph)
        dx = np.abs(((x - cx) + W / 2) % W - W / 2)
        prof = np.clip(1 - dx / w, 0, 1)
        if kind != 'straight':                              # short or curly strands: broken into pieces
            seg = rng.uniform(0.04, 0.12) if kind == 'short' else rng.uniform(0.15, 0.45); off = rng.uniform(0, 1)
            prof = prof * ss(0.0, 0.08, ((y - off) % 1.0) * (((y - off) % 1.0) < seg) + 0.0)
        alpha = np.maximum(alpha, 0.56 + (h - 0.56) * prof)
        bright = np.maximum(bright, b * prof)
    rgb = np.clip(0.42 + 0.58 * bright, 0, 1)
    img = np.dstack([rgb, rgb, rgb, alpha])
    im = bpy.data.images.new('Hair_' + kind, W, H, alpha=True)
    im.pixels.foreach_set(img.astype(np.float32).ravel())
    im.pack()
    return im


def hair_material(img):
    m = bpy.data.materials.new('Hair')
    m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial'); b = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(b.outputs[0], out.inputs[0])
    t = nt.nodes.new('ShaderNodeTexImage'); t.image = img
    ca = nt.nodes.new('ShaderNodeVertexColor'); ca.layer_name = 'Col'
    mul = nt.nodes.new('ShaderNodeMix'); mul.data_type = 'RGBA'; mul.blend_type = 'MULTIPLY'
    mul.inputs['Factor'].default_value = 1.0
    nt.links.new(t.outputs['Color'], mul.inputs[6])
    mul.inputs[7].default_value = COLOR + (1.0,)
    nt.links.new(mul.outputs[2], b.inputs['Base Color'])
    am = nt.nodes.new('ShaderNodeMath'); am.operation = 'MULTIPLY'
    nt.links.new(t.outputs['Alpha'], am.inputs[0]); nt.links.new(ca.outputs['Alpha'], am.inputs[1])
    cut = nt.nodes.new('ShaderNodeMath'); cut.operation = 'GREATER_THAN'; cut.inputs[1].default_value = 0.5
    nt.links.new(am.outputs[0], cut.inputs[0]); nt.links.new(cut.outputs[0], b.inputs['Alpha'])
    b.inputs['Roughness'].default_value = 0.5
    b.inputs['Specular IOR Level'].default_value = 0.35
    b.inputs['Coat Weight'].default_value = 0.0
    m['hair_color'] = list(COLOR)
    return m


# ---------------------------------------------------------------- the parts of a style
def skirt(cap_bm, cap_base, cap_outer):
    """A bob: hair hanging straight down to the jaw, round the sides and back
    (open for the face). It starts hidden under the cap's opaque base layer
    and comes out from under the cap's edge, hanging from the head's widest
    and clear of the head, ears and neck by 4 mm; the ends turn in a little."""
    faces = [tuple(v.index for v in f.verts) for f in cap_bm.faces]
    cap_bvh = BVHTree.FromPolygons([Vector(p) for p in cap_base], faces)
    hz = np.array([CO[i] for i in np.where(SKINV & (HEADW > 0.3))[0]])
    top, bot = C[2] + 0.03, HEAD_Z - 0.028
    rows = np.linspace(top, bot, 15)
    azs = np.radians(np.linspace(78.0, 360.0 - 78.0, 44))      # from the left cheek round the back to the right
    def polar(pts):
        rel = pts - C
        return np.arctan2(rel[:, 0], -rel[:, 1]) % (2 * np.pi), np.hypot(rel[:, 0], rel[:, 1])
    az_s, r_s = polar(hz); az_c, r_c = polar(cap_outer)
    Rskin = np.zeros((len(rows), len(azs))); Rbase = np.zeros_like(Rskin); Rabove = np.zeros_like(Rskin)
    for j, a in enumerate(azs):
        ds = np.abs(((az_s - a) + np.pi) % (2 * np.pi) - np.pi) < np.radians(7)
        dc = np.abs(((az_c - a) + np.pi) % (2 * np.pi) - np.pi) < np.radians(7)
        for i, z in enumerate(rows):
            m = ds & (hz[:, 2] >= z - 0.003)
            Rskin[i, j] = r_s[m].max() if m.any() else 0.0
            m = dc & (cap_outer[:, 2] >= z - 0.003)
            Rabove[i, j] = r_c[m].max() if m.any() else 0.0              # the cap's outer layer above
            o = Vector((C[0], C[1], z)); d = Vector((math.sin(a), -math.cos(a), 0.0))
            h = cap_bvh.ray_cast(o, d, 0.3)
            Rbase[i, j] = (h[0] - o).length if h[0] is not None else 0.0
    Rskin = np.maximum.accumulate(Rskin, axis=0)
    Rabove = np.maximum.accumulate(Rabove, axis=0)
    need = Rskin + 0.004                                       # clear of the head, ears and neck
    under = Rbase > 0                                          # the cap is right here: stay under it
    R = np.where(under, Rbase - 0.0012, np.maximum(need, Rabove - 0.0012))
    for _ in range(10):
        S_ = (np.roll(R, 1, 1) + 2 * R + np.roll(R, -1, 1)) / 4
        S_[1:-1] = (S_[:-2] + 2 * S_[1:-1] + S_[2:]) / 4
        R = np.where(under, np.minimum(S_, Rbase - 0.0012), S_)
        R = np.maximum(R, need)
    R[-1] -= 0.003; R[-2] -= 0.0015                            # the ends turn in
    R = np.maximum(R, Rskin + 0.0015)
    bm = bmesh.new(); grid = []
    for i, z in enumerate(rows):
        grid.append([bm.verts.new((C[0] + R[i, j] * math.sin(a), C[1] - R[i, j] * math.cos(a), z)) for j, a in enumerate(azs)])
    for i in range(len(rows) - 1):
        for j in range(len(azs) - 1):
            bm.faces.new((grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.verts.ensure_lookup_table(); bm.verts.index_update()
    P = np.array([v.co[:] for v in bm.verts])
    nr, na = len(rows), len(azs)
    U = np.array([[j / (na - 1) * 5.0 for j in range(na)] for i in range(nr)]).ravel()
    V = np.array([[(top - z) / 0.10 for j in range(na)] for z in rows]).ravel()
    # strand ends: the last rows and the front edges thin out
    fade = np.array([[1.0 - 0.30 * max(ss(0.6, 1.0, i / (nr - 1)), ss(2.5, 0.0, min(j, na - 1 - j)))
                      for j in range(na)] for i in range(nr)]).ravel()
    hidden = int(under.sum())
    REPORT['bob'] = dict(rows=nr, columns=na, bottom_z=round(float(bot), 3), hidden_under_cap=hidden)
    log('bob: %d x %d, %d points start hidden under the cap' % (nr, na, hidden))
    return bm, P, U, V, fade


def tail():
    """A ponytail from a hair tie at the back of the head."""
    tie_dir = dir_of(180.0, 30.0)
    loc, nrm, idx, d = BODY_BVH.find_nearest(Vector(C) + tie_dir * RAD, 0.1)
    T = np.array(loc) + np.array(nrm) * (ST['top'] + 0.004)
    out = np.array(tie_dir); out[2] = 0; out /= np.linalg.norm(out)
    Lg, n = 0.21, 18
    pts = []
    for k in range(n + 1):
        s = Lg * k / n
        back = 0.045 * (1 - math.exp(-s / 0.035))
        pts.append(T + out * back + np.array((0, 0, -1.0)) * (s - 0.03 * (1 - math.exp(-s / 0.03))))
    pts = np.array(pts)
    rad = np.array([0.012 + 0.006 * math.sin(math.pi * min(1.0, k / n * 1.3)) * (1 - k / n) + 0.004 * (1 - k / n) for k in range(n + 1)])
    rad[-1] = 0.002
    # keep the tail off the head, neck and back (with its radius + 8 mm)
    for _ in range(4):
        for k in range(1, n + 1):
            loc, nrm, idx, d = BODY_BVH.find_nearest(Vector(pts[k]), 0.3)
            s = (Vector(pts[k]) - loc).dot(nrm)
            need = rad[k] + 0.008
            if s < need:
                pts[k] = pts[k] + np.array(nrm) * (need - s)
        for k in range(2, n):
            pts[k] = 0.5 * pts[k] + 0.25 * (pts[k - 1] + pts[k + 1])
    bm = bmesh.new(); rings = []; nseg = 10
    U, V = [], []
    for k in range(n + 1):
        t_ = pts[min(k + 1, n)] - pts[max(k - 1, 0)]; t_ /= np.linalg.norm(t_)
        x_ = np.cross(t_, np.array((1.0, 0, 0))); x_ /= np.linalg.norm(x_); y_ = np.cross(t_, x_)
        ring = []
        for j in range(nseg + 1):
            a = 2 * math.pi * j / nseg
            ring.append(bm.verts.new(pts[k] + rad[k] * (math.cos(a) * x_ + math.sin(a) * y_)))
            U.append(j / nseg * 2.0); V.append(k / n * 2.5)
        rings.append(ring)
    for k in range(n):
        for j in range(nseg):
            bm.faces.new((rings[k][j], rings[k][j + 1], rings[k + 1][j + 1], rings[k + 1][j]))
    bm.verts.ensure_lookup_table(); bm.verts.index_update()
    P = np.array([v.co[:] for v in bm.verts])
    fade = np.array([1.0 - 0.3 * ss(0.75, 1.0, k / n) for k in range(n + 1) for j in range(nseg + 1)])
    # the hair tie
    tb = bmesh.new()
    t0 = pts[1] - pts[0]; t0 /= np.linalg.norm(t0)
    mtx = Matrix.Translation(Vector(pts[0] + t0 * 0.004)) @ Vector(t0).to_track_quat('Z', 'Y').to_matrix().to_4x4()
    bmesh.ops.create_cone(tb, cap_ends=True, segments=12, radius1=rad[0] + 0.0025, radius2=rad[0] + 0.0025, depth=0.008, matrix=mtx)
    REPORT['ponytail'] = dict(length_m=Lg, tip_z=round(float(pts[-1][2]), 3))
    return bm, P, np.array(U), np.array(V), fade, tb


def bun():
    d = dir_of(180.0, 6.0)
    loc, nrm, idx, dd = BODY_BVH.find_nearest(Vector(C) + d * RAD, 0.1)
    r = 0.040
    B = np.array(loc) + np.array(nrm) * (r * 0.72 + ST['back'])
    bm = bmesh.new()
    nrm_ = np.array(nrm)
    mtx = Matrix.Translation(Vector(B)) @ Vector(nrm_).to_track_quat('Z', 'Y').to_matrix().to_4x4() @ Matrix.Diagonal((1.0, 0.92, 0.78, 1.0))
    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12, radius=r, matrix=mtx)
    bm.verts.ensure_lookup_table(); bm.verts.index_update()
    P = np.array([v.co[:] for v in bm.verts])
    P = pushout(P, 0.004)
    rel = P - B
    ax = nrm_ / np.linalg.norm(nrm_)
    e1 = np.cross(ax, np.array((0, 0, 1.0))); e1 /= np.linalg.norm(e1); e2 = np.cross(ax, e1)
    U = (np.arctan2(rel @ e2, rel @ e1) / (2 * np.pi)) * 3.0          # strands wound round the bun
    V = (rel @ ax) / r * 0.6
    tb = bmesh.new()
    m2 = Matrix.Translation(Vector(B - ax * r * 0.62)) @ Vector(ax).to_track_quat('Z', 'Y').to_matrix().to_4x4()
    bmesh.ops.create_cone(tb, cap_ends=False, segments=16, radius1=r * 0.55, radius2=r * 0.55, depth=0.007, matrix=m2)
    REPORT['bun'] = dict(radius_m=r)
    return bm, P, U, V, np.ones(len(P)), tb


# ---------------------------------------------------------------- build
img = strand_texture(ST['tex'])
mat = hair_material(img)
parts = []                    # (bmesh, P, U, V, alpha per point, shells, their spacing, u period or None)

cap_bm, CAP, CAPN, DE = make_cap()
bob_fade = None
u, v = uv_from_pole(CAP, *ST['pole'])
alpha0 = 0.80 + 0.20 * ss(0.0, 0.02, DE)                  # a soft hairline: the strands thin out toward the edge
if NID.startswith('m_'):                                 # (a man's nape is cut short: it fades too)
    az_, el_ = dirs(CAP)
    alpha0 = np.minimum(alpha0, 0.84 + 0.16 * np.maximum(ss(-30.0, 0.0, el_), ss(150.0, 110.0, np.abs(az_))))
if ST.get('fade'):                                       # a fade: the sides go down to skin
    az_, el_ = dirs(CAP)
    alpha0 = np.minimum(alpha0, 0.72 + 0.28 * ss(-25.0, 15.0, el_))
if ST.get('skirt'):                                     # a bob's cap thins out at its lower edge onto the hanging hair
    alpha0 = np.minimum(alpha0, 0.80 + 0.20 * ss(0.0, 0.02, edge_dist(cap_bm, CAP)))
parts.append((cap_bm, CAP, u, v, alpha0, ST['shells'], ST['dh'], 4.0))
if ST.get('skirt'):
    sb, P, U, V, fade = skirt(cap_bm, CAP, CAP + normals_of(cap_bm, CAP) * ST['dh'] * ST['shells'])
    parts.append((sb, P, U, V, fade, 2, 0.0015, None))
extras = []
if ST.get('tail'):
    tb_, P, U, V, fade, tie = tail()
    parts.append((tb_, P, U, V, fade, 1, 0.002, None)); extras.append(tie)
if ST.get('bun'):
    bb, P, U, V, fade, tie = bun()
    parts.append((bb, P, U, V, fade, 1, 0.002, 3.0)); extras.append(tie)

# one mesh: every part, each with its shells (a layer's vertex alpha thins its strands)
hm = bpy.data.meshes.new('Hair'); out = bmesh.new()
uvl = out.loops.layers.uv.new('UVMap'); col = out.loops.layers.color.new('Col')
ntri = 0
for bm, P, U, V, al, nsh, dh, period in parts:
    Nn = normals_of(bm, P)
    for layer in range(nsh + 1):
        off = P + Nn * (dh * layer)
        a_layer = al * (1.0 - 0.12 * layer) if layer else al
        vm = [out.verts.new(p) for p in off]
        for f in bm.faces:
            idx = [v.index for v in f.verts]
            nf = out.faces.new([vm[i] for i in idx])
            us = [U[i] for i in idx]
            if period and max(us) - min(us) > period / 2:                 # a face across the u seam
                us = [x + period if x < max(us) - period / 2 else x for x in us]
            for lp, i, uu in zip(nf.loops, idx, us):
                lp[uvl].uv = (uu, V[i]); lp[col] = (1.0, 1.0, 1.0, float(np.clip(a_layer[i], 0, 1)))
            ntri += len(idx) - 2
for tie in extras:
    vm = [out.verts.new(v.co) for v in tie.verts]
    for f in tie.faces:
        nf = out.faces.new([vm[v.index] for v in f.verts]); nf.material_index = 1
        for lp in nf.loops:
            lp[uvl].uv = (0.0, 0.0); lp[col] = (0.08, 0.08, 0.09, 1.0)
        ntri += len(f.verts) - 2
out.to_mesh(hm); out.free()
hair = bpy.data.objects.new('Hair', hm); scene.collection.objects.link(hair)
hm.materials.append(mat)
tie_m = bpy.data.materials.new('HairTie'); tie_m.use_nodes = True
tie_m.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.03, 0.03, 0.035, 1)
tie_m.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value = 0.6
hm.materials.append(tie_m)
for p in hm.polygons:
    p.use_smooth = True
log('hair mesh', len(hm.vertices), 'verts,', ntri, 'triangles')
REPORT['triangles'] = ntri
# rig: the head
hair.parent = arm
vg = hair.vertex_groups.new(name='head'); vg.add(list(range(len(hm.vertices))), 1.0, 'REPLACE')
md = hair.modifiers.new('Armature', 'ARMATURE'); md.object = arm
# (in the skeleton's frame, like the body and the clothes: the rig is lifted onto the soles)
hair.matrix_parent_inverse = Matrix.Identity(4)

# the mask: the scalp under the cap (all but a strip at the hairline)
vis = body.vertex_groups.get('visible')
sc = np.where(SCALP)[0]
# the scalp's edge: scalp points next to a non-scalp skin point
edge = set()
for f in skin_faces:
    s_ = [SCALP[v] for v in f]
    if any(s_) and not all(s_):
        edge.update(v for v in f if SCALP[v])
ek = KDTree(len(edge))
for k, i in enumerate(sorted(edge)):
    ek.insert(CO[i], k)
ek.balance()
hide = [int(i) for i in sc if ek.find(CO[i])[2] > 0.006]
if vis is not None:
    vis.remove(hide)
REPORT['scalp_hidden'] = len(hide)
log('mask: %d scalp points hidden under the hair' % len(hide))
bpy.ops.file.pack_all()
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=OUT, compress=True)
with open(os.path.splitext(OUT)[0] + '_report.json', 'w') as f:
    json.dump(REPORT, f, indent=1)
log('saved', OUT, REPORT)
