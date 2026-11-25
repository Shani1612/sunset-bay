"""Dress an MB-Lab character in a T-shirt, jeans, socks and sneakers.

The clothes are built from the character's own body, so they fit its exact
measurements, and they are rigged to its skeleton:

  1. every garment starts as a copy of the skin it covers, cut along smooth
     curves (neckline, sleeve ends, hems, waist),
  2. it is moved off the body (looser where cloth hangs: below the chest, below
     the knee, toward the sleeve ends), smoothed, and pushed out again until it
     keeps a minimum gap from the body and from the clothes under it,
  3. it keeps the skin's bone weights, so it moves with the skeleton,
  4. the body faces hidden under the clothes are masked, so no skin can ever
     poke through them, in any pose.

Then it checks for penetrations in the rest pose and in a walking pose, and
writes a .blend and a .glb.

Run with Blender 4.2+ (or the bpy module):
    blender -b -P blender/dress_character.py -- characters/Human.glb characters
"""
import bpy, bmesh, math, os, sys, json
from collections import defaultdict
import numpy as np
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
SRC = os.path.abspath(ARGS[0] if ARGS else 'characters/Human.glb')
OUT = os.path.abspath(ARGS[1] if len(ARGS) > 1 else 'characters')
os.makedirs(OUT, exist_ok=True)
REPORT = {}


def log(*a):
    print('[dress]', *a, flush=True)


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- load + repair
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=SRC)
for o in list(bpy.data.objects):
    if o.name.startswith('Icosphere'):          # the glTF importer's bone-display shape
        bpy.data.objects.remove(o)
body = bpy.data.objects['m_ca01']
arm = bpy.data.objects['m_ca01_skeleton']
scene = bpy.context.scene
me = body.data

# the glTF import left the skin untextured (its albedo was wired to the tongue
# only) and the cornea opaque: restore both
alb = bpy.data.images.get('human_male_albedo')
for m in me.materials:
    nt = m.node_tree
    b = nt.nodes.get('Principled BSDF')
    if b is None:
        continue
    if m.name == 'MBLab_skin3' and alb is not None:
        t = nt.nodes.new('ShaderNodeTexImage'); t.image = alb
        nt.links.new(t.outputs['Color'], b.inputs['Base Color'])
        b.inputs['Roughness'].default_value = 0.5
        b.inputs['Subsurface Weight'].default_value = 0.15
        b.inputs['Subsurface Radius'].default_value = (1.0, 0.35, 0.2)
    if m.name == 'MBLab_skin3':
        b.inputs['Metallic'].default_value = 0.0              # exported as 1.0: metal-looking skin
        b.inputs['Roughness'].default_value = 0.48
    if m.name == 'MBlab_generic':
        b.inputs['Metallic'].default_value = 0.0; b.inputs['Roughness'].default_value = 0.9
        b.inputs['Base Color'].default_value = (0.75, 0.75, 0.74, 1)
    if m.name == 'MBlab_cornea':                              # a clear, glossy film over the eye
        b.inputs['Metallic'].default_value = 0.0; b.inputs['Transmission Weight'].default_value = 0.0
        b.inputs['IOR'].default_value = 1.376; b.inputs['Alpha'].default_value = 0.12
        b.inputs['Roughness'].default_value = 0.02; b.inputs['Specular IOR Level'].default_value = 1.0
        b.inputs['Base Color'].default_value = (1, 1, 1, 1)
    if m.name == 'MBLab_Iris_V4':
        b.inputs['Base Color'].default_value = (0.16, 0.085, 0.035, 1); b.inputs['Roughness'].default_value = 0.3
    if m.name == 'MBLab_nails':                               # exported dark red: a natural nail
        b.inputs['Base Color'].default_value = (0.62, 0.43, 0.40, 1); b.inputs['Roughness'].default_value = 0.35
    if m.name == 'MBlab_pupil':
        b.inputs['Metallic'].default_value = 0.0; b.inputs['Roughness'].default_value = 0.3
        b.inputs['Base Color'].default_value = (0.004, 0.004, 0.004, 1)

# the body surface: the skin plus the briefs patch over the pelvis
SKINS = {i for i, m in enumerate(me.materials) if m.name in ('MBLab_skin3', 'MBlab_generic')}
NV = len(me.vertices)
CO = np.empty(NV * 3); me.vertices.foreach_get('co', CO); CO = CO.reshape(-1, 3)
GN = [g.name for g in body.vertex_groups]
GI = {n: i for i, n in enumerate(GN)}
W = np.zeros((NV, len(GN)), np.float32)
for v in me.vertices:
    for g in v.groups:
        W[v.index, g.group] = g.weight
skin_polys = [p for p in me.polygons if p.material_index in SKINS]
SKINV = np.zeros(NV, bool)
for p in skin_polys:
    SKINV[list(p.vertices)] = True


def groups_like(*keys, side=None):
    out = [i for n, i in GI.items() if any(k in n for k in keys)]
    if side:
        out = [i for i in out if GN[i].endswith('_' + side)]
    return out


G_ARM = groups_like('upperarm', 'lowerarm', 'hand', 'thumb', 'index', 'middle', 'ring', 'pinky')
G_LOWARM = groups_like('lowerarm', 'hand', 'thumb', 'index', 'middle', 'ring', 'pinky')
G_LEG = {s: groups_like('thigh', 'calf', 'foot', 'toes', side=s) for s in 'LR'}
G_HEAD = groups_like('head', 'neck')
ARMW = W[:, G_ARM].sum(1)
LOWARMW = W[:, G_LOWARM].sum(1)
LEGW = {s: W[:, G_LEG[s]].sum(1) for s in 'LR'}
HEADW = W[:, G_HEAD].sum(1)
SIDE = {'L': 1.0, 'R': -1.0}

BONE = {b.name: (Vector(b.head_local), Vector(b.tail_local)) for b in arm.data.bones}
BODY_BVH = BVHTree.FromPolygons([Vector(c) for c in CO], [tuple(p.vertices) for p in skin_polys])
log('body', NV, 'verts,', len(skin_polys), 'skin faces,', len(GN), 'groups')

# dense samples of the skin surface (for the "hanging cloth" envelopes)
_s, _a, _l = [], [], {'L': [], 'R': []}
for p in skin_polys:
    vs = list(p.vertices); pts = CO[vs]
    c = pts.mean(0)
    for k in range(len(vs)):
        a, b = pts[k], pts[(k + 1) % len(vs)]
        for t in (0.25, 0.5, 0.75):
            _s.append(c * (1 - t) + a * t); _s.append(c * (1 - t) + (a + b) / 2 * t)
    _s.append(c)
    n = 6 * len(vs) + 1
    _a += [ARMW[vs].mean()] * n
    for s in 'LR':
        _l[s] += [LEGW[s][vs].mean()] * n
SAMP = np.array(_s); SAMP_ARM = np.array(_a); SAMP_LEG = {s: np.array(_l[s]) for s in 'LR'}
log('surface samples', len(SAMP))


# ---------------------------------------------------------------- mesh helpers
def new_garment(name, keep):
    """A bmesh with the skin faces whose vertices all satisfy keep[i]."""
    m2 = me.copy(); m2.name = name
    bm = bmesh.new(); bm.from_mesh(m2)
    bm.verts.ensure_lookup_table()
    dele = [f for f in bm.faces if f.material_index not in SKINS or not all(keep[v.index] for v in f.verts)]
    bmesh.ops.delete(bm, geom=dele, context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    # glTF splits vertices along UV seams: weld them so the cloth is one piece
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    return m2, bm


_SPLIT_CHECKED = []


def levelset_cut(bm, fn):
    """Cut the mesh along the zero set of fn(co) and delete the positive side."""
    val = {v: fn(v.co) for v in bm.verts}
    zero = set()
    for v, x in val.items():
        if abs(x) < 2e-5:
            val[v] = 0.0; zero.add(v)
    for e in [e for e in bm.edges if val[e.verts[0]] * val[e.verts[1]] < 0]:
        a, b = e.verts
        if val[a] * val[b] >= 0:
            continue                       # an end was already snapped onto the cut
        t = val[a] / (val[a] - val[b])
        pa, pb = a.co.copy(), b.co.copy()
        if t < 0.12 or t > 0.88:           # snap the near end onto the cut: no slivers
            v = a if t < 0.12 else b
            v.co = pa.lerp(pb, t); val[v] = 0.0; zero.add(v)
            continue
        ne, nv = bmesh.utils.edge_split(e, a, t)
        if not _SPLIT_CHECKED:
            _SPLIT_CHECKED.append((nv.co - pa.lerp(pb, t)).length)
            log('edge_split check (0 = fac measured from the given vert):', round(_SPLIT_CHECKED[0], 6))
        nv.co = pa.lerp(pb, t)
        val[nv] = 0.0; zero.add(nv)
    pairs = []
    for f in bm.faces:
        vs = list(f.verts); n = len(vs)
        zi = [i for i, v in enumerate(vs) if v in zero]
        if len(zi) < 2:
            continue
        for k in range(len(zi)):
            i, j = zi[k], zi[(k + 1) % len(zi)]
            arc = [vs[(i + m) % n] for m in range(1, (j - i) % n or n)]
            other = (i - j) % n
            if arc and all(val[v] > 0 for v in arc) and other not in (0, 1) and (j - i) % n not in (0, 1):
                pairs.append((vs[i], vs[j]))
    for a, b in pairs:
        try:
            bmesh.ops.connect_verts(bm, verts=[a, b])
        except Exception:
            pass
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if any(val.get(v, 0.0) > 0 for v in f.verts)], context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bmesh.ops.dissolve_degenerate(bm, dist=2e-4, edges=bm.edges[:])


def keep_largest(bm):
    seen = set(); comps = []
    for f in bm.faces:
        if f in seen:
            continue
        stack = [f]; comp = []; seen.add(f)
        while stack:
            g = stack.pop(); comp.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h); stack.append(h)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    for c in comps[1:]:
        bmesh.ops.delete(bm, geom=c, context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    return len(comps)


def subdivide(bm, cuts=1):
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True, smooth=0.0)


def arrays(bm):
    bm.verts.index_update(); bm.verts.ensure_lookup_table(); bm.normal_update()
    P = np.array([v.co[:] for v in bm.verts])
    N = np.array([v.normal[:] for v in bm.verts])
    return P, N


def smooth_normals(bm, P, iters=25):
    """Normals of a heavily smoothed copy of the cloth: in a crease they do not
    point at each other, so offsetting along them cannot fold the cloth."""
    L = laplacian(bm); Q = P.copy()
    for _ in range(iters):
        Q = Q + 0.5 * lap(Q, L)
    write_back(bm, Q)
    N = np.array([v.normal[:] for v in bm.verts])
    write_back(bm, P)
    return N


def write_back(bm, P):
    for v, p in zip(bm.verts, P):
        v.co = p
    bm.normal_update()


def laplacian(bm):
    n = len(bm.verts)
    bnd = np.array([v.is_boundary for v in bm.verts])
    E = np.array([(e.verts[0].index, e.verts[1].index) for e in bm.edges])
    eb = np.array([e.is_boundary for e in bm.edges])
    a, b = E[:, 0], E[:, 1]
    k1 = (~bnd[a]) | eb; k2 = (~bnd[b]) | eb
    I = np.concatenate([a[k1], b[k2]]); J = np.concatenate([b[k1], a[k2]])
    deg = np.bincount(I, minlength=n).astype(float)
    return I, J, deg


def lap(P, L):
    I, J, deg = L
    S = np.zeros_like(P); np.add.at(S, I, P[J])
    out = S / np.maximum(deg, 1)[:, None] - P
    out[deg == 0] = 0
    return out


def taubin(P, L, it, lam=0.5, mu=-0.53, wmask=None):
    for _ in range(it):
        d = lap(P, L); P = P + lam * (d if wmask is None else d * wmask[:, None])
        d = lap(P, L); P = P + mu * (d if wmask is None else d * wmask[:, None])
    return P


def pushout(P, layers):
    """Move every point out along the nearest surface normal until it is at
    least `gap` outside each (bvh, gap[, open]) layer; gap may be per point.
    For an open surface (another garment) a point whose nearest spot is the
    surface's edge (it lies beyond the edge, not over the surface) is left alone."""
    for lay in layers:
        bvh, gap = lay[0], lay[1]; open_ = len(lay) > 2 and lay[2]
        g = np.broadcast_to(np.asarray(gap, float), (len(P),))
        for i in range(len(P)):
            co = Vector(P[i])
            loc, nrm, idx, d = bvh.find_nearest(co, 0.25)
            if loc is None:
                continue
            s = (co - loc).dot(nrm)
            if open_ and d > 1e-5 and abs(s) < 0.9 * d:
                continue
            if s < g[i]:
                P[i] = co + nrm * (g[i] - s)
    return P


def clear_points(bm, P, pts, gap, iters=4):
    """The reverse of pushout: obstacle points (body vertices) that come within
    `gap` of the cloth between its vertices push that cloth face outward."""
    polys = [tuple(v.index for v in f.verts) for f in bm.faces]
    lo, hi = P.min(0) - 0.03, P.max(0) + 0.03
    pts = pts[np.all((pts > lo) & (pts < hi), axis=1)]
    for _ in range(iters):
        bvh = BVHTree.FromPolygons([Vector(p) for p in P], polys)
        mag = np.zeros(len(P)); dirn = np.zeros((len(P), 3))
        for q in pts:
            co = Vector(q)
            loc, nrm, fi, d = bvh.find_nearest(co, 0.03)
            if loc is None:
                continue
            s = (co - loc).dot(nrm)
            if d > 1e-5 and abs(s) < 0.9 * d:
                continue
            if -gap < s < 0.008:                    # well above the cloth = beyond it (a chin over a collar)
                for vi in polys[fi]:                # the largest push wins, pushes never add up
                    if s + gap > mag[vi]:
                        mag[vi] = s + gap; dirn[vi] = nrm
        if not mag.any():
            break
        P = P + dirn * mag[:, None]
    return P


def relax_folds(bm, rounds=40, tag=''):
    """Last resort for the few folds left in deep creases (under the crotch,
    in the armpit): plain local smoothing of the folded faces until none is
    left. The body under those spots is hidden by the mask, and the body
    checks run afterwards on the result."""
    P = np.array([v.co[:] for v in bm.verts]); L = laplacian(bm)
    for r in range(rounds):
        write_back(bm, P)
        sel = set()
        for e in bm.edges:
            if len(e.link_faces) == 2 and e.link_faces[0].normal.dot(e.link_faces[1].normal) < 0.0:
                for f in e.link_faces:
                    for v in f.verts:
                        sel.add(v.index)
                        for e2 in v.link_edges:
                            sel.add(e2.other_vert(v).index)
        if not sel:
            break
        w = np.zeros(len(P)); w[sorted(sel)] = 1.0
        for _ in range(4):
            P = P + 0.5 * lap(P, L) * w[:, None]
    write_back(bm, P)
    log('relax folds', tag, 'rounds', r + 1, 'left', len(sel))


def pushout_cloth(bm, P, layers, idx=None):
    """Like pushout, but every point moves along the cloth's own normal, so in a
    crease (crotch, between toes, base of the neck) neighbours never cross."""
    write_back(bm, P)
    N = np.array([v.normal[:] for v in bm.verts])
    idx = range(len(P)) if idx is None else idx
    for lay in layers:
        bvh, gap = lay[0], lay[1]; open_ = len(lay) > 2 and lay[2]
        g = np.broadcast_to(np.asarray(gap, float), (len(P),))
        for i in idx:
            co = Vector(P[i])
            loc, nrm, fi, d = bvh.find_nearest(co, 0.25)
            if loc is None:
                continue
            s = (co - loc).dot(nrm)
            if open_ and d > 1e-5 and abs(s) < 0.9 * d:
                continue
            if s < g[i]:
                nc = Vector(N[i])
                k = max(nc.dot(nrm), 0.35)
                P[i] = co + nc * ((g[i] - s) / k)
    return P


def defold(bm, P, layers, rounds=40, tag=''):
    """Find cloth that folds over itself (neighbouring faces turned > ~80 deg),
    relax it with local smoothing and push it back off the body along the
    cloth's own normals; repeat."""
    L = laplacian(bm)
    for r in range(rounds):
        write_back(bm, P)
        sel = set()
        for e in bm.edges:
            if len(e.link_faces) == 2 and e.link_faces[0].normal.dot(e.link_faces[1].normal) < 0.2:
                for f in e.link_faces:
                    for v in f.verts:
                        sel.add(v.index)
                        for e2 in v.link_edges:
                            sel.add(e2.other_vert(v).index)
        if not sel:
            break
        idx = np.array(sorted(sel)); w = np.zeros(len(P)); w[idx] = 1.0
        for _ in range(6):
            P = P + 0.6 * lap(P, L) * w[:, None]
        P = pushout_cloth(bm, P, layers, idx)
    write_back(bm, P)
    log('defold', tag, 'rounds', r + 1, 'left', len(sel))
    return P


def signed_gaps(P, bvh, maxd=0.25):
    out = np.full(len(P), np.inf)
    for i in range(len(P)):
        loc, nrm, idx, d = bvh.find_nearest(Vector(P[i]), maxd)
        if loc is not None:
            out[i] = (Vector(P[i]) - loc).dot(nrm)
    return out


def boundary_loops(bm):
    adj = defaultdict(list)
    for e in bm.edges:
        if e.is_boundary:
            a, b = e.verts; adj[a].append(b); adj[b].append(a)
    seen = set(); loops = []
    for v in list(adj):
        if v in seen:
            continue
        loop = [v]; seen.add(v); prev = None; cur = v
        while True:
            nxt = [w for w in adj[cur] if w is not prev and w not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]; seen.add(cur); loop.append(cur)
        loops.append(loop)
    return loops


def bvh_of(bm):
    bm.verts.ensure_lookup_table()
    return BVHTree.FromBMesh(bm)


def smooth_loop(bm, pick, iters, layers, polar=False, spread=0.0):
    """Smooth the chosen boundary loop(s) along their length (and the cloth
    next to them), then keep them off the body. polar=True smooths the radius
    around the loop's centre instead (a neckline: it bridges hollows without
    tightening)."""
    for loop in boundary_loops(bm):
        if not pick(loop):
            continue
        P0 = np.array([v.co[:] for v in loop]); P = P0.copy()
        if polar:
            c = P.mean(0); d = P[:, :2] - c[:2]
            r = np.hypot(d[:, 0], d[:, 1]); ph = np.arctan2(d[:, 1], d[:, 0]); z = P[:, 2]
            for _ in range(iters):
                r = (np.roll(r, 1) + 2 * r + np.roll(r, -1)) / 4
                z = (np.roll(z, 1) + 2 * z + np.roll(z, -1)) / 4
            P = np.stack([c[0] + r * np.cos(ph), c[1] + r * np.sin(ph), z], 1)
        else:
            for _ in range(iters):                     # Taubin: smooth without shrinking
                P = P + 0.5 * ((np.roll(P, 1, 0) + np.roll(P, -1, 0)) / 2 - P)
                P = P - 0.53 * ((np.roll(P, 1, 0) + np.roll(P, -1, 0)) / 2 - P)
        P = pushout(P, layers)
        if spread > 0:
            kd = kd_of(P0); D = P - P0
            for v in bm.verts:
                if v.is_boundary:
                    continue
                co, k, d = kd.find(v.co)
                if d < spread:
                    f = 1 - d / spread
                    v.co = v.co + Vector(D[k]) * (f * f * (3 - 2 * f))
        inner = {}
        for v, p in zip(loop, P):
            d = Vector(p) - v.co
            v.co = Vector(p)
            for e in v.link_edges:
                w = e.other_vert(v)
                if not w.is_boundary:
                    inner.setdefault(w, []).append(d)
        for w, ds in inner.items():
            w.co = w.co + sum(ds, Vector()) / len(ds) * 0.5
    bm.normal_update()


class Hang:
    """Cloth hanging under gravity: around an axis, for every direction, the
    cloth below height htop keeps the largest body radius found above it."""
    def __init__(self, pts, origin, axis, ref, htop, dh=0.015, nth=64):
        self.o = np.array(origin, float); a = np.array(axis, float); a /= np.linalg.norm(a)
        u = np.array(ref, float); u -= u.dot(a) * a; u /= np.linalg.norm(u)
        self.a, self.u, self.v = a, u, np.cross(a, u)
        self.htop, self.dh, self.nth = htop, dh, nth
        h, th, r = self.coords(pts)
        hb = np.floor((htop - h) / dh).astype(int); ok = hb >= 0
        nh = hb[ok].max() + 1
        G = np.full((nh, nth), -1.0)
        tb = np.floor((th + np.pi) / (2 * np.pi) * nth).astype(int) % nth
        np.maximum.at(G, (hb[ok], tb[ok]), r[ok])
        for i in range(nh):
            row = G[i]; good = np.where(row > 0)[0]
            if len(good) == 0:
                G[i] = G[i - 1] if i else 0.0; continue
            xs = np.concatenate([good - nth, good, good + nth])
            G[i] = np.interp(np.arange(nth), xs, np.tile(row[good], 3))
        self.R = G.copy()
        G = np.maximum.accumulate(G, axis=0)
        for _ in range(2):
            G = (np.roll(G, 1, 1) + 2 * G + np.roll(G, -1, 1)) / 4
        self.G = G

    def membrane(self, gap, gmin, iters, hang=None):
        """A smooth sheet over the body: it rests on the high points (at least
        gmin away) and bridges the hollows; below the top it can also hang
        straight down (hang = blend per row, 0..1)."""
        Rmin = self.R + gmin
        E = self.R + gap
        for _ in range(iters):
            B = (np.roll(E, 1, 1) + 2 * E + np.roll(E, -1, 1)) / 4
            B[1:-1] = (B[:-2] + 2 * B[1:-1] + B[2:]) / 4
            E = np.maximum(B, Rmin)
        if hang is not None:
            Hc = np.maximum.accumulate(E, axis=0)
            E = np.maximum(E, E * (1 - hang[:, None]) + Hc * hang[:, None])
            for _ in range(6):
                B = (np.roll(E, 1, 1) + 2 * E + np.roll(E, -1, 1)) / 4
                B[1:-1] = (B[:-2] + 2 * B[1:-1] + B[2:]) / 4
                E = np.maximum(B, Rmin)
        self.G = E

    def row_h(self):
        return self.htop - (np.arange(len(self.G)) + 0.5) * self.dh

    def coords(self, P):
        d = np.asarray(P, float) - self.o; h = d @ self.a
        q = d - np.outer(h, self.a); x = q @ self.u; y = q @ self.v
        return h, np.arctan2(y, x), np.hypot(x, y)

    def radius(self, h, th):
        G = self.G; nh = len(G)
        fh = np.clip((self.htop - h) / self.dh - 0.5, 0, nh - 1)
        ft = ((th + np.pi) / (2 * np.pi) * self.nth - 0.5) % self.nth
        h0 = np.floor(fh).astype(int); h1 = np.minimum(h0 + 1, nh - 1); wh = fh - h0
        t0 = np.floor(ft).astype(int) % self.nth; t1 = (t0 + 1) % self.nth; wt = ft - np.floor(ft)
        return (G[h0, t0] * (1 - wh) * (1 - wt) + G[h1, t0] * wh * (1 - wt)
                + G[h0, t1] * (1 - wh) * wt + G[h1, t1] * wh * wt)

    def place(self, h, th, r):
        return (self.o + np.outer(h, self.a) + np.outer(r * np.cos(th), self.u)
                + np.outer(r * np.sin(th), self.v))


def deform_sums(bm, groups):
    dl = bm.verts.layers.deform.active
    return np.array([sum(v[dl].get(g, 0.0) for g in groups) for v in bm.verts])


def to_object(name, bm, mesh=None, coll=None):
    m2 = mesh or bpy.data.meshes.new(name)
    bm.to_mesh(m2); m2.name = name
    # the body's custom (split) normals came along with the copy: they no longer
    # match the moved cloth, so drop them and let the cloth shade smoothly
    for a in ('custom_normal', 'sharp_edge'):
        if a in m2.attributes:
            m2.attributes.remove(m2.attributes[a])
    ob = bpy.data.objects.new(name, m2)
    (coll or scene.collection).objects.link(ob)
    for f in m2.polygons:
        f.use_smooth = True
    while len(m2.uv_layers) > 1:
        m2.uv_layers.remove(m2.uv_layers[-1])
    return ob


def thick_bvh(ob, thickness):
    """BVH of the outer surface of a garment shell of the given thickness."""
    bm = bmesh.new(); bm.from_mesh(ob.data); bm.normal_update()
    for v in bm.verts:
        v.co = v.co + v.normal * thickness
    t = BVHTree.FromBMesh(bm); bm.free(); return t


GARMENTS = {}       # name -> dict(ob, thickness, margin)
HANGS = {}

# ---------------------------------------------------------------- landmarks
def bone(n):
    return BONE[n]


ANKLE = {s: bone('calf_' + s)[1] for s in 'LR'}
TOEJ = {s: bone('toes_' + s)[0] for s in 'LR'}
TOETIP = {s: bone('toes_' + s)[1] for s in 'LR'}
FOOT_MIN_Z = float(CO[SKINV & (CO[:, 2] < 0.05), 2].min())
log('foot bottom z', round(FOOT_MIN_Z, 4))


# ================================================================== detail helpers
def kd_of(points):
    kd = KDTree(max(len(points), 1))
    for i, p in enumerate(points):
        kd.insert(p, i)
    kd.balance()
    return kd


def dist_to(P, points):
    if len(points) == 0:
        return np.ones(len(P))
    kd = kd_of(points)
    return np.array([kd.find(p)[2] for p in P])


def set_attr(mesh, name, vals):
    vals = np.asarray(vals, np.float32)
    if name in mesh.attributes:
        mesh.attributes.remove(mesh.attributes[name])
    if vals.ndim == 1:
        a = mesh.attributes.new(name, 'FLOAT', 'POINT'); a.data.foreach_set('value', vals)
    else:
        a = mesh.attributes.new(name, 'FLOAT_VECTOR', 'POINT'); a.data.foreach_set('vector', vals.ravel())


def raise_band(bm, d, width, amount):
    """A hem or a collar is a double layer: push that band out a little."""
    P = np.array([v.co[:] for v in bm.verts])
    N = smooth_normals(bm, P, 8)
    w = smoothstep(width, width - 0.004, d) * amount
    write_back(bm, P + N * w[:, None])


def sdf_poly(Q, poly):
    d = np.full(len(Q), np.inf); inside = np.zeros(len(Q), bool)
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ab = b - a; t = np.clip(((Q - a) @ ab) / (ab @ ab), 0, 1)
        d = np.minimum(d, np.linalg.norm(Q - (a + np.outer(t, ab)), axis=1))
        inside ^= ((a[1] > Q[:, 1]) != (b[1] > Q[:, 1])) & (Q[:, 0] < (b[0] - a[0]) * (Q[:, 1] - a[1]) / (b[1] - a[1] + 1e-12) + a[0])
    return np.where(inside, -d, d)


def jeans_details(bm):
    loops = boundary_loops(bm)
    waist = [l for l in loops if np.mean([v.co.z for v in l]) > 0.9]
    hems = [l for l in loops if np.mean([v.co.z for v in l]) < 0.3]
    P = np.array([v.co[:] for v in bm.verts])
    dw = dist_to(P, np.array([v.co[:] for l in waist for v in l]))
    dh = dist_to(P, np.array([v.co[:] for l in hems for v in l]))
    # back patch pockets and the yoke (in the x-z plane of the back)
    back = P[:, 1] > 0.02
    pk = np.ones(len(P))
    for sx in (1.0, -1.0):
        poly = np.array([(0.020, 0.946), (0.150, 0.936), (0.145, 0.810), (0.084, 0.788), (0.024, 0.812)]) * [sx, 1]
        if sx < 0:
            poly = poly[::-1]
        pk[back] = np.minimum(pk[back], sdf_poly(P[back][:, [0, 2]], poly))
    yoke = np.where(back, P[:, 2] - (0.958 - 0.03 * (1 - np.clip(np.abs(P[:, 0]) / 0.16, 0, 1))), 1.0)
    # double layers: waistband, hems, pockets
    lift = (smoothstep(0.038, 0.034, dw) * 0.0015 + smoothstep(0.02, 0.016, dh) * 0.0010
            + smoothstep(0.0, -0.003, pk) * 0.0010)
    write_back(bm, P + smooth_normals(bm, P, 8) * lift[:, None])
    relax_folds(bm, tag='jeans bands')
    P = np.array([v.co[:] for v in bm.verts])
    # side seams / inseams: r*sin(theta) about each leg, theta from the outside of the leg
    seam = np.zeros(len(P)); seamc = np.zeros(len(P)); fade = np.zeros(len(P))
    for s in 'LR':
        m = P[:, 0] * SIDE[s] >= 0
        h, th, r = HANGS[s].coords(P[m])
        seam[m] = r * np.sin(th); seamc[m] = np.cos(th)
        front = np.clip(-np.sin(th) * SIDE[s], 0, 1)
        zz = P[m, 2]
        thigh = np.exp(-((zz - 0.66) / 0.15) ** 2) * front ** 1.5
        knee = np.exp(-((zz - 0.53) / 0.045) ** 2) * np.clip(front + 0.3, 0, 1)
        fade[m] = np.maximum(thigh * 0.9, knee * 0.75)
    seat = np.exp(-((P[:, 2] - 0.86) / 0.06) ** 2) * np.clip((P[:, 1] - 0.03) / 0.06, 0, 1) * 0.5
    fade = np.maximum(fade, seat)
    return dict(rest=P, dw=dw, dh=dh, pk=pk, yoke=yoke, seam=seam, seamc=seamc, fade=fade)


def shirt_details(bm):
    loops = boundary_loops(bm)
    def cen(l):
        return np.mean([v.co[:] for v in l], axis=0)
    neck = [l for l in loops if cen(l)[2] > 1.4 and abs(cen(l)[0]) < 0.1]
    hems = [l for l in loops if not (cen(l)[2] > 1.4 and abs(cen(l)[0]) < 0.1)]
    P = np.array([v.co[:] for v in bm.verts])
    dn = dist_to(P, np.array([v.co[:] for l in neck for v in l]))
    dh = dist_to(P, np.array([v.co[:] for l in hems for v in l]))
    raise_band(bm, dn, 0.018, 0.0011)      # rib collar
    raise_band(bm, dh, 0.022, 0.0007)      # hems
    relax_folds(bm, tag='shirt bands')
    P = np.array([v.co[:] for v in bm.verts])
    nu = np.arctan2(P[:, 0], -P[:, 1]) * 0.075          # along the neckline (for the rib)
    arm_seam = np.ones(len(P))
    for s in 'LR':
        J, E = bone('upperarm_' + s); a = np.array((E - J).normalized())
        m = P[:, 0] * SIDE[s] > 0.12
        arm_seam[m] = (P[m] - np.array(J + (E - J) * 0.04)) @ a
    sh_seam = np.where((P[:, 2] > 1.44) & (np.abs(P[:, 0]) > 0.07) & (np.abs(P[:, 0]) < 0.2), P[:, 1] - 0.004, 1.0)
    return dict(rest=P, neck=dn, hem=dh, nu=nu, arm_seam=arm_seam, sh_seam=sh_seam)


# ================================================================== SOCKS
def make_socks():
    for s in 'LR':
        sx = SIDE[s]
        keep = SKINV & (CO[:, 0] * sx > 0.03) & (CO[:, 2] < 0.17) & (LEGW[s] > 0.5)
        m2, bm = new_garment('Socks_' + s, keep)
        # only the part that can ever be seen: the ankle, from well inside the
        # shoe (no toes: under the shoe nothing shows, and toes only fold cloth)
        c = ANKLE[s]
        levelset_cut(bm, lambda co: co.z - 0.135)
        levelset_cut(bm, lambda co: 0.028 - co.z)
        levelset_cut(bm, lambda co: math.hypot(co.x - c.x, co.y - c.y) - 0.10)
        keep_largest(bm); subdivide(bm)
        P, N = arrays(bm)
        L = laplacian(bm)
        P = P + smooth_normals(bm, P) * 0.0018
        for _ in range(3):
            P = taubin(P, L, 2)
            P = pushout(P, [(BODY_BVH, 0.0014)])
        P = clear_points(bm, P, CO[SKINV], 0.0010)
        P = defold(bm, P, [(BODY_BVH, 0.0014)], tag='socks ' + s)
        write_back(bm, P)
        top = np.array([v.co[:] for l in boundary_loops(bm) for v in l])
        dtop = dist_to(P, top)
        ob = to_object('Socks_' + s, bm, m2); bm.free()
        set_attr(ob.data, 'rest', P); set_attr(ob.data, 'top', dtop)
        GARMENTS[ob.name] = dict(ob=ob, thickness=0.0009, margin=0.018)
        log('socks', s, len(ob.data.vertices), 'verts')


# ================================================================== SHOES
SOLE_BOTTOM = FOOT_MIN_Z - 0.023       # 2.3 cm of sole under the heel
SHOE_FLOOR = FOOT_MIN_Z - 0.005        # the inside bottom of the upper
SHOE_PARTS = {}


def foot_frame(s):
    """Ankle-centred frame of a foot: forward (heel -> toes), lateral (outward)."""
    c = ANKLE[s]; tip = TOETIP[s]
    fwd = Vector((tip.x - c.x, tip.y - c.y, 0)).normalized()
    lat = Vector((fwd.y, -fwd.x, 0)) * (1 if fwd.y * SIDE[s] < 0 else -1)
    if lat.x * SIDE[s] < 0:
        lat = -lat
    return c, fwd, lat


def convex_hull_2d(pts):
    pts = sorted(set(map(tuple, np.round(pts, 6))))
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return np.array(lo[:-1] + up[:-1])


def resample_closed(poly, n):
    P = np.vstack([poly, poly[:1]])
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1); cum = np.concatenate([[0], np.cumsum(seg)])
    t = np.linspace(0, cum[-1], n, endpoint=False)
    return np.stack([np.interp(t, cum, P[:, k]) for k in range(2)], 1)


def make_shoes():
    for s in 'LR':
        sx = SIDE[s]
        c, fwd, lat = foot_frame(s)
        sock_bvh = thick_bvh(GARMENTS['Socks_' + s]['ob'], GARMENTS['Socks_' + s]['thickness'])
        # 1. convex hull of the foot -> a smooth blob (toes merged, arch bridged)
        sel = SKINV & (CO[:, 0] * sx > 0.04) & (CO[:, 2] < 0.145) & (LEGW[s] > 0.5)
        hb = bmesh.new()
        for p in CO[sel]:
            hb.verts.new(p)
        res = bmesh.ops.convex_hull(hb, input=hb.verts[:])
        drop = {g for g in res['geom_interior'] + res['geom_unused'] if isinstance(g, bmesh.types.BMVert)}
        bmesh.ops.delete(hb, geom=list(drop), context='VERTS')
        tmp = to_object('tmp_hull', hb); hb.free()
        rm = tmp.modifiers.new('rm', 'REMESH'); rm.mode = 'VOXEL'; rm.voxel_size = 0.0032
        dg = bpy.context.evaluated_depsgraph_get()
        rmesh = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
        bpy.data.objects.remove(tmp)
        bm = bmesh.new(); bm.from_mesh(rmesh); bpy.data.meshes.remove(rmesh)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        P, N = arrays(bm); L = laplacian(bm)
        # 2. inflate, round off, keep 6 mm from the foot and 3 mm from the sock
        P = P + N * 0.0068
        # shrink-wrap: the hull settles onto the foot (a lower, sleeker toe box)
        # but still bridges the toes and the arch
        for _ in range(8):
            for _ in range(4):
                P = P + 0.5 * lap(P, L)
            P = pushout(P, [(BODY_BVH, 0.0058), (sock_bvh, 0.0032, True)])
        P = taubin(P, L, 4)
        P = pushout(P, [(BODY_BVH, 0.0058), (sock_bvh, 0.0032, True)])
        write_back(bm, P)
        # the sole closes the shoe: cut the upper off just above its floor
        # (flattening it instead would fold the cloth under the foot)
        levelset_cut(bm, lambda co: SHOE_FLOOR + 0.003 - co.z)
        keep_largest(bm)
        # the shoe's mesh spacing is ~3 mm: edges under 1.2 mm are cut slivers
        bmesh.ops.dissolve_degenerate(bm, dist=0.0012, edges=bm.edges[:])
        relax_folds(bm, tag='shoe ' + s)
        # 3. collar: low at the sides (under the ankle bones), higher at the
        #    heel, highest in front (the tongue)
        def collar(co):
            dx, dy = co.x - c.x, co.y - c.y
            d = math.hypot(dx, dy)
            back = (dx * -fwd.x + dy * -fwd.y) / max(d, 1e-6)        # 1 behind, -1 in front
            h = 0.063 + 0.015 * max(back, 0) ** 2 + 0.047 * max(-back, 0) ** 3
            return co.z - (h + 30.0 * max(0.0, d - 0.08) ** 2)
        levelset_cut(bm, collar)
        keep_largest(bm)
        P, N = arrays(bm); L = laplacian(bm)
        bnd = np.array([v.is_boundary for v in bm.verts])
        # smooth the collar line itself
        P = taubin(P, L, 4, wmask=bnd.astype(float))
        P = pushout(P, [(BODY_BVH, 0.0058), (sock_bvh, 0.0032, True)])
        write_back(bm, P)
        up = to_object('Shoe_' + s, bm)
        loops = boundary_loops(bm)
        collar_loop = np.array([v.co[:] for v in max(loops, key=lambda l: np.mean([v.co.z for v in l]))])
        bm.free()
        U = np.array([v.co[:] for v in up.data.vertices])
        rel = U[:, :2] - np.array(c[:2])
        set_attr(up.data, 'rest', U)
        set_attr(up.data, 'fw', rel @ np.array(fwd[:2]))
        set_attr(up.data, 'lat', rel @ np.array(lat[:2]))
        set_attr(up.data, 'col', dist_to(U, collar_loop))
        SHOE_PARTS[s] = dict(upper=up, frame=(c, fwd, lat), collar=collar_loop)
        log('shoe', s, 'upper', len(up.data.vertices), 'verts, collar z %.3f..%.3f' % (collar_loop[:, 2].min(), collar_loop[:, 2].max()))
        make_sole(s); make_collar_pad(s); make_laces(s)


def make_sole(s):
    up = SHOE_PARTS[s]['upper']; c, fwd, lat = SHOE_PARTS[s]['frame']
    U = np.array([v.co[:] for v in up.data.vertices])
    band = U[U[:, 2] < SHOE_FLOOR + 0.024]
    hull = convex_hull_2d(band[:, :2])
    ring = resample_closed(hull, 128)
    # smooth the outline and get outward normals (counter-clockwise hull)
    for _ in range(3):
        ring = (np.roll(ring, 1, 0) + 2 * ring + np.roll(ring, -1, 0)) / 4
    tang = np.roll(ring, -1, 0) - np.roll(ring, 1, 0)
    nrm = np.stack([tang[:, 1], -tang[:, 0]], 1); nrm /= np.linalg.norm(nrm, axis=1)[:, None]
    fw = (ring - np.array(c[:2])) @ np.array(fwd[:2])
    fj = (Vector(TOEJ[s][:2]) - Vector(c[:2])).dot(fwd[:2]); ft = fw.max()
    spring = 0.008 * smoothstep(fj, ft, fw) ** 2
    zb = SOLE_BOTTOM
    rim = SHOE_FLOOR + 0.016
    prof = [(0.0011, rim), (0.0030, rim - 0.0035), (0.0038, rim - 0.010), (0.0038, zb + 0.005),
            (0.0030, zb + 0.0013), (0.0008, zb)]
    bm = bmesh.new(); rings = []
    for off, z in prof:
        rings.append([bm.verts.new((ring[i, 0] + nrm[i, 0] * off, ring[i, 1] + nrm[i, 1] * off, z + spring[i]))
                      for i in range(len(ring))])
    n = len(ring)
    for k in range(len(rings) - 1):
        A, B = rings[k], rings[k + 1]
        for i in range(n):
            bm.faces.new((A[i], A[(i + 1) % n], B[(i + 1) % n], B[i]))
    top = bm.faces.new(rings[0][::-1]); top.material_index = 1          # insole
    bm.faces.new(rings[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    for f in bm.faces:
        f.smooth = True
    top.smooth = False
    ob = to_object('Sole_' + s, bm); bm.free()
    SHOE_PARTS[s]['sole'] = ob
    # the rim must stay outside the upper
    ubvh = BVHTree.FromObject(up, bpy.context.evaluated_depsgraph_get())
    rim_pts = np.array([v.co[:] for v in ob.data.vertices])[:n]
    gaps = signed_gaps(rim_pts, ubvh)
    log('sole', s, 'rim gap to upper min %.4f' % gaps.min())


def sweep_tube(bm, pts, radius, closed, nseg=8, flat=1.0, up_hint=None):
    """Tube (or flattened ribbon) along a polyline."""
    pts = [Vector(p) for p in pts]; n = len(pts); rings = []
    for i in range(n):
        a = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        b = pts[(i + 1) % n] if (closed or i < n - 1) else pts[i]
        t = (b - a).normalized()
        up = Vector(up_hint[i]) if up_hint is not None else Vector((0, 0, 1))
        x = t.cross(up).normalized(); y = x.cross(t).normalized()
        rings.append([bm.verts.new(pts[i] + x * radius * math.cos(2 * math.pi * k / nseg)
                                   + y * radius * flat * math.sin(2 * math.pi * k / nseg)) for k in range(nseg)])
    for i in range(n if closed else n - 1):
        A, B = rings[i], rings[(i + 1) % n]
        for k in range(nseg):
            bm.faces.new((A[k], A[(k + 1) % nseg], B[(k + 1) % nseg], B[k]))
    if not closed:
        bm.faces.new(rings[0][::-1]); bm.faces.new(rings[-1])
    return rings


def make_collar_pad(s):
    """A padded roll along the shoe opening."""
    loop = SHOE_PARTS[s]['collar']; c, fwd, lat = SHOE_PARTS[s]['frame']
    up = SHOE_PARTS[s]['upper']
    ubvh = BVHTree.FromObject(up, bpy.context.evaluated_depsgraph_get())
    # resample, smooth
    L = len(loop)
    pts = loop.copy()
    for _ in range(4):
        pts = (np.roll(pts, 1, 0) + 2 * pts + np.roll(pts, -1, 0)) / 4
    out = []
    for p in pts:
        loc, nrm, idx, d = ubvh.find_nearest(Vector(p))
        out.append(Vector(p) + nrm * 0.0020 - Vector((0, 0, 0.0015)))
    bm = bmesh.new()
    sweep_tube(bm, out, 0.0042, True, nseg=10)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    ob = to_object('ShoeCollar_' + s, bm); bm.free()
    SHOE_PARTS[s]['pad'] = ob


def surface_point(bvh, x, y, z0=0.4):
    hit = bvh.ray_cast(Vector((x, y, z0)), Vector((0, 0, -1)), 1.0)
    return hit[0]


def make_laces(s):
    c, fwd, lat = SHOE_PARTS[s]['frame']
    up = SHOE_PARTS[s]['upper']
    ubvh = BVHTree.FromObject(up, bpy.context.evaluated_depsgraph_get())
    # where the opening ends in front: the lacing starts just ahead of it
    loop = SHOE_PARTS[s]['collar']
    front = max(((Vector(p[:2]) - Vector(c[:2])).dot(fwd[:2]) for p in loop))
    bm = bmesh.new()
    eyelets = []
    def on_top(q):
        hit = surface_point(ubvh, q.x, q.y)
        return hit if (hit is not None and hit.z > 0.05) else None
    for k in range(5):
        f = front + 0.016 + k * 0.0125
        half = 0.0165 - k * 0.0010
        pts = []
        for j in range(11):
            l = -half + 2 * half * j / 10
            q = Vector(c[:2]) + Vector(fwd[:2]) * f + Vector(lat[:2]) * l
            hit = on_top(q)
            if hit is None:
                pts = []; break
            pts.append(hit + Vector((0, 0, 0.0030 + 0.0014 * (1 - (l / half) ** 2))))
        if len(pts) > 3:
            sweep_tube(bm, pts, 0.0036, False, nseg=8, flat=0.36,
                       up_hint=[Vector((0, 0, 1))] * len(pts))
            eyelets += [pts[0], pts[-1]]
    # a bow on the top lace: two loops and two tails resting on the shoe
    f0 = front + 0.016
    for side_l in (-1, 1):
        lp = []
        for j in range(24):
            a = 2 * math.pi * j / 24
            l = side_l * (0.012 + 0.011 * (1 - math.cos(a)) / 2 * 1.6)
            ff = f0 + 0.0075 * math.sin(a)
            q = Vector(c[:2]) + Vector(fwd[:2]) * ff + Vector(lat[:2]) * (l * 0.9)
            hit = on_top(q)
            if hit is not None:
                lp.append(hit + Vector((0, 0, 0.0042)))
        if len(lp) > 8:
            sweep_tube(bm, lp, 0.0016, True, nseg=6)
        tail = []
        for j in range(7):
            l = side_l * (0.004 + 0.004 * j)
            ff = f0 + 0.002 + 0.006 * j
            q = Vector(c[:2]) + Vector(fwd[:2]) * ff + Vector(lat[:2]) * l
            hit = on_top(q)
            if hit is not None:
                tail.append(hit + Vector((0, 0, 0.0035)))
        if len(tail) > 2:
            sweep_tube(bm, tail, 0.0016, False, nseg=6)
    # knot
    q = Vector(c[:2]) + Vector(fwd[:2]) * (f0 - 0.001)
    hit = on_top(q)
    if hit is not None:
        bmesh.ops.create_uvsphere(bm, u_segments=10, v_segments=6, radius=0.0042,
                                  matrix=Matrix.Translation(hit + Vector((0, 0, 0.0035))) @ Matrix.Diagonal((1.2, 0.9, 0.6, 1)))
    # eyelets: small metal rings at the lace ends
    eb = bmesh.new()
    for p in eyelets:
        loc, nrm, idx, d = ubvh.find_nearest(p)
        mtx = Matrix.Translation(loc + nrm * 0.0007) @ nrm.to_track_quat('Z', 'Y').to_matrix().to_4x4()
        r = bmesh.ops.create_circle(eb, cap_ends=False, segments=10, radius=0.0028, matrix=mtx)
        inner = bmesh.ops.create_circle(eb, cap_ends=False, segments=10, radius=0.0016, matrix=mtx)
        A, B = r['verts'], inner['verts']
        for k in range(10):
            eb.faces.new((A[k], A[(k + 1) % 10], B[(k + 1) % 10], B[k]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    ob = to_object('Laces_' + s, bm); bm.free()
    eo = to_object('Eyelets_' + s, eb); eb.free()
    SHOE_PARTS[s]['laces'] = ob; SHOE_PARTS[s]['eyelets'] = eo


# ================================================================== JEANS
JEANS_HEM_TOP = 0.16
HEM_LOOPS = {}


def waist_z(y):
    return 0.985 + (y + 0.15) * 0.152          # mid-rise, a little higher at the back


def make_jeans():
    keep = SKINV & (CO[:, 2] < 1.07) & (ARMW < 0.3) & (CO[:, 2] > JEANS_HEM_TOP - 0.03)
    m2, bm = new_garment('Jeans', keep)
    levelset_cut(bm, lambda c: c.z - waist_z(c.y))
    levelset_cut(bm, lambda c: JEANS_HEM_TOP - c.z)
    keep_largest(bm); subdivide(bm)
    P, N = arrays(bm); L = laplacian(bm)
    z = P[:, 2]
    g = np.interp(z, [0.7, 0.82, 0.92, 0.97, 1.05], [0.0095, 0.008, 0.0062, 0.0042, 0.0038])
    T = P + smooth_normals(bm, P) * g[:, None]
    # below the knee the denim hangs straight from the knee and calf
    for s in 'LR':
        sx = SIDE[s]
        th_h, ca_t = bone('thigh_' + s)[0], bone('calf_' + s)[1]
        axis = th_h - ca_t
        sel = (SAMP_LEG[s] > 0.6) & (SAMP[:, 2] < 0.62)
        hang = Hang(SAMP[sel], ca_t, axis, (sx, 0, 0), htop=float((Vector((0, 0, 0.56)) - ca_t).dot(axis.normalized())))
        side = (T[:, 0] * sx > 0) & (z < 0.62)
        h, th, r = hang.coords(T[side])
        b = 0.9 * smoothstep(0.56, 0.44, z[side])
        H = hang.radius(h, th) + 0.009
        r2 = np.maximum(r, r * (1 - b) + H * b)
        T[side] = hang.place(h, th, r2)
        HANGS[s] = hang
    for _ in range(6):
        T = taubin(T, L, 2)
        T = pushout(T, [(BODY_BVH, np.maximum(g * 0.75, 0.0032))])
    # the crotch: denim bridges it in one smooth curve
    cw = (smoothstep(0.085, 0.035, np.abs(P[:, 0])) * smoothstep(0.78, 0.84, z) * smoothstep(0.98, 0.93, z))
    for _ in range(12):
        for _ in range(3):
            T = T + 0.5 * lap(T, L) * cw[:, None]
        T = pushout_cloth(bm, T, [(BODY_BVH, 0.0035)])
    T = clear_points(bm, T, CO[SKINV], 0.003)
    T = defold(bm, T, [(BODY_BVH, 0.0032)], tag='jeans')
    write_back(bm, T)
    extend_legs(bm)
    Pj, _ = arrays(bm)
    layers = [(BODY_BVH, 0.0032)]
    for s in 'LR':
        dg = bpy.context.evaluated_depsgraph_get()
        layers += [(BVHTree.FromObject(SHOE_PARTS[s][k], dg), 0.004, True) for k in ('upper', 'sole', 'pad', 'laces')]
        layers += [(thick_bvh(GARMENTS['Socks_' + s]['ob'], 0.001), 0.006, True)]
    defold(bm, Pj, layers, tag='jeans (all)')
    relax_folds(bm, tag='jeans')
    attrs = jeans_details(bm)
    ob = to_object('Jeans', bm, m2); bm.free()
    for k, v in attrs.items():
        set_attr(ob.data, k, v)
    GARMENTS['Jeans'] = dict(ob=ob, thickness=0.0024, margin=lambda p, i: 0.07 if p[2] < 0.3 else 0.03)
    log('jeans', len(ob.data.vertices), 'verts')


def extend_legs(bm):
    """Continue each trouser leg straight down from z=0.16 to the hem, which
    rests on the shoe in front (a slight break) and drops lower at the back."""
    dl = bm.verts.layers.deform.active
    loops = [l for l in boundary_loops(bm) if np.mean([v.co.z for v in l]) < JEANS_HEM_TOP + 0.02]
    dg = bpy.context.evaluated_depsgraph_get()
    for loop in loops:
        s = 'L' if np.mean([v.co.x for v in loop]) > 0 else 'R'
        parts = [SHOE_PARTS[s][k] for k in ('upper', 'sole', 'pad', 'laces')]
        shoe_bvhs = [BVHTree.FromObject(o, dg) for o in parts]
        sock_bvh = thick_bvh(GARMENTS['Socks_' + s]['ob'], 0.001)
        c = ANKLE[s]; fwd = SHOE_PARTS[s]['frame'][1]
        zh = []
        for v in loop:
            back = Vector((v.co.x - c.x, v.co.y - c.y)).normalized().dot(-Vector(fwd[:2]))
            base = 0.05 + 0.012 * max(0.0, -back)
            top = base
            for t in shoe_bvhs:
                hit = t.ray_cast(Vector((v.co.x, v.co.y, 0.4)), Vector((0, 0, -1)), 1.0)[0]
                if hit is not None:
                    top = max(top, hit.z + 0.011)
            zh.append(min(top, JEANS_HEM_TOP - 0.02))
        # smooth the hem height along the loop
        zh = np.array(zh); n = len(zh)
        for _ in range(10):
            zh = np.maximum(zh, (np.roll(zh, 1) + 2 * zh + np.roll(zh, -1)) / 4)
        for _ in range(4):
            zh = (np.roll(zh, 1) + 2 * zh + np.roll(zh, -1)) / 4
        steps = 5
        prev = {v: v for v in loop}
        idx = {v: i for i, v in enumerate(loop)}
        layers = []
        for k in range(1, steps + 1):
            cur = {}
            for v in loop:
                t = k / steps
                co = Vector((v.co.x, v.co.y, v.co.z + (zh[idx[v]] - v.co.z) * t))
                nv = bm.verts.new(co)
                kf = 0.45 * t * t                  # the hem rests on the shoe: it follows the foot a little
                for gk, w in v[dl].items():
                    nv[dl][gk] = w * (1 - kf)
                fg = GI['foot_' + s]
                nv[dl][fg] = nv[dl].get(fg, 0.0) + kf
                cur[v] = nv
            layers.append((prev, cur)); prev = cur
        # faces: follow the winding of the boundary face of every edge
        bedges = [e for e in bm.edges if e.is_boundary and e.verts[0] in idx and e.verts[1] in idx]
        for e in bedges:
            l0 = e.link_loops[0]
            a, b = l0.vert, l0.link_loop_next.vert
            for P0, P1 in layers:
                bm.faces.new((P0[b], P0[a], P1[a], P1[b]))
        bm.normal_update()
        # keep the new tube clear of the shoe and the sock
        for it in range(3):
            newv = [v for P0, P1 in layers for v in P1.values()]
            pts = np.array([v.co[:] for v in newv])
            pts = pushout(pts, [(t, 0.004, True) for t in shoe_bvhs] + [(sock_bvh, 0.006, True)])
            for v, p in zip(newv, pts):
                v.co = p
            # keep every ring smooth along the leg's circumference
            for P0, P1 in layers:
                ring = np.array([P1[v].co[:] for v in loop])
                ring = (np.roll(ring, 1, 0) + 2 * ring + np.roll(ring, -1, 0)) / 4
                for v, p in zip(loop, ring):
                    P1[v].co = p
        HEM_LOOPS[s] = [layers[-1][1][v] for v in loop]
        log('jeans hem', s, 'z %.3f..%.3f' % (zh.min(), zh.max()))


# ================================================================== T-SHIRT
SHIRT_HEM = 0.905
NECK_Z = (1.472, 1.578, 1.562)       # crew neckline height: front (below the throat hollow), sides, back


def _ss(t):
    t = min(max(t, 0.0), 1.0); return t * t * (3 - 2 * t)


def neck_fn(co):
    r = math.hypot(co.x, co.y)
    if co.z < 1.43 or r > 0.13:
        return -1.0
    phi = abs(math.atan2(co.x, -co.y))                    # 0 front, pi back
    f, sd, b = NECK_Z
    # a round crew neck: low across the front, rising near the sides
    zn = f + (sd - f) * (phi / (math.pi / 2)) ** 2.5 if phi < math.pi / 2 else sd + (b - sd) * _ss(phi / (math.pi / 2) - 1)
    return co.z - zn


def sleeve_fn(s, tcut=0.5):
    J, E = bone('upperarm_' + s)
    a = (E - J).normalized(); Pc = J + (E - J) * tcut
    def f(co):
        d = co - J; t = d.dot(a)
        if t < 0.02 or (d - a * t).length > 0.15:
            return -1.0
        return (co - Pc).dot(a)
    return f


def rigid_sleeves(bm, P0):
    """Toward the sleeve end the cloth follows the upper-arm bone rigidly (the
    skin weights blend in the shoulder and chest there, which would let the
    gap under the arm collapse when the arm comes down)."""
    dl = bm.verts.layers.deform.active
    for s in 'LR':
        J, E = bone('upperarm_' + s); a = np.array((E - J).normalized()); Ln = (E - J).length
        t = ((P0 - np.array(J)) @ a) / Ln
        k = smoothstep(0.12, 0.38, t) * (P0[:, 0] * SIDE[s] > 0.15)
        ua = GI['upperarm_' + s]
        for v, kk in zip(bm.verts, k):
            if kk <= 0:
                continue
            d = v[dl]
            for g in list(d.keys()):
                d[g] = d[g] * (1 - kk)
            d[ua] = d.get(ua, 0.0) + kk


def weights_like(bm, other, k):
    """Blend each vertex's bone weights (by k) toward those of the nearest
    vertices of another garment: the shirt's hem moves with the jeans under it."""
    dl = bm.verts.layers.deform.active
    O = other.data
    kd = kd_of(np.array([v.co[:] for v in O.vertices]))
    for v, kk in zip(bm.verts, k):
        if kk <= 0:
            continue
        acc = defaultdict(float); tot = 0.0
        for co, i, d in kd.find_n(v.co, 4):
            w = 1.0 / (d + 1e-3); tot += w
            for g in O.vertices[i].groups:
                acc[g.group] += w * g.weight
        dv = v[dl]
        for g in list(dv.keys()):
            dv[g] = dv[g] * (1 - kk)
        for g, w in acc.items():
            dv[g] = dv.get(g, 0.0) + kk * w / tot


def smooth_weights(bm, mask, iters):
    """Diffuse the bone weights over the cloth (where mask > 0) so that
    neighbouring points never move very differently."""
    dl = bm.verts.layers.deform.active
    bm.verts.index_update()
    groups = sorted({g for v in bm.verts for g in v[dl].keys()})
    col = {g: i for i, g in enumerate(groups)}
    Wm = np.zeros((len(bm.verts), len(groups)))
    for v in bm.verts:
        for g, w in v[dl].items():
            Wm[v.index, col[g]] = w
    L = laplacian(bm)
    for _ in range(iters):
        Wm = Wm + 0.5 * lap(Wm, L) * mask[:, None]
    Wm = np.clip(Wm, 0, None); Wm /= np.maximum(Wm.sum(1, keepdims=True), 1e-9)
    for v in bm.verts:
        if mask[v.index] <= 0:
            continue
        d = v[dl]
        for g in list(d.keys()):
            del d[g]
        for g, i in col.items():
            if Wm[v.index, i] > 0.002:
                d[g] = float(Wm[v.index, i])


def make_shirt():
    # generous region; the neckline, sleeve and hem cuts give the real edges and
    # whatever ends up detached from the shirt (the chin) is dropped
    keep = SKINV & (CO[:, 2] > SHIRT_HEM - 0.04) & (CO[:, 2] < 1.6) & (LOWARMW < 0.2)
    m2, bm = new_garment('TShirt', keep)
    levelset_cut(bm, lambda c: SHIRT_HEM - c.z)
    levelset_cut(bm, neck_fn)
    for s in 'LR':
        levelset_cut(bm, sleeve_fn(s))
    nc = keep_largest(bm)
    subdivide(bm)
    P, N = arrays(bm); L = laplacian(bm)
    armw = np.clip(deform_sums(bm, G_ARM) * 1.6, 0, 1)
    z = P[:, 2]
    g = np.where(z > 1.44, 0.0045, 0.0062) * np.ones(len(P))
    T = P + smooth_normals(bm, P) * g[:, None]
    # torso: a smooth sheet resting on the chest, shoulder blades, hips and the
    # jeans' waistband, hanging straight down below the chest
    sel = (SAMP_ARM < 0.2) & (SAMP[:, 2] < 1.40) & (SAMP[:, 2] > 0.84)
    jb = bmesh.new(); jb.from_mesh(GARMENTS['Jeans']['ob'].data); jb.normal_update()
    jpts = np.array([(v.co + v.normal * (GARMENTS['Jeans']['thickness'] + 0.006))[:] for v in jb.verts]); jb.free()
    jpts_all = jpts.copy()
    jpts = jpts[jpts[:, 2] > 0.84]
    torso = Hang(np.vstack([SAMP[sel], jpts]), (0, -0.02, 0), (0, 0, 1), (1, 0, 0), htop=1.40, dh=0.012, nth=96)
    torso.membrane(0.0075, 0.0048, 60, hang=0.85 * smoothstep(1.34, 1.2, torso.row_h()))
    h, th, r = torso.coords(T)
    wt = smoothstep(1.37, 1.29, z) * (1 - armw)
    T = T * (1 - wt[:, None]) + torso.place(h, th, torso.radius(h, th)) * wt[:, None]
    # sleeves: a little looser toward the sleeve end
    for s in 'LR':
        J, E = bone('upperarm_' + s); a = np.array((E - J).normalized()); Ln = (E - J).length
        d = P - np.array(J); t = (d @ a) / Ln
        q = d - np.outer(d @ a, a); r = np.linalg.norm(q, axis=1)
        on = (P[:, 0] * SIDE[s] > 0.12) & (r < 0.11)
        ws = smoothstep(0.10, 0.30, t) * armw * on
        gs = 0.0045 + 0.0085 * smoothstep(0.10, 0.5, t)
        Ts = np.array(J) + np.outer(d @ a, a) + q / np.maximum(r, 1e-6)[:, None] * (r + gs)[:, None]
        T = T * (1 - ws[:, None]) + Ts * ws[:, None]
    jeans_out = thick_bvh(GARMENTS['Jeans']['ob'], GARMENTS['Jeans']['thickness'] + 0.0055)
    for _ in range(10):
        T = taubin(T, L, 3)
        T = pushout(T, [(BODY_BVH, 0.0042), (jeans_out, 0.0035, True)])
    jo = jpts_all + 0.0
    T = clear_points(bm, T, np.vstack([CO[SKINV], jo]), 0.003)
    write_back(bm, T)
    smooth_loop(bm, lambda loop: np.mean([v.co.z for v in loop]) > 1.4, 90, [(BODY_BVH, 0.0042)])
    P2, _ = arrays(bm); P2 = pushout(P2, [(BODY_BVH, 0.0042)])
    P2 = clear_points(bm, P2, CO[SKINV], 0.0035)
    P2 = defold(bm, P2, [(BODY_BVH, 0.0042), (jeans_out, 0.0035, True)], tag='shirt')
    smooth_loop(bm, lambda loop: np.mean([v.co.z for v in loop]) < 1.4, 25, [(BODY_BVH, 0.0042), (jeans_out, 0.0035, True)])
    Pf, _ = arrays(bm)
    defold(bm, Pf, [(BODY_BVH, 0.0042), (jeans_out, 0.0035, True)], tag='shirt (final)')
    relax_folds(bm, tag='shirt')
    rigid_sleeves(bm, P)
    weights_like(bm, GARMENTS['Jeans']['ob'], smoothstep(1.10, 1.02, P[:, 2]))
    smooth_weights(bm, smoothstep(1.16, 1.06, P[:, 2]), 25)
    attrs = shirt_details(bm)
    ob = to_object('TShirt', bm, m2); bm.free()
    for k, v in attrs.items():
        set_attr(ob.data, k, v)
    # keep more skin inside the sleeves (you can look into them), less at the neck
    GARMENTS['TShirt'] = dict(ob=ob, thickness=0.0016,
                              margin=lambda p, i: 0.013 if abs(p[0]) > 0.2 else 0.03)
    log('tshirt', len(ob.data.vertices), 'verts, pieces cut away', nc - 1)


# ================================================================== rigging
def weights_from_body(ob, s):
    """Bone weights for the shoe parts: from the nearest foot skin."""
    sel = np.where(SKINV & (LEGW[s] > 0.3) & (CO[:, 2] < 0.3))[0]
    kd = KDTree(len(sel))
    for k, i in enumerate(sel):
        kd.insert(CO[i], k)
    kd.balance()
    groups = [g for g in G_LEG[s] if W[sel, g].max() > 0]
    vgs = {g: ob.vertex_groups.new(name=GN[g]) for g in groups}
    for v in ob.data.vertices:
        acc = defaultdict(float)
        for co, k, d in kd.find_n(v.co, 6):
            w = 1.0 / (d + 2e-3) ** 2
            for g in groups:
                acc[g] += w * W[sel[k], g]
        tot = sum(acc.values())
        for g, w in acc.items():
            if w / tot > 0.005:
                vgs[g].add([v.index], w / tot, 'REPLACE')


def rig(ob, side=None, thickness=0.0):
    ob.parent = arm
    if len(ob.vertex_groups) == 0:
        weights_from_body(ob, side)
    else:
        # drop groups the garment does not use (keeps the file lean)
        used = set()
        for v in ob.data.vertices:
            used.update(g.group for g in v.groups if g.weight > 0)
        for vg in [vg for vg in ob.vertex_groups if vg.index not in used]:
            ob.vertex_groups.remove(vg)
    m = ob.modifiers.new('Armature', 'ARMATURE'); m.object = arm
    if thickness:
        so = ob.modifiers.new('Thickness', 'SOLIDIFY')
        so.thickness = thickness; so.offset = 1.0
        so.use_even_offset = False; so.use_quality_normals = False; so.use_rim = True


# ================================================================== body mask
def mesh_topology(ob):
    bm = bmesh.new(); bm.from_mesh(ob.data)
    polys = [tuple(v.index for v in f.verts) for f in bm.faces]
    bnd = np.array([v.is_boundary for v in bm.verts])
    bm.free()
    return polys, bnd


def build_mask():
    """Hide the body faces under the clothes. A body vertex is under a garment
    when the garment lies over it (along its normal, or as the nearest surface)
    away from the garment's edges; a band of skin is kept at every edge."""
    covered = np.zeros(NV, bool)
    bm0 = bmesh.new(); bm0.from_mesh(me); bm0.verts.ensure_lookup_table(); bm0.normal_update()
    NRM = np.array([v.normal[:] for v in bm0.verts]); bm0.free()
    for name, G in GARMENTS.items():
        ob = G['ob']
        P = np.array([v.co[:] for v in ob.data.vertices])
        polys, bnd = mesh_topology(ob)
        bvh = BVHTree.FromPolygons([Vector(p) for p in P], polys)
        bpts = P[bnd]
        kd = KDTree(max(len(bpts), 1))
        for k, p in enumerate(bpts):
            kd.insert(p, k)
        kd.balance()
        margin = G['margin'] if callable(G['margin']) else (lambda p, i, m=G['margin']: m)
        def away_from_edge(loc, i):
            if len(bpts) == 0:
                return True
            co, k, d = kd.find(loc)
            return d > margin(co, i)
        lo, hi = P.min(0) - 0.05, P.max(0) + 0.05
        cand = np.where(SKINV & np.all((CO > lo) & (CO < hi), axis=1))[0]
        n = 0
        for i in cand:
            co = Vector(CO[i])
            hit = bvh.ray_cast(co, Vector(NRM[i]), 0.07)
            if hit[0] is not None and away_from_edge(hit[0], i):
                covered[i] = True; n += 1; continue
            loc, nrm, idx, d = bvh.find_nearest(co, 0.06)
            if loc is None:
                continue
            s = (co - loc).dot(nrm)
            # under the cloth, or right at it (a crease such as the armpit),
            # and away from the garment's edges
            if (s <= 0 or d < 0.015) and away_from_edge(loc, i):
                covered[i] = True; n += 1
        log('mask:', name, 'covers', n, 'body verts')
    # glTF splits vertices along UV seams: the copies must agree
    key = defaultdict(list)
    for i, c in enumerate(np.round(CO, 6)):
        key[tuple(c)].append(i)
    for idx in key.values():
        if len(idx) > 1 and covered[idx].any():
            covered[idx] = True
    vis = np.where(~covered)[0]
    vg = body.vertex_groups.new(name='visible')
    vg.add(vis.tolist(), 1.0, 'REPLACE')
    mm = body.modifiers.new('HideUnderClothes', 'MASK')
    mm.vertex_group = 'visible'; mm.threshold = 0.5
    REPORT['body_faces_hidden'] = int(sum(1 for p in me.polygons if covered[list(p.vertices)].any()))
    return covered


# ================================================================== checks
WALK = [('spine01', 'Z', 4), ('thigh_L', 'X', -24), ('calf_L', 'X', 6), ('foot_L', 'X', -4),
        ('thigh_R', 'X', 14), ('calf_R', 'X', 28), ('foot_R', 'X', 12),
        ('upperarm_L', 'Y', 30), ('upperarm_L', 'X', 14), ('lowerarm_L', 'X', -14),
        ('upperarm_R', 'Y', -30), ('upperarm_R', 'X', -18), ('lowerarm_R', 'X', -24)]


def apply_pose(spec):
    for name, axis, deg in spec:
        pb = arm.pose.bones[name]
        M = pb.matrix.copy(); h = M.to_translation()
        pb.matrix = Matrix.Translation(h) @ Matrix.Rotation(math.radians(deg), 4, axis) @ Matrix.Translation(-h) @ M
        bpy.context.view_layer.update()


def clear_pose():
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()


def evaluated(ob):
    """Posed coordinates of a mesh, without its thickness / mask modifiers."""
    keep = [(m, m.show_viewport) for m in ob.modifiers if m.type in ('SOLIDIFY', 'MASK')]
    for m, _ in keep:
        m.show_viewport = False
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg); m2 = ev.to_mesh()
    P = np.empty(len(m2.vertices) * 3); m2.vertices.foreach_get('co', P)
    ev.to_mesh_clear()
    for m, v in keep:
        m.show_viewport = v
    return P.reshape(-1, 3)


def check(tag, covered):
    """Count every place where something shows through a garment."""
    bad = {}
    Pb = evaluated(body)
    vis_v = np.where(SKINV & ~covered)[0]
    vis_f = [p for p in skin_polys if not covered[list(p.vertices)].any()]
    pts = np.vstack([Pb[vis_v], np.array([Pb[list(p.vertices)].mean(0) for p in vis_f])])
    rest = np.vstack([CO[vis_v], np.array([CO[list(p.vertices)].mean(0) for p in vis_f])])
    surf = {}
    for name, G in GARMENTS.items():
        P = evaluated(G['ob']); polys, bnd = mesh_topology(G['ob'])
        surf[name] = (P, BVHTree.FromPolygons([Vector(p) for p in P], polys))
    def through(points, bvh, need, maxd=0.03, where=None, outside=False):
        """Points that are not at least `need` under the surface (or, with
        outside=True, at least `need` above it), counting only points over the
        surface, not beyond its edges."""
        n, worst, at = 0, 0.0, []
        for p in points:
            co = Vector(p)
            loc, nrm, idx, d = bvh.find_nearest(co, maxd)
            if loc is None:
                continue
            s = (co - loc).dot(nrm)
            if d > 1e-5 and abs(s) < 0.95 * d:
                continue                       # beyond the garment's edge
            short = (need - s) if outside else (s + need)
            if short > 0:
                n += 1; worst = max(worst, short); at.append(tuple(round(float(x), 3) for x in p))
        if where is not None and at:
            where.extend(at[:4])
        return n, worst

    def through_body(bvh, need):
        n, worst, at = 0, 0.0, []
        for p, r in zip(pts, rest):
            co = Vector(p)
            loc, nrm, idx, d = bvh.find_nearest(co, 0.03)
            if loc is None:
                continue
            s = (co - loc).dot(nrm)
            if d > 1e-5 and abs(s) < 0.95 * d:
                continue
            if s + need > 0:
                n += 1; worst = max(worst, s + need); at.append(tuple(round(float(x), 3) for x in r))
        return n, worst, at
    for name in GARMENTS:
        n, w, at = through_body(surf[name][1], 0.0003)
        bad['body>' + name] = (n, w)
        if at:
            log('   body through %s at rest positions %s' % (name, at[:6]))
    pairs = [('TShirt', 'Jeans')]
    for s in 'LR':
        pairs += [('Jeans', 'Socks_' + s), ('Jeans', 'Shoe_' + s), ('Shoe_' + s, 'Socks_' + s)]
    for outer, inner in pairs:
        need = GARMENTS[inner]['thickness'] + 0.0002     # outer must clear the inner's thickness
        P, _ = surf[outer]
        Pin, bvh_in = surf[inner]
        at = []
        bad[outer + '>' + inner] = through(P, bvh_in, need, 0.02, where=at, outside=True)
        if at:
            log('   %s into %s near %s' % (outer, inner, at))
    # the extra shoe parts must stay outside the sock
    for s in 'LR':
        for part, key in (('ShoeCollar_', 'pad'), ('Laces_', 'laces')):
            P = evaluated(SHOE_PARTS[s][key])
            bad[part + s + '>Socks_' + s] = through(P, surf['Socks_' + s][1], GARMENTS['Socks_' + s]['thickness'] + 0.0002, 0.02, outside=True)
    if tag == 'rest':
        # folds: neighbouring cloth faces must never turn more than 90 degrees
        for name, G in GARMENTS.items():
            P, _ = surf[name]
            bm = bmesh.new(); bm.from_mesh(G['ob'].data)
            for v, p in zip(bm.verts, P):
                v.co = p
            bm.normal_update(); n = 0; at = []
            for e in bm.edges:
                if len(e.link_faces) == 2 and min(f.calc_area() for f in e.link_faces) > 1e-9 \
                        and e.link_faces[0].normal.dot(e.link_faces[1].normal) < -0.2:
                    n += 1; at.append(tuple(round(x, 3) for x in e.verts[0].co))
            bm.free()
            bad['folds:' + name] = (n, 0.0)
            if at:
                log('   folds in %s near %s' % (name, at[:5]))
    tot = sum(v[0] for v in bad.values())
    log('check [%s]: %d problems' % (tag, tot))
    for k, (n, w) in bad.items():
        if n:
            log('   %-24s %5d points, worst %.1f mm' % (k, n, w * 1000))
    REPORT['check_' + tag] = {k: dict(points=v[0], worst_mm=round(v[1] * 1000, 2)) for k, v in bad.items()}
    return tot


# ================================================================== materials
def _sock(coll, ident):
    return next(x for x in coll if x.identifier == ident)


class Shader:
    """A small helper to write Cycles/EEVEE node trees."""
    def __init__(self, name):
        self.mat = bpy.data.materials.new(name); self.mat.use_nodes = True
        self.nt = self.mat.node_tree; self.nt.nodes.clear()
        self.bsdf = self.n('ShaderNodeBsdfPrincipled')
        self.link(self.bsdf.outputs[0], self.n('ShaderNodeOutputMaterial').inputs[0])
        self.rest = None

    def n(self, t, **props):
        node = self.nt.nodes.new(t)
        for k, v in props.items():
            setattr(node, k, v)
        return node

    def link(self, a, b):
        if isinstance(a, (int, float, tuple)):
            b.default_value = a
        else:
            self.nt.links.new(a, b)

    def attr(self, name, vec=False):
        a = self.n('ShaderNodeAttribute', attribute_name=name, attribute_type='GEOMETRY')
        return a.outputs['Vector'] if vec else a.outputs['Fac']

    def R(self):
        if self.rest is None:
            self.rest = self.attr('rest', True)
        return self.rest

    def m(self, op, a, b=0.0, clamp=False):
        node = self.n('ShaderNodeMath', operation=op, use_clamp=clamp)
        self.link(a, node.inputs[0]); self.link(b, node.inputs[1])
        return node.outputs[0]

    def mr(self, x, a, b, c=0.0, d=1.0):
        r = self.n('ShaderNodeMapRange', clamp=True)
        self.link(x, r.inputs['Value'])
        r.inputs['From Min'].default_value = a; r.inputs['From Max'].default_value = b
        r.inputs['To Min'].default_value = c; r.inputs['To Max'].default_value = d
        return r.outputs['Result']

    def line(self, d, w, at=0.0):
        """1 on the line d == at (width w), 0 away from it."""
        return self.mr(self.m('ABSOLUTE', self.m('SUBTRACT', d, at)), w * 0.35, w * 0.7, 1.0, 0.0)

    def dashes(self, period=0.0034, duty=0.62):
        s = self.sep(self.R())
        t = self.m('ADD', self.m('ADD', s[0], s[1]), self.m('MULTIPLY', s[2], 0.8))
        return self.m('LESS_THAN', self.m('FRACT', self.m('DIVIDE', t, period)), duty)

    def mix(self, f, a, b):
        node = self.n('ShaderNodeMix', data_type='RGBA')
        self.link(f, _sock(node.inputs, 'Factor_Float'))
        self.link(a, _sock(node.inputs, 'A_Color')); self.link(b, _sock(node.inputs, 'B_Color'))
        return _sock(node.outputs, 'Result_Color')

    def vmul(self, vec, s):
        node = self.n('ShaderNodeVectorMath', operation='MULTIPLY')
        self.link(vec, node.inputs[0]); node.inputs[1].default_value = s
        return node.outputs[0]

    def sep(self, vec):
        node = self.n('ShaderNodeSeparateXYZ'); self.link(vec, node.inputs[0])
        return node.outputs

    def noise(self, vec, scale, detail=2.0, rough=0.5):
        node = self.n('ShaderNodeTexNoise'); self.link(vec, node.inputs['Vector'])
        node.inputs['Scale'].default_value = scale; node.inputs['Detail'].default_value = detail
        node.inputs['Roughness'].default_value = rough
        return node.outputs['Fac']

    def wave(self, vec, scale, direction='DIAGONAL'):
        node = self.n('ShaderNodeTexWave', wave_type='BANDS', bands_direction=direction)
        self.link(vec, node.inputs['Vector']); node.inputs['Scale'].default_value = scale
        return node.outputs['Fac']

    def voronoi(self, vec, scale):
        node = self.n('ShaderNodeTexVoronoi'); self.link(vec, node.inputs['Vector'])
        node.inputs['Scale'].default_value = scale
        return node.outputs['Distance']

    def bump(self, h, strength, dist):
        b = self.n('ShaderNodeBump'); self.link(h, b.inputs['Height'])
        b.inputs['Strength'].default_value = strength; b.inputs['Distance'].default_value = dist
        self.link(b.outputs['Normal'], self.bsdf.inputs['Normal'])

    def set(self, name, v):
        self.link(v, self.bsdf.inputs[name])


def max_of(S, *xs):
    out = xs[0]
    for x in xs[1:]:
        out = S.m('MAXIMUM', out, x)
    return out


def mat_tshirt():
    S = Shader('Tee_Cotton')
    white = (0.70, 0.70, 0.68, 1)
    neck, hem = S.attr('neck'), S.attr('hem')
    stitch = max_of(S, S.line(hem, 0.0009, 0.019), S.line(hem, 0.0009, 0.025),
                    S.line(neck, 0.0009, 0.0165), S.line(neck, 0.0009, 0.0195))
    stitch = S.m('MULTIPLY', stitch, S.dashes())
    seams = max_of(S, S.line(S.attr('arm_seam'), 0.0012), S.line(S.attr('sh_seam'), 0.0012),
                   S.line(neck, 0.0012, 0.018), S.line(hem, 0.0012, 0.0215))
    rib = S.m('MULTIPLY', S.m('LESS_THAN', neck, 0.0175),
              S.m('MULTIPLY', S.m('SINE', S.m('DIVIDE', S.attr('nu'), 0.00035)), 0.5))
    knit = S.noise(S.R(), 1400.0, 2.0, 0.6)
    h = S.m('ADD', S.m('MULTIPLY', knit, 0.35), rib)
    h = S.m('SUBTRACT', h, S.m('MULTIPLY', max_of(S, stitch, seams), 0.8))
    S.bump(h, 0.22, 0.0006)
    col = S.mix(S.m('MULTIPLY', max_of(S, stitch, seams), 0.25), white, (0.5, 0.5, 0.49, 1))
    col = S.mix(S.m('MULTIPLY', S.noise(S.R(), 40.0, 3.0), 0.12), col, (0.62, 0.62, 0.61, 1))
    S.set('Base Color', col); S.set('Roughness', 0.86)
    S.set('Sheen Weight', 0.45); S.set('Sheen Roughness', 0.45)
    return S.mat


def mat_jeans():
    S = Shader('Denim_Indigo')
    dark, light = (0.007, 0.016, 0.050, 1), (0.055, 0.10, 0.22, 1)
    gold = (0.52, 0.30, 0.07, 1)
    R = S.R()
    slub = S.mr(S.noise(S.vmul(R, (420.0, 420.0, 30.0)), 1.0, 3.0, 0.6), 0.42, 0.68)
    twill = S.wave(R, 440.0)
    wear = S.m('ADD', S.m('MULTIPLY', S.attr('fade'), 0.65), S.m('MULTIPLY', slub, 0.25), clamp=True)
    seam, pk, yoke, dw, dh = S.attr('seam'), S.attr('pk'), S.attr('yoke'), S.attr('dw'), S.attr('dh')
    seam_line = S.m('MULTIPLY', S.line(seam, 0.0016), S.m('LESS_THAN', S.attr('rest'), 9.0))
    edges = max_of(S, S.line(seam, 0.0018), S.line(yoke, 0.0018), S.line(dw, 0.0018, 0.038), S.line(pk, 0.0018))
    # worn ridges along the seams and the edges of the hems and pockets
    ridge = max_of(S, S.mr(S.m('ABSOLUTE', seam), 0.0, 0.006, 1.0, 0.0), S.mr(dh, 0.0, 0.004, 1.0, 0.0),
                   S.mr(S.m('ABSOLUTE', pk), 0.0, 0.004, 1.0, 0.0))
    wear = S.m('ADD', wear, S.m('MULTIPLY', ridge, S.m('MULTIPLY', slub, 0.6)), clamp=True)
    col = S.mix(wear, dark, light)
    col = S.mix(S.m('MULTIPLY', twill, 0.16), col, (0.20, 0.24, 0.32, 1))
    stitch = max_of(S, S.line(seam, 0.0008, 0.006), S.line(seam, 0.0008, -0.006), S.line(seam, 0.0008, 0.012),
                    S.line(yoke, 0.0008, -0.004), S.line(yoke, 0.0008, -0.010),
                    S.line(pk, 0.0008, -0.003), S.line(pk, 0.0008, -0.009),
                    S.line(dw, 0.0008, 0.004), S.line(dw, 0.0008, 0.034), S.line(dh, 0.0008, 0.014))
    stitch = S.m('MULTIPLY', stitch, S.dashes())
    col = S.mix(stitch, col, gold)
    col = S.mix(S.m('MULTIPLY', edges, 0.6), col, (0.008, 0.016, 0.04, 1))
    h = S.m('ADD', S.m('MULTIPLY', twill, 0.5), S.m('MULTIPLY', slub, 0.3))
    h = S.m('SUBTRACT', h, S.m('MULTIPLY', edges, 1.4))
    h = S.m('ADD', h, S.m('MULTIPLY', stitch, 0.5))
    S.bump(h, 0.3, 0.0007)
    S.set('Base Color', col); S.set('Roughness', 0.82)
    S.set('Sheen Weight', 0.2); S.set('Sheen Roughness', 0.5)
    return S.mat


def mat_socks():
    S = Shader('Sock_Cotton')
    s = S.sep(S.R())
    rib = S.m('SINE', S.m('DIVIDE', S.m('ADD', S.m('MULTIPLY', s[0], 1.0), S.m('MULTIPLY', s[1], 0.6)), 0.0006))
    S.bump(S.m('ADD', S.m('MULTIPLY', rib, 0.5), S.noise(S.R(), 900.0)), 0.25, 0.0005)
    S.set('Base Color', (0.42, 0.42, 0.43, 1)); S.set('Roughness', 0.9); S.set('Sheen Weight', 0.4)
    return S.mat


def mat_shoe(front):
    S = Shader('Sneaker_Leather')
    fw, lat, col = S.attr('fw'), S.attr('lat'), S.attr('col')
    z = S.sep(S.R())[2]
    cap_at = S.m('ADD', front + 0.075, S.m('MULTIPLY', S.m('MULTIPLY', lat, lat), 18.0))
    cap = S.m('SUBTRACT', fw, cap_at)                     # > 0 on the toe cap
    heel = S.m('MULTIPLY', S.m('LESS_THAN', fw, -0.028), S.m('LESS_THAN', z, 0.074))
    stay = S.m('MULTIPLY', S.m('LESS_THAN', S.m('ABSOLUTE', lat), 0.031),
               S.m('MULTIPLY', S.m('GREATER_THAN', fw, front - 0.01), S.m('LESS_THAN', fw, front + 0.075)))
    seams = max_of(S, S.line(cap, 0.0012), S.line(S.m('ADD', fw, 0.028), 0.0012))
    stitch = max_of(S, S.line(cap, 0.0007, -0.0025), S.line(cap, 0.0007, -0.0050), S.line(col, 0.0007, 0.006),
                    S.m('MULTIPLY', S.line(S.m('ABSOLUTE', lat), 0.0007, 0.031), stay),
                    S.m('MULTIPLY', S.line(fw, 0.0007, -0.031), S.m('LESS_THAN', z, 0.074)))
    stitch = S.m('MULTIPLY', stitch, S.dashes(0.003, 0.6))
    # suede overlays: toe cap, eyestay, heel; perforations on the white vamp
    suede = max_of(S, S.m('GREATER_THAN', cap, 0.0), stay, heel)
    dots = S.m('LESS_THAN', S.voronoi(S.R(), 300.0), 0.16)
    dots = S.m('MULTIPLY', dots, S.m('MULTIPLY', S.m('LESS_THAN', cap, -0.008),
                                       S.m('MULTIPLY', S.m('GREATER_THAN', fw, front + 0.085), S.m('LESS_THAN', S.m('ABSOLUTE', lat), 0.026))))
    white, grey = (0.80, 0.80, 0.78, 1), (0.40, 0.41, 0.43, 1)
    nap = S.noise(S.R(), 2500.0, 2.0)
    c = S.mix(suede, white, S.mix(S.m('MULTIPLY', nap, 0.35), grey, (0.5, 0.51, 0.53, 1)))
    c = S.mix(S.m('MULTIPLY', stitch, 0.8), c, (0.74, 0.74, 0.72, 1))
    c = S.mix(dots, c, (0.12, 0.12, 0.12, 1))
    grain = S.voronoi(S.R(), 1800.0)
    h = S.m('ADD', S.m('MULTIPLY', grain, 0.25), S.m('MULTIPLY', suede, S.m('ADD', 0.8, S.m('MULTIPLY', nap, 0.3))))
    h = S.m('SUBTRACT', h, S.m('MULTIPLY', max_of(S, seams, dots), 1.0))
    S.bump(h, 0.3, 0.0008)
    S.set('Base Color', c)
    S.set('Roughness', S.mix(suede, (0.38, 0.38, 0.38, 1), (0.92, 0.92, 0.92, 1)))
    S.set('Sheen Weight', S.m('MULTIPLY', suede, 0.6))
    S.set('Coat Weight', 0.12)
    return S.mat


def mat_simple(name, color, rough, metal=0.0, bump_scale=0.0, sheen=0.0):
    S = Shader(name)
    S.set('Base Color', color + (1,)); S.set('Roughness', rough); S.set('Metallic', metal)
    if sheen:
        S.set('Sheen Weight', sheen)
    if bump_scale:
        S.bump(S.noise(S.attr('rest', True) if False else S.n('ShaderNodeTexCoord').outputs['Object'], bump_scale), 0.2, 0.0005)
    return S.mat


def mat_sole():
    S = Shader('Sole_Rubber')
    z = S.sep(S.n('ShaderNodeTexCoord').outputs['Object'])[2]
    stripe = S.m('MULTIPLY', S.m('GREATER_THAN', z, SOLE_BOTTOM + 0.0085), S.m('LESS_THAN', z, SOLE_BOTTOM + 0.0115))
    groove = S.line(z, 0.0012, SHOE_FLOOR + 0.004)
    c = S.mix(stripe, (0.86, 0.85, 0.80, 1), (0.05, 0.07, 0.16, 1))
    S.set('Base Color', c); S.set('Roughness', 0.62)
    S.bump(S.m('SUBTRACT', S.m('MULTIPLY', S.noise(S.n('ShaderNodeTexCoord').outputs['Object'], 600.0), 0.2), groove), 0.3, 0.0008)
    return S.mat


# flat colours for the .glb (glTF keeps colour, roughness and metalness)
GLTF_COLOR = {'Tee_Cotton': ((0.70, 0.70, 0.68), 0.86, 0.0), 'Denim_Indigo': ((0.02, 0.04, 0.10), 0.82, 0.0),
              'Sock_Cotton': ((0.42, 0.42, 0.43), 0.9, 0.0), 'Sneaker_Leather': ((0.80, 0.80, 0.78), 0.42, 0.0),
              'Sole_Rubber': ((0.86, 0.85, 0.80), 0.62, 0.0), 'Insole': ((0.06, 0.06, 0.065), 0.8, 0.0),
              'Lace_Cotton': ((0.86, 0.86, 0.85), 0.9, 0.0), 'Eyelet_Metal': ((0.62, 0.62, 0.64), 0.3, 1.0),
              'Collar_Padding': ((0.55, 0.56, 0.58), 0.85, 0.0)}


def assign_materials():
    def put(ob, mats):
        ob.data.materials.clear()
        for m in mats:
            ob.data.materials.append(m)
        if len(mats) == 1:
            for p in ob.data.polygons:
                p.material_index = 0
    tee, denim, sock = mat_tshirt(), mat_jeans(), mat_socks()
    put(GARMENTS['TShirt']['ob'], [tee]); put(GARMENTS['Jeans']['ob'], [denim])
    lace = mat_simple('Lace_Cotton', (0.86, 0.86, 0.85), 0.9, sheen=0.4)
    metal = mat_simple('Eyelet_Metal', (0.62, 0.62, 0.64), 0.3, metal=1.0)
    pad = mat_simple('Collar_Padding', (0.55, 0.56, 0.58), 0.85, sheen=0.3)
    insole = mat_simple('Insole', (0.06, 0.06, 0.065), 0.8)
    sole = mat_sole()
    for s in 'LR':
        put(GARMENTS['Socks_' + s]['ob'], [sock])
        c, fwd, lat = SHOE_PARTS[s]['frame']
        front = max(((Vector(p[:2]) - Vector(c[:2])).dot(fwd[:2]) for p in SHOE_PARTS[s]['collar']))
        put(SHOE_PARTS[s]['upper'], [mat_shoe(front)])
        put(SHOE_PARTS[s]['sole'], [sole, insole])
        put(SHOE_PARTS[s]['laces'], [lace]); put(SHOE_PARTS[s]['eyelets'], [metal]); put(SHOE_PARTS[s]['pad'], [pad])


def export_glb(path):
    """glTF keeps a flat colour per material: swap in simple copies for the export."""
    swaps = []
    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue
        for slot in ob.material_slots:
            m = slot.material
            if m is None:
                continue
            key = m.name.split('.')[0]
            if key in GLTF_COLOR:
                col, rough, metal = GLTF_COLOR[key]
                g = bpy.data.materials.get('gltf_' + key) or mat_simple('gltf_' + key, col, rough, metal)
                g.name = 'gltf_' + key
                swaps.append((slot, m)); slot.material = g
    bpy.ops.export_scene.gltf(filepath=path, export_format='GLB', export_apply=True, export_skins=True,
                              export_animations=False, export_morph=False, export_yup=True)
    for slot, m in swaps:
        slot.material = m
    for m in [m for m in bpy.data.materials if m.name.startswith('gltf_')]:
        bpy.data.materials.remove(m)


# ================================================================== run
make_socks()
make_shoes()
make_jeans()
make_shirt()
for s in 'LR':
    rig(GARMENTS['Socks_' + s]['ob'], s, GARMENTS['Socks_' + s]['thickness'])
    GARMENTS['Shoe_' + s] = dict(ob=SHOE_PARTS[s]['upper'], thickness=0.0024, margin=0.02)
    rig(SHOE_PARTS[s]['upper'], s, 0.0024)
    for k in ('sole', 'pad', 'laces', 'eyelets'):
        rig(SHOE_PARTS[s][k], s)
rig(GARMENTS['Jeans']['ob'], thickness=GARMENTS['Jeans']['thickness'])
rig(GARMENTS['TShirt']['ob'], thickness=GARMENTS['TShirt']['thickness'])
covered = build_mask()
check('rest', covered)
apply_pose(WALK)
check('walk', covered)
clear_pose()
assign_materials()
arm['walk_test_pose'] = json.dumps(WALK)
# the soles now carry the body: lift the whole rig so the shoes stand on z = 0
arm.location.z = -SOLE_BOTTOM
bpy.context.view_layer.update()
REPORT['lifted_by_m'] = round(-SOLE_BOTTOM, 4)
REPORT['objects'] = {o.name: len(o.data.vertices) for o in bpy.data.objects if o.type == 'MESH'}
blend = os.path.join(OUT, 'human_dressed.blend')
bpy.ops.file.pack_all()
bpy.context.preferences.filepaths.save_version = 0          # no .blend1 backups
bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
export_glb(os.path.join(OUT, 'human_dressed.glb'))
with open(os.path.join(OUT, 'human_dressed_report.json'), 'w') as f:
    json.dump(REPORT, f, indent=1)
log('saved', blend, 'and the .glb; report:', json.dumps({k: v for k, v in REPORT.items() if k.startswith('check')}))
