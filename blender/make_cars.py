"""The city's cars: three original, realistic models built in Blender (no brands).

    blender -b -P blender/make_cars.py -- <out_dir> [--only hatch,sedan,suv] [--renders DIR]

  hatch   a city hatchback      4.05 x 1.75 x 1.46 m, wheelbase 2.58, 195/55 R16
  sedan   a family sedan        4.65 x 1.80 x 1.45 m, wheelbase 2.70, 205/55 R16
  suv     a compact crossover   4.50 x 1.86 x 1.66 m, wheelbase 2.68, 225/60 R17

The body is a subdivision surface over a control cage: rings of 13 points
(half a cross-section, from the middle of the floor up the side to the middle
of the roof) at stations along the car, mirrored, closed at both ends. The
cage's rows are the body's lines: the sill, the character line and the
shoulder (sharp creases), the window's seals, the roof rail; its stations are
the nose, the wheel arches, the windscreen's foot, the pillars and the tail.
Each face of the cage belongs to a zone (hood, windscreen, a window, a
pillar...), so after subdivision every face knows whether it is paint, glass
or black trim, and the window outlines are the smooth curves of the cage's
lines. The wheel arches are cut out of the closed body with cylinders (their
inside is the wheel well), sized to the tyre with room for steering and for
the body's roll and pitch in the game.

Axes (Blender): x forward, y to the car's left, z up; the origin on the ground
at mid-length (the game's car origin).
"""
import bpy, bmesh, math, os, sys, json
import numpy as np
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

A = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUTDIR = os.path.abspath(A[0] if A else 'city/assets')
ONLY = A[A.index('--only') + 1].split(',') if '--only' in A else None
RENDERS = os.path.abspath(A[A.index('--renders') + 1]) if '--renders' in A else None
QUICK = '--quick' in A
REPORT = {}


def log(*a):
    print('[cars]', *a, flush=True)


def srgb(h):
    h = h.lstrip('#')
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


def ss(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- the cars' specifications
# Each station: x, the zone of the interval behind it (toward the next station),
# and the ring: W (half width at the character line), zb (underside), zs (sill
# top), zc (character line), Wsh/zsh (shoulder crease), and the top: a 'cabin'
# (Wgb, zgb glass foot; Wgt, zgt glass top; Wrr, zrr roof rail; zr roof middle)
# or a 'deck' (hood / boot lid: ze its edge height, zm its middle).
HATCH = dict(
    key='hatch', length=4.05, width=1.75, height=1.46, wheelbase=2.58, front_overhang=0.80, rear_overhang=0.67,
    track=1.52, tyre=(195, 55, 16), clearance=0.16, arch_gap=0.047, rim='double5', vmax=40, name_he="האצ'בק",
    seat=None,
)

SEDAN = dict(
    key='sedan', length=4.65, width=1.80, height=1.45, wheelbase=2.70, front_overhang=0.92, rear_overhang=1.03,
    track=1.56, tyre=(205, 55, 16), clearance=0.16, arch_gap=0.050, rim='ten', vmax=44, name_he='סדאן', seat=None,
)
SUV = dict(
    key='suv', length=4.50, width=1.86, height=1.66, wheelbase=2.68, front_overhang=0.92, rear_overhang=0.90,
    track=1.60, tyre=(225, 60, 17), clearance=0.20, arch_gap=0.055, rim='wide5', vmax=42, name_he='רכב פנאי', seat=None, cladding=True,
)
SPECS = {'hatch': HATCH, 'sedan': SEDAN, 'suv': SUV}
# for the game's physics: mass (kg), the centre of gravity's height, driven wheels, power, the gearbox
HATCH.update(mass=1200, cog_h=0.52, drive='fwd', power_kw=90, torque_nm=200, gears=[3.55, 2.05, 1.38, 1.03, 0.84, 0.70], final_drive=4.06)
SEDAN.update(mass=1400, cog_h=0.50, drive='fwd', power_kw=120, torque_nm=250, gears=[3.45, 2.00, 1.33, 1.00, 0.80, 0.66], final_drive=3.90)
SUV.update(mass=1650, cog_h=0.62, drive='awd', power_kw=135, torque_nm=280, gears=[3.70, 2.10, 1.40, 1.04, 0.84, 0.69], final_drive=4.10)
for _S in SPECS.values():
    _S['bump'] = round(_S['arch_gap'] - 0.024, 3)      # suspension travel: up (into the arch, checked) and down
    _S['droop'] = 0.06


def hatch_stations(S):
    """The hatchback's cage stations, front to back."""
    xf = S['length'] / 2
    # x, zone behind, W, zb, zs, zc, Wsh, zsh, top
    st = []
    def cab(Wgb, zgb, Wgt, zgt, Wrr, zrr, zr):
        return ('cabin', Wgb, zgb, Wgt, zgt, Wrr, zrr, zr)

    def deck(ze, zm):
        return ('deck', ze, zm)
    st.append((xf,         'nose',   0.44, 0.30, 0.36, 0.50, 0.40, 0.58, deck(0.60, 0.61)))
    st.append((xf - 0.06,  'nose',   0.70, 0.24, 0.32, 0.55, 0.66, 0.70, deck(0.72, 0.735)))
    st.append((xf - 0.19,  'hood',   0.82, 0.22, 0.31, 0.60, 0.78, 0.765, deck(0.785, 0.80)))
    st.append((1.62,       'hood',   0.870, 0.20, 0.30, 0.65, 0.835, 0.83, deck(0.86, 0.875)))
    st.append((1.225,      'hood',   0.875, 0.19, 0.30, 0.68, 0.840, 0.90, deck(0.915, 0.93)))
    st.append((0.98,       'screen', 0.872, 0.18, 0.30, 0.69, 0.832, 0.95, cab(0.790, 0.975, 0.788, 0.982, 0.735, 0.99, 0.975)))
    st.append((0.55,       'screen', 0.868, 0.16, 0.30, 0.70, 0.828, 0.965, cab(0.778, 0.990, 0.738, 1.15, 0.665, 1.19, 1.205)))
    st.append((0.12,       'window', 0.866, 0.16, 0.30, 0.71, 0.826, 0.975, cab(0.775, 1.000, 0.690, 1.345, 0.630, 1.395, 1.425)))
    st.append((-0.36,      'bpillar', 0.865, 0.16, 0.30, 0.725, 0.826, 0.99, cab(0.775, 1.012, 0.688, 1.37, 0.628, 1.415, 1.455)))
    st.append((-0.44,      'window', 0.865, 0.16, 0.30, 0.73, 0.827, 0.995, cab(0.776, 1.016, 0.688, 1.37, 0.628, 1.418, 1.458)))
    st.append((-1.05,      'window', 0.870, 0.16, 0.30, 0.755, 0.840, 1.015, cab(0.785, 1.040, 0.688, 1.335, 0.630, 1.398, 1.44)))
    st.append((-1.28,      'cpillar', 0.875, 0.18, 0.30, 0.77, 0.850, 1.025, cab(0.790, 1.075, 0.692, 1.29, 0.634, 1.375, 1.425)))
    st.append((-1.66,      'backlight', 0.868, 0.20, 0.31, 0.78, 0.842, 1.03, cab(0.778, 1.06, 0.676, 1.27, 0.616, 1.345, 1.385)))
    st.append((-1.94,      'tail',   0.82, 0.25, 0.33, 0.77, 0.79, 1.02, cab(0.725, 1.035, 0.700, 1.04, 0.660, 1.045, 1.05)))
    st.append((-xf + 0.05, 'tail',   0.76, 0.28, 0.35, 0.72, 0.72, 0.98, deck(0.985, 0.99)))
    st.append((-xf,        None,     0.54, 0.31, 0.37, 0.62, 0.50, 0.88, deck(0.89, 0.895)))
    return st


def sedan_stations(S):
    xf = S['length'] / 2
    def cab(*a):
        return ('cabin',) + a
    def deck(ze, zm):
        return ('deck', ze, zm)
    return [
        (xf,         'nose',   0.46, 0.30, 0.36, 0.50, 0.42, 0.58, deck(0.60, 0.61)),
        (xf - 0.06,  'nose',   0.72, 0.24, 0.32, 0.55, 0.68, 0.70, deck(0.72, 0.735)),
        (xf - 0.195, 'hood',   0.845, 0.22, 0.31, 0.60, 0.805, 0.755, deck(0.775, 0.79)),
        (1.80,       'hood',   0.892, 0.20, 0.30, 0.64, 0.857, 0.82, deck(0.84, 0.855)),
        (1.405,      'hood',   0.900, 0.19, 0.30, 0.67, 0.865, 0.88, deck(0.895, 0.91)),
        (1.06,       'screen', 0.897, 0.18, 0.30, 0.68, 0.858, 0.93, cab(0.810, 0.955, 0.808, 0.962, 0.755, 0.97, 0.955)),
        (0.62,       'screen', 0.894, 0.16, 0.30, 0.69, 0.854, 0.945, cab(0.800, 0.970, 0.760, 1.13, 0.690, 1.17, 1.185)),
        (0.20,       'window', 0.892, 0.16, 0.30, 0.70, 0.852, 0.955, cab(0.797, 0.980, 0.712, 1.35, 0.652, 1.408, 1.44)),
        (-0.33,      'bpillar', 0.892, 0.16, 0.30, 0.715, 0.852, 0.97, cab(0.797, 0.995, 0.708, 1.37, 0.648, 1.415, 1.45)),
        (-0.42,      'window', 0.892, 0.16, 0.30, 0.72, 0.853, 0.975, cab(0.798, 1.000, 0.708, 1.37, 0.648, 1.415, 1.45)),
        (-0.98,      'cpillar', 0.896, 0.16, 0.30, 0.74, 0.862, 0.995, cab(0.806, 1.030, 0.705, 1.325, 0.646, 1.39, 1.43)),
        (-1.18,      'backlight', 0.900, 0.18, 0.30, 0.75, 0.868, 1.005, cab(0.810, 1.050, 0.700, 1.29, 0.640, 1.36, 1.395)),
        (-1.70,      'deck',   0.892, 0.21, 0.31, 0.76, 0.857, 1.015, cab(0.790, 1.035, 0.775, 1.040, 0.745, 1.045, 1.05)),
        (-2.08,      'deck',   0.860, 0.24, 0.32, 0.75, 0.820, 1.000, deck(1.010, 1.020)),
        (-xf + 0.065, 'tail',  0.760, 0.28, 0.35, 0.72, 0.720, 0.960, deck(0.970, 0.975)),
        (-xf,        None,     0.540, 0.31, 0.37, 0.62, 0.500, 0.860, deck(0.870, 0.875)),
    ]


def suv_stations(S):
    xf = S['length'] / 2
    def cab(*a):
        return ('cabin',) + a
    def deck(ze, zm):
        return ('deck', ze, zm)
    return [
        (xf,         'nose',   0.52, 0.36, 0.42, 0.66, 0.48, 0.86, deck(0.88, 0.89)),
        (xf - 0.05,  'nose',   0.78, 0.31, 0.39, 0.70, 0.74, 0.95, deck(0.975, 0.99)),
        (xf - 0.19,  'hood',   0.885, 0.28, 0.37, 0.73, 0.845, 0.995, deck(1.02, 1.035)),
        (1.70,       'hood',   0.925, 0.25, 0.36, 0.75, 0.885, 1.04, deck(1.06, 1.075)),
        (1.33,       'hood',   0.930, 0.24, 0.36, 0.76, 0.890, 1.075, deck(1.09, 1.105)),
        (1.06,       'screen', 0.928, 0.215, 0.36, 0.77, 0.885, 1.095, cab(0.835, 1.120, 0.833, 1.127, 0.785, 1.135, 1.12)),
        (0.66,       'screen', 0.925, 0.20, 0.36, 0.775, 0.882, 1.105, cab(0.825, 1.130, 0.790, 1.31, 0.725, 1.35, 1.37)),
        (0.28,       'window', 0.923, 0.20, 0.36, 0.78, 0.880, 1.115, cab(0.822, 1.140, 0.755, 1.535, 0.700, 1.585, 1.615)),
        (-0.30,      'bpillar', 0.923, 0.20, 0.36, 0.79, 0.880, 1.13, cab(0.822, 1.155, 0.752, 1.55, 0.698, 1.60, 1.625)),
        (-0.39,      'window', 0.923, 0.20, 0.36, 0.795, 0.881, 1.135, cab(0.823, 1.160, 0.752, 1.55, 0.698, 1.60, 1.625)),
        (-1.05,      'bpillar', 0.926, 0.20, 0.36, 0.81, 0.885, 1.15, cab(0.828, 1.175, 0.752, 1.545, 0.698, 1.597, 1.622)),
        (-1.13,      'window', 0.928, 0.20, 0.36, 0.815, 0.887, 1.155, cab(0.830, 1.180, 0.752, 1.540, 0.698, 1.595, 1.62)),
        (-1.56,      'cpillar', 0.928, 0.215, 0.37, 0.83, 0.887, 1.17, cab(0.830, 1.200, 0.745, 1.50, 0.692, 1.575, 1.605)),
        (-1.88,      'backlight', 0.920, 0.25, 0.38, 0.835, 0.878, 1.175, cab(0.820, 1.200, 0.720, 1.47, 0.670, 1.545, 1.585)),
        (-2.13,      'tail',   0.870, 0.30, 0.40, 0.83, 0.830, 1.15, cab(0.770, 1.160, 0.750, 1.165, 0.710, 1.17, 1.175)),
        (-xf + 0.05, 'tail',   0.780, 0.33, 0.42, 0.80, 0.740, 1.10, deck(1.105, 1.11)),
        (-xf,        None,     0.560, 0.35, 0.44, 0.70, 0.520, 1.00, deck(1.005, 1.01)),
    ]


STATIONS = {'hatch': hatch_stations, 'sedan': sedan_stations, 'suv': suv_stations}

# the cage's rows (half section, bottom middle -> roof middle)
J_UNDER, J_SILL_IN, J_SILL, J_ROCK, J_CHAR, J_SHOULDER, J_SEAL_LO, J_GLASS_LO, J_GLASS_HI, J_RAIL = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
NJ = 13


def ring(W, zb, zs, zc, Wsh, zsh, top):
    """13 (y, z) points, from the middle of the underside to the middle of the top."""
    p = [(0.0, zb), (W - 0.10, zb), (W - 0.026, zb + 0.016), (W - 0.010, zs), (W, zc), (Wsh, zsh)]
    if top[0] == 'cabin':
        _, Wgb, zgb, Wgt, zgt, Wrr, zrr, zr = top
        crown = zr - zrr
        p += [(Wgb, zgb), (Wgb - 0.010, zgb + 0.018), (Wgt, zgt), (Wrr, zrr),
              (0.62 * Wrr, zrr + 0.72 * crown), (0.28 * Wrr, zrr + 0.95 * crown), (0.0, zr)]
    else:
        _, ze, zm = top
        Wd = Wsh - 0.02
        def hz(f):
            return ze + (zm - ze) * (1 - f * f)
        p += [(Wd, ze)] + [(f * Wd, hz(f)) for f in (0.93, 0.86, 0.79, 0.55, 0.26)] + [(0.0, zm)]
    assert len(p) == NJ
    return p


# which material each band of rows takes in each zone ('P' paint, 'G' glass, 'K' black trim)
#            under sill  sill rock  char  shoulder  seal_lo glass  seal_hi roof x3
BANDS = {
    'nose':      'K P P P P P P P P P P P'.split(),
    'hood':      'K P P P P P P P P P P P'.split(),
    'screen':    'K P P P P P K G P G G G'.split(),
    'window':    'K P P P P P K G K P P P'.split(),
    'bpillar':   'K P P P P P K K K P P P'.split(),
    'cpillar':   'K P P P P P P P P P P P'.split(),
    'backlight': 'K P P P P P K P P G G G'.split(),
    'tail':      'K P P P P P P P P P P P'.split(),
    'deck':      'K P P P P P P P P P P P'.split(),
}
CREASE_ROWS = {J_SILL: 0.6, J_CHAR: 0.9, J_SHOULDER: 1.0, J_SEAL_LO: 1.0, J_GLASS_LO: 1.0, J_GLASS_HI: 1.0, J_RAIL: 1.0}
ZONES = list(BANDS)


def build_cage(key):
    S = SPECS[key]
    st = STATIONS[key](S)
    bm = bmesh.new()
    rows = []                                    # per station: the loop's verts (left 0..12, right 11..1)
    for (x, zone, W, zb, zs, zc, Wsh, zsh, top) in st:
        half = ring(W, zb, zs, zc, Wsh, zsh, top)
        left = [bm.verts.new((x, y, z)) for (y, z) in half]
        right = [left[0]] + [bm.verts.new((x, -y, z)) for (y, z) in half[1:-1]] + [left[-1]]
        rows.append((left, right))
    mats = {'P': 0, 'G': 1, 'K': 2}
    crease_pairs = set()
    # each face remembers its band (row interval), zone and side through the subdivision
    # (the driver's door is cut out by them)
    L_band = bm.faces.layers.int.new('band'); L_zone = bm.faces.layers.int.new('zone'); L_side = bm.faces.layers.int.new('side')
    for i in range(len(st) - 1):
        zone = st[i][1]
        (la, ra), (lb, rb) = rows[i], rows[i + 1]
        for side, (A_, B_) in (('L', (la, lb)), ('R', (ra, rb))):
            for j in range(NJ - 1):
                quad = (A_[j], A_[j + 1], B_[j + 1], B_[j])
                if side == 'R':
                    quad = quad[::-1]
                if len(set(quad)) < 4:
                    continue
                f = bm.faces.new(quad)
                band = BANDS[zone][j]
                if S.get('cladding') and 1 <= j <= 2:       # black lower cladding along the sills
                    band = 'K'
                f.material_index = mats[band]
                f[L_band] = j; f[L_zone] = ZONES.index(zone) + 1; f[L_side] = 1 if side == 'L' else 2
            for j, w in CREASE_ROWS.items():
                crease_pairs.add((A_[j], B_[j], w))
    # the ends: a ladder of quads across from left to right
    for (left, right), sign in ((rows[0], 1), (rows[-1], -1)):
        for j in range(1, NJ - 2):
            quad = (left[j], left[j + 1], right[j + 1], right[j])
            f = bm.faces.new(quad if sign > 0 else quad[::-1])
            f.material_index = 2 if j == 0 else 0
        f = bm.faces.new((left[0], left[1], right[1]) if sign > 0 else (right[1], left[1], left[0]))
        f.material_index = 2
        f = bm.faces.new((left[NJ - 2], left[NJ - 1], right[NJ - 2]) if sign > 0 else (right[NJ - 2], left[NJ - 1], left[NJ - 2]))
        f.material_index = 0
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(key + '_cage')
    bm.to_mesh(me)
    edge_of = {}
    for e in me.edges:
        edge_of[frozenset(e.vertices)] = e.index
    cr = me.attributes.new('crease_edge', 'FLOAT', 'EDGE')
    vi = {v: v.index for v in bm.verts}
    for a, b, w in crease_pairs:
        k = frozenset((vi[a], vi[b]))
        if k in edge_of:
            cr.data[edge_of[k]].value = w
    bm.free()
    ob = bpy.data.objects.new(key + '_body', me)
    bpy.context.scene.collection.objects.link(ob)
    return ob, st


def subdivide(ob, levels):
    md = ob.modifiers.new('Subd', 'SUBSURF')
    md.levels = md.render_levels = levels
    md.use_creases = True
    md.quality = 4
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.modifier_apply(modifier='Subd')
    for p in ob.data.polygons:
        p.use_smooth = True


def cut_arches(ob, S):
    """Wheel arches: cylinders (axis y) around each axle, from outside the body to
    the wheel well's inner wall, subtracted from the closed body."""
    R = tyre_radius(S)
    gap = S['arch_gap']
    xf, xr = axles(S)
    # the smooth body before the cut lends its normals to the cut body (the
    # cut's new vertices would otherwise shade the panels around it in steps)
    src = ob.copy(); src.data = ob.data.copy(); bpy.context.scene.collection.objects.link(src)
    cutters = []
    for x in (xf, xr):
        bm = bmesh.new()
        ret = bmesh.ops.create_cone(bm, cap_ends=True, segments=ARCH_SEGS, radius1=R + gap, radius2=R + gap, depth=2.0)
        bmesh.ops.rotate(bm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'X'))
        me = bpy.data.meshes.new('arch')
        bm.to_mesh(me); bm.free()
        c = bpy.data.objects.new('arch', me)
        bpy.context.scene.collection.objects.link(c)
        c.location = (x, 0, R)
        # the well: only the outer 0.42 m of each side is cut (two cutters, mirrored)
        cutters.append(c)
    # a box keeps the middle of the car: subtract (cylinder minus middle box)
    for c in cutters:
        keep = bpy.data.objects.new('keep', bpy.data.meshes.new('keep'))
        bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(keep.data); bm.free()
        bpy.context.scene.collection.objects.link(keep)
        Wi = well_inner(S)
        keep.scale = (3.0, 2 * Wi, 3.0); keep.location = c.location
        md = c.modifiers.new('Hollow', 'BOOLEAN'); md.operation = 'DIFFERENCE'; md.object = keep; md.solver = 'EXACT'
        with bpy.context.temp_override(object=c, active_object=c):
            bpy.ops.object.modifier_apply(modifier='Hollow')
        bpy.data.objects.remove(keep)
        well = render_material('Well')
        c.data.materials.clear(); c.data.materials.append(well)
        md = ob.modifiers.new('Arch', 'BOOLEAN'); md.operation = 'DIFFERENCE'; md.object = c; md.solver = 'EXACT'
        md.material_mode = 'TRANSFER'
        with bpy.context.temp_override(object=ob, active_object=ob):
            bpy.ops.object.modifier_apply(modifier='Arch')
        bpy.data.objects.remove(c)
    bm = bmesh.new(); bm.from_mesh(ob.data)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4])
    bm.to_mesh(ob.data); bm.free()
    transfer_normals(ob, src)
    return src


def transfer_normals(ob, src):
    """The panels take their normals from the smooth body (src): the cuts' new vertices
    (the arches, the door) would otherwise shade the panels around them in steps."""
    well = ob.data.materials.find('R_Well')
    me = ob.data
    # (the wells' rims are sharp: the panels' normals and the wells' stay apart)
    fm = np.zeros(len(me.polygons), np.int32); me.polygons.foreach_get('material_index', fm)
    ek = {}
    for p in me.polygons:
        w = fm[p.index] == well
        for e in p.edge_keys:
            ek.setdefault(e, set()).add(w)
    sharp = me.attributes.get('sharp_edge') or me.attributes.new('sharp_edge', 'BOOLEAN', 'EDGE')
    vals = np.zeros(len(me.edges), bool); sharp.data.foreach_get('value', vals)
    for e in me.edges:
        if len(ek.get(e.key, ())) == 2:
            vals[e.index] = True
    sharp.data.foreach_set('value', vals)
    for p in me.polygons:
        p.use_smooth = True
    # every panel corner takes the smooth body's normal, the arch's rim too (its corners on the panel side);
    # then the wells' own corners go back to their faces' normals
    md = ob.modifiers.new('Normals', 'DATA_TRANSFER'); md.object = src
    md.use_loop_data = True; md.data_types_loops = {'CUSTOM_NORMAL'}; md.loop_mapping = 'POLYINTERP_NEAREST'
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.modifier_apply(modifier='Normals')
    me = ob.data
    cn = [n.vector.copy() for n in me.corner_normals]
    for p in me.polygons:
        if p.material_index == well:
            for li in range(p.loop_start, p.loop_start + p.loop_total):
                cn[li] = p.normal.copy()
    me.normals_split_custom_set(cn)


ARCH_SEGS = 64
STEER = 0.58                         # the game's full lock (radians)


def well_inner(S):
    """The wheel well's inner wall (|y|): the inner shoulder of a tyre at full lock clears it by 3 cm."""
    R = tyre_radius(S); w = S['tyre'][0] / 1000
    return S['track'] / 2 - (R * math.sin(STEER) + w / 2 * math.cos(STEER)) - 0.03


def tyre_radius(S):
    w, ar, rim = S['tyre']
    return (rim * 25.4 / 2 + w * ar / 100) / 1000


def axles(S):
    xf = S['length'] / 2 - S['front_overhang']
    return xf, xf - S['wheelbase']


# ---------------------------------------------------------------- details on the body's surface
# Materials of the details (index -> name); the render gives each its look, the
# game export maps them to its atlas texels and lamp functions.
DMATS = ['Paint', 'Glass', 'Trim', 'GlossBlack', 'Chrome', 'Lens', 'Reflector', 'DRL', 'Tail', 'Brake', 'IndL', 'IndR',
         'Reverse', 'Plate', 'Rubber', 'Interior', 'Seat', 'Dash', 'Screen', 'Carpet', 'Headliner', 'Well', 'Rim', 'DarkMetal',
         'Caliper', 'Rim2', 'MirrorGlass', 'Grille']
DM = {n: i for i, n in enumerate(DMATS)}


class Surface:
    """Ray casts onto the body (the smooth body, before the arches are cut)."""

    def __init__(self, ob):
        me = ob.data
        self.bvh = BVHTree.FromPolygons([v.co.copy() for v in me.vertices], [tuple(p.vertices) for p in me.polygons])

    def hit(self, origin, direction, far=4.0):
        loc, nrm, fi, d = self.bvh.ray_cast(Vector(origin), Vector(direction).normalized(), far)
        return (loc, nrm) if loc is not None else (None, None)


def inside_poly(pts, u, v):
    c = False
    n = len(pts)
    for i in range(n):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
        if (y1 > v) != (y2 > v) and u < (x2 - x1) * (v - y1) / (y2 - y1 + 1e-12) + x1:
            c = not c
    return c


def cpatch(bm, surf, O, U, V, D, u0, u1, lo, hi, nu, nv, mat, off, skirt=0.004, mirror=False):
    """A patch shaped to an outline: columns across u0..u1 (closer at the ends),
    each from lo(u) to hi(u) with its rows evenly between, so the patch's edge
    is the outline itself (no steps). mat(u, t, v) chooses each cell's material
    (t: 0 at the bottom edge, 1 at the top: bands that follow the outline).
    Projected along D onto the body and lifted `off`, with a skirt like patch()."""
    O, U, V, D = Vector(O), Vector(U), Vector(V), Vector(D).normalized()
    nu = max(3, int(round(nu * LOW['patch']))); nv = max(2, int(round(nv * LOW['patch'])))
    us = [u0 + (u1 - u0) * (1 - math.cos(math.pi * i / nu)) / 2 for i in range(nu + 1)]
    cols = []
    for u in us:
        uu = min(max(u, u0 + 1e-6), u1 - 1e-6)
        a, b = lo(uu), hi(uu)
        if a is None or b is None or b < a:
            a = b = (a if a is not None else b) or 0.0
        cols.append([a + (b - a) * j / nv for j in range(nv + 1)])
    hits = {}
    for i, u in enumerate(us):
        for j, v in enumerate(cols[i]):
            P_ = O + U * u + V * v
            loc, nrm = surf.hit(P_ - D * 2.0, D)
            hits[(i, j)] = None if loc is None else (loc, nrm)
    kind = bm.verts.layers.int.get('kind') or bm.verts.layers.int.new('kind')
    for side in ((1, -1) if mirror else (1,)):
        flip = lambda p: Vector((p.x, side * p.y, p.z))
        vert = {}
        for c, h in hits.items():
            if h is None:
                continue
            key = (c[0], 0 if cols[c[0]][-1] - cols[c[0]][0] < 1e-6 else c[1])      # (a column of one point)
            if key not in vert:
                vert[key] = bm.verts.new(flip(h[0] + h[1] * off)); vert[key][kind] = 1
            vert[c] = vert[key]
        edge = {}
        for i in range(nu):
            for j in range(nv):
                q = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
                if any(c not in vert for c in q):
                    continue
                vs = []
                for c in q:
                    if vert[c] not in vs:
                        vs.append(vert[c])
                if len(vs) < 3:
                    continue
                f = bm.faces.new(vs if side > 0 else vs[::-1])
                um = (us[i] + us[i + 1]) / 2; tm = (j + 0.5) / nv
                f.material_index = mat(um, tm, (cols[i][j] + cols[i + 1][j + 1]) / 2)
                for k in range(len(vs)):
                    e = frozenset((vs[k], vs[(k + 1) % len(vs)]))
                    edge[e] = edge.get(e, 0) + 1
        if skirt:
            low = {}
            for e, n_ in edge.items():
                if n_ != 1:
                    continue
                a_, b_ = tuple(e)
                for v_ in (a_, b_):
                    if v_ not in low:
                        low[v_] = bm.verts.new(v_.co - surf_normal(surf, v_.co) * (off + skirt))
                g = bm.faces.new((a_, b_, low[b_], low[a_]))
                g.material_index = DM['Trim']
    return True


def surf_normal(surf, co):
    """The body's normal nearest to co."""
    loc, nrm, i_, d_ = surf.bvh.find_nearest(co)
    return nrm if nrm is not None else Vector((0, 0, 1))


def patch(bm, surf, O, U, V, D, box2, nu, nv, keep, mat, off, skirt=0.004, mirror=False):
    """A patch of the body's surface: a grid over box2 = (u0, u1, v0, v1) in the
    plane O + u U + v V, projected along D onto the body and lifted `off` along
    its normal; keep(u, v) chooses the cells, mat(u, v) their materials. A skirt
    from the patch's edge down into the body closes the gap at grazing angles.
    mirror: also the patch mirrored to the car's right side (y -> -y)."""
    O, U, V, D = Vector(O), Vector(U), Vector(V), Vector(D).normalized()
    nu = max(2, int(round(nu * LOW['patch']))); nv = max(2, int(round(nv * LOW['patch'])))
    u0, u1, v0, v1 = box2
    us = [u0 + (u1 - u0) * i / nu for i in range(nu + 1)]
    vs = [v0 + (v1 - v0) * j / nv for j in range(nv + 1)]
    cells = [(i, j) for i in range(nu) for j in range(nv)
             if keep((us[i] + us[i + 1]) / 2, (vs[j] + vs[j + 1]) / 2)]
    if not cells:
        return 0
    corners = {}
    for i, j in cells:
        for c in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
            if c in corners:
                continue
            P = O + U * us[c[0]] + V * vs[c[1]]
            loc, nrm = surf.hit(P - D * 2.0, D)
            corners[c] = None if loc is None else (loc, nrm)
    made = 0
    for side in ((1, -1) if mirror else (1,)):
        flip = lambda p: Vector((p.x, side * p.y, p.z))
        vert = {}
        kind = bm.verts.layers.int.get('kind') or bm.verts.layers.int.new('kind')
        for c, h in corners.items():
            if h is not None:
                vert[c] = bm.verts.new(flip(h[0] + h[1] * off)); vert[c][kind] = 1
        edge_count = {}
        for i, j in cells:
            q = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            if any(vert.get(c) is None for c in q):
                continue
            vv = [vert[c] for c in q]
            f = bm.faces.new(vv if side > 0 else vv[::-1])
            f.material_index = mat((us[i] + us[i + 1]) / 2, (vs[j] + vs[j + 1]) / 2)
            made += 1
            for k in range(4):
                e = tuple(sorted((q[k], q[(k + 1) % 4])))
                edge_count[e] = edge_count.get(e, 0) + 1
        if skirt:
            low = {}
            for e, n_ in edge_count.items():
                if n_ != 1:
                    continue
                for c in e:
                    if c not in low:
                        loc, nrm = corners[c]
                        low[c] = bm.verts.new(flip(loc - nrm * skirt))
                a, b = e
                f = bm.faces.new((vert[a], vert[b], low[b], low[a]))
                f.material_index = DM['Trim']
    return made


def strip(bm, surf, pts, D, width, off, mi, mirror=False, n_per_m=80, force=False):
    """A narrow band along a polyline (body coordinates, projected along D onto
    the body): a panel gap, a seal, a chrome line."""
    if LOW['strips_off'] and not force:
        return
    if LOW['strips_off']:
        n_per_m = 30
    P = [Vector(p) for p in pts]
    dense = [P[0]]
    for a, b in zip(P[:-1], P[1:]):
        n = max(1, int((b - a).length * n_per_m))
        dense += [a.lerp(b, (k + 1) / n) for k in range(n)]
    D = Vector(D).normalized()
    hits = []
    for p in dense:
        loc, nrm = surf.hit(p - D * 2.0, D)
        if loc is not None:
            hits.append((loc, nrm))
    if len(hits) < 2:
        return
    for side in ((1, -1) if mirror else (1,)):
        flip = lambda p: Vector((p.x, side * p.y, p.z))
        prev = None
        for k, (loc, nrm) in enumerate(hits):
            t = (hits[min(k + 1, len(hits) - 1)][0] - hits[max(k - 1, 0)][0])
            if t.length < 1e-9:
                continue
            across = nrm.cross(t).normalized() * (width / 2)
            a = bm.verts.new(flip(loc + nrm * off + across)); b = bm.verts.new(flip(loc + nrm * off - across))
            if prev:
                f = bm.faces.new((prev[0], prev[1], b, a) if side > 0 else (a, b, prev[1], prev[0]))
                f.material_index = mi
            prev = (a, b)


LOW = dict(patch=1.0, blob=1.0, strips_off=False)     # the far model's resolution (set while it is built)


def tube(bm, pts, hw, hh, mi, segs=12, e=4.0):
    """A bar swept along pts: a rounded rectangle across it (hw wide, hh high), its ends closed."""
    if len(pts) < 2:
        return
    def se(a, ex):
        return math.copysign(abs(a) ** (2.0 / ex), a)
    rings = []
    for k, p in enumerate(pts):
        t = (pts[min(k + 1, len(pts) - 1)] - pts[max(k - 1, 0)]).normalized()
        side = t.cross(Vector((0, 0, 1))).normalized(); up = side.cross(t).normalized()
        rings.append([bm.verts.new(p + side * (hw * se(math.cos(2 * math.pi * i / segs), e)) + up * (hh * se(math.sin(2 * math.pi * i / segs), e)))
                      for i in range(segs)])
    for a, b in zip(rings[:-1], rings[1:]):
        for i in range(segs):
            bm.faces.new((a[i], b[i], b[(i + 1) % segs], a[(i + 1) % segs])).material_index = mi
    bm.faces.new(rings[0][::-1]).material_index = mi
    bm.faces.new(rings[-1]).material_index = mi


def blob(bm, center, half, e=4.0, nu=20, nv=12, mi=0, rot=None):
    """A superellipsoid (exponent e: 2 a ball, higher a box with rounded edges),
    half sizes `half`, rotated by the 3x3 `rot`, at `center`."""
    hx, hy, hz = half
    nu = max(6, int(round(nu * LOW['blob']))); nv = max(4, int(round(nv * LOW['blob'])))
    def se(a, ex):
        return math.copysign(abs(a) ** (2.0 / ex), a)
    rows = []
    for j in range(nv + 1):
        b = -math.pi / 2 + math.pi * j / nv
        row = []
        for i in range(nu):
            a = 2 * math.pi * i / nu
            p = Vector((hx * se(math.cos(b), e) * se(math.cos(a), e), hy * se(math.cos(b), e) * se(math.sin(a), e),
                        hz * se(math.sin(b), e)))
            if rot is not None:
                p = rot @ p
            row.append(p + Vector(center))
        rows.append(row)
    vs = [[bm.verts.new(p) for p in row] for row in rows]
    for j in range(nv):
        for i in range(nu):
            a, b_, c, d = vs[j][i], vs[j][(i + 1) % nu], vs[j + 1][(i + 1) % nu], vs[j + 1][i]
            f = bm.faces.new((a, b_, c, d))
            f.material_index = mi
    bmesh.ops.remove_doubles(bm, verts=[v for row in vs for v in row], dist=1e-6)
    return vs


RENDER_LOOK = {   # name: (colour, roughness, metallic, emission colour, emission strength)
    'Paint': ('#9a1b1f', 0.3, 0.55, None, 0), 'Glass': ('#56656c', 0.02, 0.0, None, 0), 'Trim': ('#0e0e10', 0.55, 0.0, None, 0),
    'GlossBlack': ('#060708', 0.12, 0.0, None, 0), 'Chrome': ('#e4e7ec', 0.06, 1.0, None, 0),
    'Lens': ('#4a5058', 0.05, 0.85, None, 0), 'Reflector': ('#aeb6c0', 0.12, 1.0, None, 0),
    'DRL': ('#f4f7ff', 0.1, 0.0, '#eef3ff', 4.0), 'Tail': ('#6e0806', 0.08, 0.0, '#ff1a0a', 0.35),
    'Brake': ('#a3120c', 0.08, 0.0, '#ff2010', 1.2), 'IndL': ('#d8771b', 0.1, 0.0, None, 0), 'IndR': ('#d8771b', 0.1, 0.0, None, 0),
    'Reverse': ('#e3e6ea', 0.05, 0.2, None, 0), 'Plate': ('#f2c200', 0.4, 0.0, None, 0), 'Rubber': ('#141415', 0.88, 0.0, None, 0),
    'Interior': ('#2a2b2e', 0.7, 0.0, None, 0), 'Seat': ('#3a3b40', 0.85, 0.0, None, 0), 'Dash': ('#1c1d20', 0.6, 0.0, None, 0),
    'Screen': ('#050608', 0.08, 0.0, None, 0), 'Carpet': ('#1a1a1c', 0.95, 0.0, None, 0), 'Headliner': ('#8c8a86', 0.9, 0.0, None, 0),
    'Well': ('#101012', 0.85, 0.0, None, 0), 'Rim': ('#b9bdc3', 0.32, 0.9, None, 0), 'DarkMetal': ('#34363a', 0.4, 0.8, None, 0),
    'Caliper': ('#2a2b2e', 0.5, 0.3, None, 0), 'Rim2': ('#2b2d31', 0.3, 0.6, None, 0), 'MirrorGlass': ('#a9b3bd', 0.02, 1.0, None, 0),
    'Grille': ('#0b0c0d', 0.3, 0.0, None, 0),
}


def render_material(name):
    """The studio's material for a part (named 'R_<part>'; the game's materials have the plain names)."""
    m = bpy.data.materials.get('R_' + name)
    if m is not None:
        return m
    col, r, mt, ec, es = RENDER_LOOK[name]
    m = new_principled('R_' + name, col, r, mt, coat=1.0 if name in ('Paint', 'GlossBlack') else 0.0, emit=ec,
                       transmission=1.0 if name == 'Glass' else 0.0)
    if ec:
        m.node_tree.nodes['Principled BSDF'].inputs['Emission Strength'].default_value = es
    return m


def obj_from_bm(bm, name, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = smooth
    ob = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(ob)
    for n in DMATS:
        ob.data.materials.append(render_material(n))
    return ob


# ---------------------------------------------------------------- wheels
def revolve(bm, prof, segs, mi, x=0.0, z=0.0, side=1):
    """A profile [(r, y)] spun around the y axis (the axle); y toward the wheel's outside is +side."""
    rings = []
    for k in range(segs):
        th = 2 * math.pi * k / segs
        rings.append([bm.verts.new((x + r * math.sin(th), side * y, z + r * math.cos(th))) for (r, y) in prof])
    for k in range(segs):
        a, b = rings[k], rings[(k + 1) % segs]
        for j in range(len(prof) - 1):
            f = bm.faces.new((a[j], a[j + 1], b[j + 1], b[j]) if side > 0 else (b[j], b[j + 1], a[j + 1], a[j]))
            f.material_index = mi(j) if callable(mi) else mi
    return rings


def wheel(S, segs=72, lo=False):
    """One left wheel, centred on its axle (y toward the outside): tyre, rim, nuts,
    centre cap, brake disc and caliper. Materials: 0 rubber, 1 rim, 2 dark metal
    (disc, nuts' recesses), 3 caliper, 4 rim's second tone."""
    w_mm, ar, rim_in = S['tyre']
    W = w_mm / 1000; R = tyre_radius(S); rr = rim_in * 25.4 / 2000
    h = R - rr
    bm = bmesh.new()
    # tyre: bead -> sidewall (bulging) -> shoulder -> tread with three grooves -> mirror
    hw = W / 2
    side = [(rr + 0.004, hw * 0.80), (rr + 0.25 * h, hw * 0.97), (rr + 0.55 * h, hw * 1.03), (rr + 0.80 * h, hw * 1.0),
            (R - 0.018, hw * 0.93)]
    shoulder = [(R - 0.006, hw * 0.84), (R, hw * 0.74)]
    tread = []
    for gy in (0.42, 0.0):                       # grooves (outer half): at 0.42 hw and in the middle
        tread += [(R, hw * (gy + 0.09)), (R - 0.007, hw * (gy + 0.06)), (R - 0.007, hw * (gy - 0.06)), (R, hw * (gy - 0.09))]
    half = side + shoulder + tread[:4] + [(R, hw * 0.12)]
    if lo:
        half = [side[0], side[2], side[4], shoulder[1], (R, 0.0)]
    prof = half + [(r, -y) for (r, y) in reversed(half[:-1])]
    revolve(bm, prof, segs, 0)
    # rim: barrel (inside), the outer lip, a dished face of spokes
    lip_y = hw * 0.80
    barrel = [(rr - 0.012, -hw * 0.82), (rr - 0.006, -hw * 0.80), (rr - 0.006, lip_y - 0.012), (rr + 0.010, lip_y - 0.004),
              (rr + 0.010, lip_y + 0.004), (rr - 0.010, lip_y + 0.006), (rr - 0.030, lip_y - 0.004)]
    if lo:
        barrel = [barrel[0], barrel[3], barrel[5], barrel[6]]
    revolve(bm, barrel, segs, 1)
    face_y = lip_y - 0.012
    design = S['rim']
    spokes = {'double5': 10, 'ten': 10, 'wide5': 5}[design]
    for k in range(spokes):
        a = 2 * math.pi * k / spokes
        if design == 'double5':                  # five pairs
            a = 2 * math.pi * (k // 2) / 5 + (0.14 if k % 2 else -0.14)
        width_hub, width_rim = {'double5': (0.026, 0.040), 'ten': (0.020, 0.034), 'wide5': (0.046, 0.088)}[design]
        spoke(bm, a, 0.066, rr - 0.024, width_hub, width_rim, face_y, dish=0.028, thick=0.030, mi=1)
    # hub (flat), five nuts in it, a plain centre cap
    if lo:
        disc(bm, 0.074, face_y - 0.034, face_y - 0.024, 10, 1)
        disc(bm, rr * 0.80, -0.020, 0.004, 12, 2)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        return bm
    disc(bm, 0.074, face_y - 0.034, face_y - 0.024, 40, 1)
    for k in range(5):
        a = 2 * math.pi * k / 5 + math.pi / 5
        disc(bm, 0.0105, face_y - 0.030, face_y - 0.018, 6, 2, cx=0.050 * math.sin(a), cz=0.050 * math.cos(a))
    disc(bm, 0.028, face_y - 0.030, face_y - 0.020, 24, 4)
    # brake: disc and caliper behind the spokes
    disc(bm, rr * 0.80, -0.020, 0.004, 48, 2)
    box(bm, (-0.035, -0.042, rr * 0.48), (0.035, 0.002, rr * 0.82), 3, rot=-0.6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def spoke(bm, a, r0, r1, w0, w1, y_face, dish, thick, mi):
    """A tapered spoke from the hub (r0) to the rim (r1) at angle a, dished
    inward toward the hub, thick along the axle."""
    ca, sa = math.cos(a), math.sin(a)
    vs = []
    for (r, w, dy) in ((r0, w0, -dish), (r1, w1, 0.0)):
        for s_ in (-1, 1):
            for t in (0.0, -thick):
                u = s_ * w / 2
                x = u * ca + r * sa
                z = -u * sa + r * ca
                vs.append(bm.verts.new((x, y_face + dy + t, z)))
    # vs: hub(-,front),(-,back),(+,front),(+,back), rim(...)
    q = lambda *i: bm.faces.new([vs[k] for k in i])
    for f in (q(0, 2, 6, 4), q(1, 5, 7, 3), q(0, 4, 5, 1), q(2, 3, 7, 6), q(0, 1, 3, 2), q(4, 6, 7, 5)):
        f.material_index = mi


def disc(bm, r, y0, y1, segs, mi, cx=0.0, cz=0.0):
    ret = bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r, radius2=r, depth=y1 - y0)
    bmesh.ops.rotate(bm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(-math.pi / 2, 3, 'X'))
    bmesh.ops.translate(bm, verts=ret['verts'], vec=(cx, (y0 + y1) / 2, cz))
    for f in {f for v in ret['verts'] for f in v.link_faces}:
        f.material_index = mi


def box(bm, lo, hi, mi, rot=0.0):
    ret = bmesh.ops.create_cube(bm, size=1.0)
    c = [(l + h_) / 2 for l, h_ in zip(lo, hi)]; s = [h_ - l for l, h_ in zip(lo, hi)]
    bmesh.ops.scale(bm, verts=ret['verts'], vec=s)
    bmesh.ops.translate(bm, verts=ret['verts'], vec=c)
    if rot:
        bmesh.ops.rotate(bm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(rot, 3, 'Y'))
    for f in {f for v in ret['verts'] for f in v.link_faces}:
        f.material_index = mi


def wheel_object(S, name):
    bm = wheel(S, segs=48)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = True
    ob = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(ob)
    for nm in ('Rubber', 'Rim', 'DarkMetal', 'Caliper', 'Rim2'):
        ob.data.materials.append(render_material(nm))
    return ob


def place_wheels(S, name):
    """Four wheels for the studio (the game mirrors the one left wheel itself)."""
    xf, xr = axles(S); R = tyre_radius(S); t = S['track'] / 2
    src = wheel_object(S, name + '_wheel')
    obs = [src]
    for i, (x, sy) in enumerate(((xf, 1), (xr, 1), (xf, -1), (xr, -1))):
        o = src if i == 0 else src.copy()
        if i:
            bpy.context.scene.collection.objects.link(o)
        o.location = (x, sy * t, R); o.scale = (1, sy, 1)
        obs.append(o)
    return src


# ---------------------------------------------------------------- materials (render quality)
def new_principled(name, hexcol, rough=0.5, metal=0.0, coat=0.0, alpha=1.0, transmission=0.0, emit=None):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = srgb(hexcol) + (1,)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metal
    if coat:
        b.inputs['Coat Weight'].default_value = coat; b.inputs['Coat Roughness'].default_value = 0.03
    if transmission:
        b.inputs['Transmission Weight'].default_value = transmission
    if alpha < 1:
        b.inputs['Alpha'].default_value = alpha
    if emit:
        b.inputs['Emission Color'].default_value = srgb(emit) + (1,); b.inputs['Emission Strength'].default_value = 2.0
    return m


def body_materials(ob, paint_hex):
    idx = [p.material_index for p in ob.data.polygons]          # (clearing the slots resets the faces' indices)
    ob.data.materials.clear()
    for n in ('Paint', 'Glass', 'Trim'):
        ob.data.materials.append(render_material(n))
    ob.data.polygons.foreach_set('material_index', idx)


# ---------------------------------------------------------------- studio renders
def studio():
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'; sc.cycles.device = 'CPU'
    sc.cycles.samples = 24 if QUICK else 64; sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = (800, 450) if QUICK else (1280, 720)
    sc.render.image_settings.file_format = 'JPEG'
    w = bpy.data.worlds.new('Sky'); sc.world = w; w.use_nodes = True
    nt = w.node_tree; nt.nodes.clear()
    sky = nt.nodes.new('ShaderNodeTexSky')
    try:
        sky.sky_type = 'MULTIPLE_SCATTERING'
    except Exception:
        sky.sky_type = 'NISHITA'
    sky.sun_elevation = math.radians(35); sky.sun_rotation = math.radians(140)
    bg = nt.nodes.new('ShaderNodeBackground'); bg.inputs['Strength'].default_value = 0.22
    sc.view_settings.view_transform = 'AgX'; sc.view_settings.exposure = -0.35
    try:
        sc.view_settings.look = 'AgX - Medium High Contrast'
    except Exception:
        pass
    out = nt.nodes.new('ShaderNodeOutputWorld')
    nt.links.new(sky.outputs['Color'], bg.inputs['Color']); nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    sun = bpy.data.objects.new('Sun', bpy.data.lights.new('Sun', 'SUN')); sun.data.energy = 3.5; sun.data.angle = math.radians(1.5)
    sun.rotation_euler = (math.radians(50), 0, math.radians(140)); sc.collection.objects.link(sun)
    bm = bmesh.new(); bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=40)
    me = bpy.data.meshes.new('ground'); bm.to_mesh(me); bm.free()
    g = bpy.data.objects.new('ground', me); sc.collection.objects.link(g)
    g.data.materials.append(new_principled('Ground', '#6d6e70', 0.85))
    cam = bpy.data.objects.new('Cam', bpy.data.cameras.new('Cam')); sc.collection.objects.link(cam); sc.camera = cam
    return cam


def shoot(cam, name, loc, target, lens=50):
    sc = bpy.context.scene
    cam.location = loc; cam.data.lens = lens
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    sc.render.filepath = os.path.join(RENDERS, name + '.jpg')
    bpy.ops.render.render(write_still=True)


VIEWS_EXTRA = [tuple(json.loads(v)) for v in os.environ.get('CARS_VIEWS', '').split(';') if v]   # (more views: name, eye, target, lens)
VIEWS = [('side', (0, 9.0, 0.75), (0, 0, 0.72), 50), ('front34', (6.2, 4.6, 1.5), (0, 0, 0.62), 45),
         ('rear34', (-6.0, 4.6, 1.6), (0, 0, 0.62), 45), ('front', (8.5, 0, 0.9), (0, 0, 0.7), 50),
         ('rear', (-8.5, 0, 1.0), (0, 0, 0.7), 50), ('top', (0.01, 0, 11), (0, 0, 0), 40)]


def trap(u, z0, z1, h0, h1):
    """A trapezoid (half width h0 at z0, h1 at z1): its (bottom, top) at u."""
    a = abs(u)
    if a <= min(h0, h1):
        return z0, z1
    t = min(max((a - h0) / (h1 - h0), 0.0), 1.0)
    v = z0 + t * (z1 - z0)
    return (v, z1) if h1 > h0 else (z0, v)


def poly_edge(pts, u, top=True):
    """The outline's upper (or lower) edge height at u (outline drawn left to right on top, back on the bottom)."""
    best = None
    n = len(pts)
    for i in range(n):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
        if (x1 - u) * (x2 - u) <= 0 and x1 != x2:
            y = y1 + (y2 - y1) * (u - x1) / (x2 - x1)
            if best is None or (y > best if top else y < best):
                best = y
    return best


def unit(*v):
    return Vector(v).normalized()


# where each car's details go (body coordinates, x forward, y left, z up)
DETAILS = {
    'hatch': dict(
        # headlight (left): projected from front-left; outline in (u along U, v up)
        head=dict(T=(2.00, 0.33, 0.705), D=(-1, -0.5, 0), outline=[(0.0, -0.022), (0.14, -0.040), (0.32, -0.050), (0.46, -0.046),
              (0.56, -0.018), (0.60, 0.022), (0.55, 0.048), (0.38, 0.052), (0.18, 0.044), (0.04, 0.030), (0.0, 0.016)],
              projector=(0.20, -0.006, 0.028), ind_u=0.50),
        grille_top=dict(z=(0.635, 0.690), half=(0.30, 0.25)),            # the slim upper grille between the lamps
        grille=dict(z=(0.33, 0.52), half_top=0.42, half_bottom=0.50),     # the main lower grille
        fog=dict(y=0.64, z=0.37, r=0.042),
        lip=dict(z=(0.235, 0.29), half=0.66),
        plate_front=dict(z=0.435),
        tail=dict(T=(-1.995, 0.36, 0.93), D=(1, -0.55, 0), outline=[(0.0, -0.05), (0.20, -0.055), (0.38, -0.045), (0.48, -0.015),
              (0.50, 0.035), (0.40, 0.055), (0.20, 0.060), (0.0, 0.045)], bar=0.012, rev_u=0.09, ind_u=(0.09, 0.19)),
        plate_rear=dict(z=0.62, x_probe=-2.2),
        diffuser=dict(z=(0.29, 0.39), half=0.62),
        doors=[[(0.98, 0.975), (0.975, 0.75), (0.955, 0.62), (0.92, 0.52), (0.88, 0.44), (0.86, 0.335)],
               [(0.86, 0.335), (-0.975, 0.335)],
               [(-0.385, 0.335), (-0.39, 1.0)],
               [(-1.06, 1.025), (-1.06, 0.74), (-1.02, 0.63), (-0.99, 0.52), (-0.975, 0.42), (-0.975, 0.335)]],
        handles=[(0.02, 0.935), (-0.84, 0.95)],
        mirror=dict(x=0.80, z=1.05),
        hood_lines=dict(x=(0.985, 1.86), y=0.66, front_z=0.737),
        tailgate=dict(y=0.66, z=(0.52, 1.37)),
        fuel=dict(x=-1.62, z=0.90, r=0.07),
        wipers=[(0.94, -0.52, 0.24), (0.94, 0.06, 0.70)], rear_wiper=dict(z=(1.10, 1.22)),
        spoiler=dict(x=-1.70, half=(0.10, 0.58, 0.016)),
        fin=dict(x=-1.45),
    ),
    'sedan': dict(
        head=dict(T=(2.30, 0.34, 0.703), D=(-1, -0.5, 0), outline=[(0.0, -0.020), (0.14, -0.036), (0.34, -0.046), (0.50, -0.042),
              (0.61, -0.016), (0.65, 0.020), (0.60, 0.046), (0.42, 0.050), (0.20, 0.042), (0.04, 0.028), (0.0, 0.014)],
              projector=(0.22, -0.006, 0.027), ind_u=0.55),
        grille_top=dict(z=(0.64, 0.69), half=(0.30, 0.25)),
        grille=dict(z=(0.33, 0.53), half_top=0.44, half_bottom=0.52),
        fog=dict(y=0.66, z=0.37, r=0.040),
        lip=dict(z=(0.235, 0.29), half=0.68),
        plate_front=dict(z=0.435),
        tail=dict(T=(-2.30, 0.40, 0.90), D=(1, -0.55, 0), outline=[(0.0, -0.040), (0.22, -0.048), (0.40, -0.040), (0.50, -0.012),
              (0.52, 0.030), (0.42, 0.048), (0.20, 0.052), (0.0, 0.038)], bar=0.010, rev_u=0.09, ind_u=(0.09, 0.19)),
        plate_rear=dict(z=0.74),
        diffuser=dict(z=(0.29, 0.38), half=0.64),
        doors=[[(1.06, 0.955), (1.055, 0.70), (1.035, 0.50), (1.02, 0.335)], [(1.02, 0.335), (-0.925, 0.335)],
               [(-0.375, 0.335), (-0.38, 0.99)],
               [(-1.02, 1.03), (-1.02, 0.72), (-0.98, 0.62), (-0.95, 0.52), (-0.93, 0.42), (-0.925, 0.335)]],
        handles=[(0.10, 0.93), (-0.80, 0.945)],
        mirror=dict(x=0.90, z=1.035),
        hood_lines=dict(x=(1.07, 2.16), y=0.68, front_z=0.737),
        trunk=dict(x=(-1.72, -2.31), y=0.70, rear_z=0.80),
        fuel=dict(x=-1.55, z=0.92, r=0.07),
        wipers=[(1.02, -0.54, 0.24), (1.02, 0.06, 0.70)],
        fin=dict(x=-1.10),
    ),
    'suv': dict(
        head=dict(T=(2.22, 0.36, 0.93), D=(-1, -0.5, 0), outline=[(0.0, -0.026), (0.14, -0.046), (0.32, -0.058), (0.47, -0.054),
              (0.57, -0.022), (0.61, 0.026), (0.56, 0.055), (0.38, 0.060), (0.18, 0.050), (0.04, 0.034), (0.0, 0.018)],
              projector=(0.21, -0.008, 0.032), ind_u=0.51),
        grille_top=dict(z=(0.845, 0.90), half=(0.32, 0.27)),
        grille=dict(z=(0.52, 0.79), half_top=0.48, half_bottom=0.52),
        fog=dict(y=0.70, z=0.49, r=0.045),
        lip=dict(z=(0.33, 0.40), half=0.66), skid_front=dict(z=(0.335, 0.395), half=0.34),
        plate_front=dict(z=0.47),
        tail=dict(T=(-2.23, 0.40, 1.06), D=(1, -0.55, 0), outline=[(0.0, -0.055), (0.20, -0.060), (0.38, -0.050), (0.48, -0.018),
              (0.50, 0.040), (0.40, 0.062), (0.20, 0.066), (0.0, 0.050)], bar=0.013, rev_u=0.09, ind_u=(0.09, 0.19)),
        plate_rear=dict(z=0.76),
        diffuser=dict(z=(0.36, 0.48), half=0.66), skid_rear=dict(z=(0.37, 0.44), half=0.38),
        doors=[[(1.06, 1.12), (1.06, 0.80), (1.02, 0.70), (0.945, 0.60), (0.885, 0.50), (0.865, 0.39)],
               [(0.865, 0.39), (-0.955, 0.39)], [(-0.345, 0.39), (-0.35, 1.14)],
               [(-1.07, 1.15), (-1.07, 0.80), (-1.02, 0.70), (-0.98, 0.60), (-0.96, 0.50), (-0.955, 0.39)]],
        handles=[(0.12, 1.075), (-0.80, 1.095)],
        mirror=dict(x=0.92, z=1.185),
        hood_lines=dict(x=(1.07, 2.02), y=0.72, front_z=0.955),
        tailgate=dict(y=0.70, z=(0.62, 1.57)),
        fuel=dict(x=-1.55, z=1.06, r=0.075),
        wipers=[(1.03, -0.56, 0.24), (1.03, 0.06, 0.72)], rear_wiper=dict(z=(1.22, 1.36)),
        spoiler=dict(x=-1.86, half=(0.10, 0.62, 0.018)),
        fin=dict(x=-1.60),
        roof_rails=dict(y=0.58, x=(0.25, -1.70), h=0.045),
        flares=0.05,
    ),
}


def add_details(key, surf, S, paint_ob, split_door=True):
    """Everything on the body's surface: lamps, grilles, plates, panel gaps,
    handles, mirrors, wipers, spoiler, antenna. Returns the body's details and
    (split_door) the driver's door's own: its mirror and handle."""
    P = DETAILS[key]
    bm = bmesh.new()
    bmd = bmesh.new() if split_door else bm
    # ---- headlights (both sides)
    H = P['head']
    D = unit(*H['D']); U = unit(-D.y, D.x, 0) * -1          # horizontal, toward the car's left/outside
    if U.y < 0:
        U = -U
    V = Vector((0, 0, 1))
    pts = H['outline']
    pu, pv, pr = H['projector']

    def head_mat(u, t, v):                                    # (bands that follow the lamp's outline)
        if u > H['ind_u']:
            return DM['IndL']
        return DM['DRL'] if t > 0.80 else DM['Lens']
    us = [p[0] for p in pts]
    for side in (1, -1):
        Ds = Vector((D.x, D.y * side, D.z)); Us = Vector((U.x, U.y * side, U.z)); T = Vector((H['T'][0], H['T'][1] * side, H['T'][2]))
        m = (lambda f, sd: (lambda u, t, v: f(u, t, v) if sd > 0 or f(u, t, v) != DM['IndL'] else DM['IndR']))(head_mat, side)
        cpatch(bm, surf, T, Us, V, Ds, min(us), max(us), lambda u: poly_edge(pts, u, False), lambda u: poly_edge(pts, u, True),
               44, 8, m, 0.004)
        # the projector: a chrome ring and its dark lens, round, on the lamp
        loc, nrm = surf.hit(T + Us * pu + V * pv - Ds * 2.0, Ds)
        if loc is not None:
            blob(bm, loc + nrm * 0.0055, (0.0025, pr, pr), e=2.2, nu=20, nv=6, mi=DM['Chrome'], rot=frame(nrm))
            blob(bm, loc + nrm * 0.0068, (0.0018, pr * 0.62, pr * 0.62), e=2.2, nu=16, nv=6, mi=DM['GlossBlack'], rot=frame(nrm))
    # ---- grilles, fog lamps, lip, front plate (projected straight from the front)
    Dx = Vector((-1, 0, 0)); Uy = Vector((0, 1, 0))
    g = P['grille_top']                                       # (a trapezoid: half[1] wide at its foot, half[0] at its top)
    z0, z1, h0, h1 = g['z'][0], g['z'][1], g['half'][1], g['half'][0]
    cpatch(bm, surf, (2.5, 0, 0), Uy, V, Dx, -max(h0, h1), max(h0, h1), lambda u: trap(u, z0, z1, h0, h1)[0],
           lambda u: trap(u, z0, z1, h0, h1)[1], 36, 3, lambda u, t, v: DM['GlossBlack'], 0.003)
    g = P['grille']
    z0, z1, h0, h1 = g['z'][0], g['z'][1], g['half_bottom'], g['half_top']
    cpatch(bm, surf, (2.5, 0, 0), Uy, V, Dx, -max(h0, h1), max(h0, h1), lambda u: trap(u, z0, z1, h0, h1)[0],
           lambda u: trap(u, z0, z1, h0, h1)[1], 44, 8, lambda u, t, v: DM['Grille'], 0.0025)
    for k in range(0 if LOW['strips_off'] else 5):            # horizontal bars across the grille
        z = g['z'][0] + (g['z'][1] - g['z'][0]) * (k + 0.5) / 5
        hw_ = g['half_bottom'] + (g['half_top'] - g['half_bottom']) * (k + 0.5) / 5 - 0.03
        strip(bm, surf, [(2.5, -hw_, z), (2.5, hw_, z)], Dx, 0.010, 0.006, DM['GlossBlack'])
    f = P['fog']
    rx, rz = f['r'] * 1.55, f['r'] * 1.25
    for side in (1, -1):                                      # an oval bezel, its round lamp inside
        cy = side * f['y']
        hh = lambda u, cy=cy: rz * math.sqrt(max(0.0, 1 - ((u - cy) / rx) ** 2))
        cpatch(bm, surf, (2.5, 0, 0), Uy, V, Dx, cy - rx, cy + rx, lambda u, hh=hh: f['z'] - hh(u), lambda u, hh=hh: f['z'] + hh(u),
               18, 4, lambda u, t, v: DM['Trim'], 0.003)
        loc, nrm = surf.hit((2.5, cy, f['z']), Dx)
        if loc is not None:
            blob(bm, loc + nrm * 0.0045, (0.002, f['r'] * 0.8, f['r'] * 0.8), e=2.2, nu=20, nv=6, mi=DM['Reflector'], rot=frame(nrm))
    l_ = P['lip']
    patch(bm, surf, (2.5, 0, 0), Uy, V, Dx, (-l_['half'], l_['half'], l_['z'][0], l_['z'][1]), 40, 3, lambda u, v: True, lambda u, v: DM['Trim'], 0.003)
    add_plate(bm, surf, +1, P['plate_front']['z'])
    # ---- tail lights (both sides)
    T_ = P['tail']
    D = unit(*T_['D']); U = Vector((-D.y, D.x, 0)).normalized()
    if U.y < 0:
        U = -U
    pts2 = T_['outline']

    def tail_mat(u, t, v):                                    # (bands that follow the lamp's outline)
        if 0.45 <= t < 0.70 and u > 0.03:
            return DM['Brake']
        if u < T_['rev_u'] and t < 0.40:
            return DM['Reverse']
        if T_['ind_u'][0] <= u < T_['ind_u'][1] and t < 0.40:
            return DM['IndL']
        return DM['Tail']
    us = [p[0] for p in pts2]
    for side in (1, -1):
        Ds = Vector((D.x, D.y * side, D.z)); Us = Vector((U.x, U.y * side, U.z)); T = Vector((T_['T'][0], T_['T'][1] * side, T_['T'][2]))
        m = (lambda f, sd: (lambda u, t, v: f(u, t, v) if sd > 0 or f(u, t, v) != DM['IndL'] else DM['IndR']))(tail_mat, side)
        cpatch(bm, surf, T, Us, V, Ds, min(us), max(us), lambda u: poly_edge(pts2, u, False), lambda u: poly_edge(pts2, u, True),
               48, 10, m, 0.004)
    # ---- rear: diffuser, plate
    Dr = Vector((1, 0, 0))
    d_ = P['diffuser']
    patch(bm, surf, (-2.5, 0, 0), Uy, V, Dr, (-d_['half'], d_['half'], d_['z'][0], d_['z'][1]), 40, 4, lambda u, v: True, lambda u, v: DM['Trim'], 0.003)
    add_plate(bm, surf, -1, P['plate_rear']['z'])
    # ---- panel gaps (doors on both sides, the hood, the tailgate)
    xb = P['doors'][2][0][0]                                  # the front door's rear edge (at the sill)
    for k, line in enumerate(P['doors']):
        if not split_door:
            strip(bm, surf, [(x, 1.5, z) for (x, z) in line], (0, -1, 0), 0.0042, 0.0012, DM['GlossBlack'], mirror=True)
            continue
        # the driver's door (left, front) has real gaps around it: its lines are drawn on the right only
        strip(bm, surf, [(x, -1.5, z) for (x, z) in line], (0, 1, 0), 0.0042, 0.0012, DM['GlossBlack'])
        if k == 1:
            strip(bm, surf, [(xb - 0.004, 1.5, line[0][1]), (line[-1][0], 1.5, line[-1][1])], (0, -1, 0), 0.0042, 0.0012, DM['GlossBlack'])
        elif k == 3:
            strip(bm, surf, [(x, 1.5, z) for (x, z) in line], (0, -1, 0), 0.0042, 0.0012, DM['GlossBlack'])
    h = P['hood_lines']
    strip(bm, surf, [(h['x'][0], h['y'], 2.0), (h['x'][1], h['y'] + 0.06, 2.0)], (0, 0, -1), 0.0042, 0.0012, DM['GlossBlack'], mirror=True)
    strip(bm, surf, [(2.6, -h['y'] - 0.06, h['front_z']), (2.6, h['y'] + 0.06, h['front_z'])], (-1, 0, 0), 0.0042, 0.0012, DM['GlossBlack'])
    if 'tailgate' in P:
        t_ = P['tailgate']
        strip(bm, surf, [(-2.6, t_['y'], t_['z'][0]), (-2.6, t_['y'], t_['z'][1])], (1, 0, 0), 0.0042, 0.0012, DM['GlossBlack'], mirror=True)
        strip(bm, surf, [(-2.6, -t_['y'], t_['z'][0]), (-2.6, t_['y'], t_['z'][0])], (1, 0, 0), 0.0042, 0.0012, DM['GlossBlack'])
    if 'trunk' in P:
        t_ = P['trunk']
        strip(bm, surf, [(t_['x'][0], t_['y'], 3.0), (t_['x'][1], t_['y'] - 0.02, 3.0)], (0, 0, -1), 0.0042, 0.0012, DM['GlossBlack'], mirror=True)
        strip(bm, surf, [(-2.6, -t_['y'], t_['rear_z']), (-2.6, t_['y'], t_['rear_z'])], (1, 0, 0), 0.0042, 0.0012, DM['GlossBlack'])
        strip(bm, surf, [(t_['x'][0], -t_['y'], 3.0), (t_['x'][0], t_['y'], 3.0)], (0, 0, -1), 0.0042, 0.0012, DM['GlossBlack'])
    for k_, Dk, sgn in (('skid_front', Dx, 2.5), ('skid_rear', Dr, -2.5)):
        if k_ in P:
            q = P[k_]
            patch(bm, surf, (sgn, 0, 0), Uy, V, Dk, (-q['half'], q['half'], q['z'][0], q['z'][1]), 30, 3, lambda u, v: True,
                  lambda u, v: DM['Rim'], 0.0045)
    if 'roof_rails' in P:                                     # one smooth bar each side, its feet on the roof
        rr_ = P['roof_rails']
        n_r = 16 if LOW['strips_off'] else 48
        for side in (1, -1):
            pts_r = []
            for k in range(n_r + 1):
                x = rr_['x'][0] + (rr_['x'][1] - rr_['x'][0]) * k / n_r
                loc, nrm = surf.hit((x, side * rr_['y'], 3.0), (0, 0, -1))
                if loc is not None:
                    f_ = abs(2 * k / n_r - 1)
                    pts_r.append(loc + Vector((0, 0, rr_['h'] * (1 - f_ ** 8) + 0.004)))
            tube(bm, pts_r, 0.014, 0.010, DM['Rim'], segs=8 if LOW['strips_off'] else 12)
    if P.get('flares'):
        R = tyre_radius(S); xa, xb = axles(S)
        for xc in (xa, xb):
            arc = [(xc + (R + S['arch_gap'] + 0.022) * math.cos(a), 1.5, R + (R + S['arch_gap'] + 0.022) * math.sin(a))
                   for a in np.linspace(math.radians(8), math.radians(172), 40)]
            strip(bm, surf, arc, (0, -1, 0), P['flares'], 0.004, DM['Trim'], mirror=True, force=True)
    fu = P['fuel']                                            # the fuel door, on the right
    circ = [(fu['x'] + fu['r'] * math.cos(a), -1.5, fu['z'] + fu['r'] * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 33)]
    strip(bm, surf, circ, (0, 1, 0), 0.004, 0.0012, DM['GlossBlack'])
    # ---- handles (body colour on a dark recess), both sides
    for hi, (x, z) in enumerate(P['handles']):
        for side in (1, -1):
            if LOW['strips_off']:
                continue
            loc, nrm = surf.hit((x, side * 1.5, z), (0, -side, 0))
            if loc is None:
                continue
            bm_ = bmd if (side > 0 and hi == 0) else bm
            he = lambda u: 0.026 * max(0.0, 1 - (u / 0.075) ** 4) ** 0.25
            cpatch(bm_, surf, (x, side * 1.5, z), (1, 0, 0), (0, 0, 1), (0, -side, 0), -0.075, 0.075, lambda u, he=he: -he(u),
                   lambda u, he=he: he(u), 12, 3, lambda u, t, v: DM['GlossBlack'], 0.0012, skirt=0)
            blob(bm_, loc + nrm * 0.010, (0.062, 0.010, 0.013), e=3.5, nu=16, nv=8, mi=DM['Paint'],
                 rot=Vector((0, 1, 0)).rotation_difference(Vector((0, side, 0)) if side > 0 else Vector((0, 1, 0))).to_matrix())
    # ---- mirrors (housing in body colour, black base, glass, a repeater)
    mi_ = P['mirror']
    for side in (1, -1):
        loc, nrm = surf.hit((mi_['x'], side * 1.5, mi_['z']), (0, -side, 0))
        if loc is None:
            continue
        bm_ = bmd if side > 0 else bm
        base = loc + Vector((0, side * 0.03, 0))
        blob(bm_, base, (0.05, 0.035, 0.03), e=3, nu=14, nv=8, mi=DM['Trim'])
        c = loc + Vector((-0.01, side * 0.15, 0.035))
        blob(bm_, c, (0.055, 0.095, 0.058), e=2.6, nu=20, nv=12, mi=DM['Paint'])
        blob(bm_, c + Vector((-0.050, side * 0.004, 0)), (0.008, 0.085, 0.050), e=4, nu=16, nv=8, mi=DM['MirrorGlass'])
        blob(bm_, c + Vector((0.035, side * 0.02, -0.040)), (0.022, 0.06, 0.006), e=4, nu=12, nv=6, mi=DM['IndL'] if side > 0 else DM['IndR'])
    # ---- wipers, rear wiper, spoiler, antenna
    for (x, y0, y1) in P['wipers']:
        strip(bm, surf, [(x - 0.02, y0, 3.0), (x, y1, 3.0)], (0, 0, -1), 0.014, 0.012, DM['Trim'])
    if P.get('rear_wiper'):
        z0, z1 = P['rear_wiper']['z']
        strip(bm, surf, [(-3.0, 0.0, z0), (-3.0, 0.32, z1)], (1, 0, 0), 0.012, 0.012, DM['Trim'])
    sp = P.get('spoiler')                                      # sitting on the roof's rear edge
    loc, nrm = surf.hit((sp['x'], 0, 3.0), (0, 0, -1)) if sp else (None, None)
    if loc is not None:
        blob(bm, loc + Vector((-0.02, 0, sp['half'][2] * 0.6)), sp['half'], e=5, nu=28, nv=8, mi=DM['Paint'],
             rot=Matrix.Rotation(math.radians(-6), 3, 'Y'))
    loc, nrm = surf.hit((P['fin']['x'], 0, 3.0), (0, 0, -1))
    if loc is not None and not LOW['strips_off']:
        blob(bm, loc + Vector((0, 0, 0.028)), (0.075, 0.022, 0.034), e=2.4, nu=16, nv=8, mi=DM['GlossBlack'])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges)
    if not split_door:
        return obj_from_bm(bm, key + '_details'), None
    bmesh.ops.recalc_face_normals(bmd, faces=bmd.faces)
    return obj_from_bm(bm, key + '_details'), obj_from_bm(bmd, key + '_door_items')


def add_plate(bm, surf, sign, z):
    """A number plate 52 x 11 cm (Israeli size) on a dark recess, flat and square to
    the car's length (the game finds it by its material and puts its own plate there)."""
    loc, nrm = surf.hit((sign * 3.0, 0, z), (-sign, 0, 0))
    if loc is None:
        return
    x = loc.x + sign * 0.004
    w, h = 0.52, 0.11
    vs = [bm.verts.new((x, y, zz)) for (y, zz) in ((-w / 2, z - h / 2), (w / 2, z - h / 2), (w / 2, z + h / 2), (-w / 2, z + h / 2))]
    f = bm.faces.new(vs if sign > 0 else vs[::-1]); f.material_index = DM['Plate']
    patch(bm, surf, (sign * 3.0, 0, 0), (0, 1, 0), (0, 0, 1), (-sign, 0, 0), (-w / 2 - 0.02, w / 2 + 0.02, z - h / 2 - 0.02, z + h / 2 + 0.02),
          16, 4, lambda u, v: True, lambda u, v: DM['Trim'], 0.0015, skirt=0)


def mname(m):
    return m.name[2:] if m.name.startswith('R_') else m.name


def st_at(st, x, field):
    """A station value, interpolated along the car at x (W, zb, zsh, or the top's Wgb, zgb, Wgt, zgt, Wrr, zrr, zr)."""
    def val(s_):
        top = s_[8]
        if field == 'W':
            return s_[2]
        if field == 'zb':
            return s_[3]
        if field == 'zsh':
            return s_[7]
        if top[0] == 'cabin':
            return dict(Wgb=top[1], zgb=top[2], Wgt=top[3], zgt=top[4], Wrr=top[5], zrr=top[6], zr=top[7])[field]
        return dict(Wgb=s_[6], zgb=top[1], Wgt=s_[6], zgt=top[1], Wrr=s_[6], zrr=top[1], zr=top[2])[field]
    if x >= st[0][0]:
        return val(st[0])
    for a, b in zip(st[:-1], st[1:]):
        if a[0] >= x >= b[0]:
            t = (a[0] - x) / max(a[0] - b[0], 1e-9)
            return val(a) + (val(b) - val(a)) * t
    return val(st[-1])


def zone_x(st, zone):
    """Where a zone starts (its first station's x)."""
    for s_ in st:
        if s_[1] == zone:
            return s_[0]
    return None


def line_x(poly, z):
    """x of a door line (a polyline of (x, z), monotonic in z) at height z (held beyond its ends)."""
    pts = sorted(poly, key=lambda p: p[1])
    if z <= pts[0][1]:
        return pts[0][0]
    if z >= pts[-1][1]:
        return pts[-1][0]
    for (x0, z0), (x1, z1) in zip(pts[:-1], pts[1:]):
        if z0 <= z <= z1:
            return x0 + (x1 - x0) * (z - z0) / max(z1 - z0, 1e-9)


# ---------------------------------------------------------------- the drivers (the player's and the city's people's Drive pose)
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'city', 'assets')
PLAYER_HIP = 1.01           # the game's hip height for the player (index.html: hip:1.01), the hip joint at rest
HAND_BONES = ('hand', 'thumb', 'index', 'middle', 'ring', 'pinky')


def measure_drivers():
    """Every character of the game in the Drive pose (as the game seats them: the root
    at the seat's height minus the hip height): their points relative to the hip joint,
    in car axes, with the hands marked. Returns {id: dict(pts, hand, head, mesh)}."""
    npc = json.load(open(os.path.join(ASSETS, 'npc', 'npcs.json')))
    files = [('player', os.path.join(ASSETS, 'player.glb'), PLAYER_HIP)] + \
            [(k, os.path.join(ASSETS, 'npc', k + '.glb'), v['hip']) for k, v in sorted(npc.items())]
    out = {}
    for cid, f, hip in files:
        before = set(bpy.data.objects); before_act = set(bpy.data.actions)
        bpy.ops.import_scene.gltf(filepath=f)
        new = [o for o in bpy.data.objects if o not in before]
        arm = next(o for o in new if o.type == 'ARMATURE')
        act = next(a for a in bpy.data.actions if a not in before_act and a.name.split('|')[-1].startswith('Drive'))
        ad = arm.animation_data or arm.animation_data_create()
        for t in ad.nla_tracks:
            t.mute = True
        ad.action = act
        try:
            ad.action_slot = act.slots[0]
        except Exception:
            pass
        bpy.context.scene.frame_set(int(act.frame_range[0]))
        dg = bpy.context.evaluated_depsgraph_get()
        P, Hn, verts_all, faces_all = [], [], [], []
        for o in new:
            if o.type != 'MESH' or o.find_armature() is None:
                continue
            p_ = o
            lod1 = False
            while p_ is not None:
                lod1 |= p_.name.startswith('LOD1')
                p_ = p_.parent
            if lod1:
                continue
            vg = {g.index: g.name for g in o.vertex_groups}
            hand = np.array([any(k in vg.get(max(v.groups, key=lambda g: g.weight).group, '') for k in HAND_BONES)
                             if len(v.groups) else False for v in o.data.vertices])
            me = o.evaluated_get(dg).to_mesh()
            co = np.array([(o.matrix_world @ v.co)[:] for v in me.vertices])
            base = sum(len(v) for v in verts_all)
            verts_all.append(co); faces_all += [[base + i for i in p.vertices] for p in me.polygons]
            o.evaluated_get(dg).to_mesh_clear()
            P.append(co); Hn.append(hand if len(hand) == len(co) else np.zeros(len(co), bool))
        P = np.concatenate(P); Hn = np.concatenate(Hn)
        C = np.stack([-P[:, 1], P[:, 0], P[:, 2] - hip], 1)        # (the import faces -y) -> x forward, y left; hip joint at 0
        V = np.concatenate(verts_all); V = np.stack([-V[:, 1], V[:, 0], V[:, 2] - hip], 1)
        me = bpy.data.meshes.new('drv_' + cid); me.from_pydata(V.tolist(), [], faces_all); me.use_fake_user = True
        out[cid] = dict(pts=C, hand=Hn, mesh=me, hip=hip)
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
        log('driver', cid, 'points %d, head top %.3f above the hip joint' % (len(C), C[:, 2].max()))
    return out


def fit_wheel(drivers):
    """The steering wheel through everyone's hands: a rim (centre, normal toward the
    driver, radius) nearest to every hand, relative to the hip joint."""
    H = []
    for d in drivers.values():
        h = d['pts'][d['hand']]
        for sg in (1, -1):
            hh = h[h[:, 1] * sg > 0]
            H.append(hh.mean(0))
    H = np.array(H)
    # a 41 cm wheel leaning back 15 degrees (a larger lean puts the highest and the
    # lowest hands further than 6 cm from the rim), placed nearest to every hand
    best = None
    tilt, r = math.radians(15), 0.205
    n = np.array([-math.cos(tilt), 0.0, math.sin(tilt)])
    for xc in np.arange(0.15, 0.401, 0.005):
        for zc in np.arange(0.36, 0.621, 0.005):
            v = H - np.array([xc, 0.0, zc])
            vn = v @ n
            vp = np.linalg.norm(v - np.outer(vn, n), axis=1)
            d = np.sqrt(vn ** 2 + (vp - r) ** 2).max()
            if best is None or d < best[0]:
                best = (d, xc, zc)
    err, xc, zc = best
    return dict(c=Vector((xc, 0.0, zc)), n=Vector((-math.cos(tilt), 0.0, math.sin(tilt))), r=float(r), tube=0.016,
                tilt=math.degrees(tilt), fit_err=float(err))


# ---------------------------------------------------------------- shells: an inside skin with rims
def offset_shell(bm_out, faces, depth_of, mat_of, rim_of, nrm_of=None, lip=0.0):
    """An inner skin for `faces` (of a mesh with up-to-date normals): their copy moved in
    along the vertex normals by depth_of(vert), facing the other way, and rims joining
    the two along the boundary edges for which rim_of(edge, face) gives a material."""
    fset = set(faces)
    new = {}
    for f in faces:
        for v in f.verts:
            if v not in new:
                new[v] = bm_out.verts.new(v.co - (nrm_of(v) if nrm_of else v.normal) * depth_of(v))
    for f in faces:
        nf = bm_out.faces.new([new[v] for v in reversed(f.verts)])
        nf.material_index = mat_of(f)
    outer = {}
    for f in faces:
        for l in f.loops:
            e = l.edge
            if sum(1 for g in e.link_faces if g in fset) != 1:
                continue
            m = rim_of(e, f)
            if m is None:
                continue
            a, b = l.vert, l.link_loop_next.vert
            for v in (a, b):
                if v not in outer:
                    outer[v] = bm_out.verts.new(v.co)
            q = bm_out.faces.new((outer[b], outer[a], new[a], new[b]))
            q.material_index = m
            if lip and e.is_boundary:
                # a seal's lip from the rim's inner end into the opening (both faces: it is seen from either side)
                for v in (a, b):
                    if ('lip', v) not in outer:
                        d = inward(v) or Vector()
                        d = Vector((d.x, 0.0, d.z))             # (in the jamb's plane, across the car's side)
                        outer[('lip', v)] = bm_out.verts.new(new[v].co - (d.normalized() if d.length > 1e-9 else d) * lip)
                la, lb = outer[('lip', a)], outer[('lip', b)]
                bm_out.faces.new((new[a], new[b], lb, la)).material_index = m
                dup = [bm_out.verts.new(v.co) for v in (la, lb, new[b], new[a])]
                bm_out.faces.new(dup).material_index = m
    return new


def inward(v, skip=None, scaled=False):
    """At a boundary vertex: the direction into the mesh, across its boundary edges
    (from each edge toward its face's centre, square to the edge), averaged; scaled:
    as long as moving each of its edges in by 1 takes (longer at a corner)."""
    acc = Vector(); ws = []
    for e in v.link_edges:
        fs = [f for f in e.link_faces if skip is None or not skip(f)]
        if len(fs) != 1:
            continue
        a, b = e.verts
        t = (b.co - a.co)
        if t.length < 1e-9:
            continue
        t.normalize()
        w = fs[0].calc_center_median() - (a.co + b.co) / 2
        w -= t * w.dot(t)
        if w.length > 1e-9:
            acc += w.normalized(); ws.append(w.normalized())
    if acc.length < 1e-9:
        return None
    d = acc.normalized()
    if scaled:
        d = d / max(0.4, min(d.dot(w) for w in ws))
    return d


def move_boundary(bm, dist):
    """Moves a mesh's boundary vertices by dist along the surface, into the mesh (away from the hole)."""
    bm.normal_update()
    moves = {}
    for v in bm.verts:
        if not any(e.is_boundary for e in v.link_edges):
            continue
        d = inward(v, scaled=True)
        if d is not None:
            moves[v] = d * dist
    # (corners where the boundary meets itself: as their neighbours along it)
    for v in bm.verts:
        if v in moves or not any(e.is_boundary for e in v.link_edges):
            continue
        nb = [moves[e.other_vert(v)] for e in v.link_edges if e.is_boundary and e.other_vert(v) in moves]
        if nb:
            moves[v] = sum(nb, Vector()) / len(nb)
    for v, m in moves.items():
        v.co += m


def obox(bm, center, axes, half, mi):
    """A box with its own axes (three unit vectors) and half sizes."""
    c = Vector(center)
    ax = [Vector(a).normalized() * h for a, h in zip(axes, half)]
    vs = {}
    for i in (-1, 1):
        for j in (-1, 1):
            for k in (-1, 1):
                vs[(i, j, k)] = bm.verts.new(c + ax[0] * i + ax[1] * j + ax[2] * k)
    made = []
    for quad in (((-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1)), ((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)),
                 ((-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)), ((-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1)),
                 ((-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)), ((1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1))):
        f = bm.faces.new([vs[q] for q in quad]); f.material_index = mi; made.append(f)
    bmesh.ops.recalc_face_normals(bm, faces=made)


def frame(n, up=Vector((0, 0, 1))):
    """A rotation whose local x is n (and z as near to `up` as it can be)."""
    n = Vector(n).normalized()
    y = up.cross(n)
    if y.length < 1e-6:
        y = Vector((0, 1, 0))
    y.normalize()
    z = n.cross(y)
    return Matrix((n, y, z)).transposed()


def torus(bm, c, n, r, t, segs, tsegs, mi):
    """A ring (the steering wheel's rim): centre c, axis n, radius r, tube radius t."""
    n = Vector(n).normalized(); e1 = Vector((0, 1, 0)); e2 = -n.cross(e1).normalized()
    rings = []
    for i in range(segs):
        th = 2 * math.pi * i / segs
        d = e1 * math.cos(th) + e2 * math.sin(th)
        rings.append([bm.verts.new(c + d * (r + t * math.cos(2 * math.pi * j / tsegs)) + n * (t * math.sin(2 * math.pi * j / tsegs)))
                      for j in range(tsegs)])
    for i in range(segs):
        a, b = rings[i], rings[(i + 1) % segs]
        for j in range(tsegs):
            bm.faces.new((a[(j + 1) % tsegs], b[(j + 1) % tsegs], b[j], a[j])).material_index = mi
    return e1, e2


# ---------------------------------------------------------------- the cabin
CABIN = {   # the floor's height over the underside, the rear seat behind the front one
    'hatch': dict(floor_lift=0.025, rear_dx=0.80),
    'sedan': dict(floor_lift=0.025, rear_dx=0.86),
    'suv': dict(floor_lift=0.10, rear_dx=0.86),
}
HEEL = 0.262          # the hip joint above the floor: the longest legs' heels rest on it (Drive pose)


def cabin_spec(key, S, st, wheel_fit):
    xf, xr = axles(S)
    c = CABIN[key]
    Hx = xf - 1.38                                  # the hip joint 1.38 m behind the front axle (as the game had it)
    zfloor = st_at(st, Hx, 'zb') + c['floor_lift']
    H = Vector((Hx, 0.36, zfloor + HEEL))
    return dict(H=H, zfloor=zfloor, rear=Vector((Hx - c['rear_dx'], 0.0, H.z + 0.03)), wheel=wheel_fit,
                x_cowl=zone_x(st, 'screen'), x_fw=min(zone_x(st, 'screen') - 0.02, Hx + 1.06),
                x_back=-S['length'] / 2 + 0.10, x_ws_top=zone_x(st, 'window'))


def build_cabin(key, S, st, body, C, full_bvh, lo=False):
    """The inside: an inner skin under the body's panels (headliner above the windows'
    feet), rims at the windows and the door's opening, liners on the wheel wells, the
    floor and the firewall, the dashboard with its instruments and screen, the steering
    wheel and column, two front seats, the console, the rear bench, the parcel shelf
    and the mirror. Returns one object (DMATS materials)."""
    bm = bmesh.new()
    src = bmesh.new(); src.from_mesh(body.data); src.normal_update()
    names = [mname(m) for m in body.data.materials]
    nm = lambda f: names[f.material_index]
    H = C['H']; zf = C['zfloor']; xc = C['x_cowl']; xb = C['x_back']
    Ws = S['width'] / 2

    def half_w(x, z, margin=0.012):
        """The inside's half width at (x, z), or None where that point isn't inside the body."""
        loc, n_, i_, d_ = full_bvh.ray_cast(Vector((x, 0, z)), Vector((0, 1, 0)), 3.0)
        if loc is None or not is_inside(full_bvh, Vector((x, 0, z))):
            return None
        return loc.y - margin

    def under(x):
        """The underside's height on the car's middle line at x."""
        loc, n_, i_, d_ = full_bvh.ray_cast(Vector((x, 0, -1.0)), Vector((0, 0, 1)), 3.0)
        return loc.z if loc is not None else None

    # ---- the inner skin
    faces = []
    for f in src.faces:
        if nm(f) not in ('Paint', 'Trim'):
            continue
        cc = f.calc_center_median()
        if cc.z < zf - 0.005 or cc.x > xc + 0.005 or cc.x < xb:
            continue
        faces.append(f)
    inner = offset_shell(bm, faces, lambda v: 0.068 if v.is_boundary else 0.025 + 0.03 * min(1.0, abs(v.normal.y) / 0.8),
                 lambda f: DM['Headliner'] if f.calc_center_median().z > st_at(st, f.calc_center_median().x, 'zgb') + 0.03 else DM['Interior'],
                 lambda e, f: DM['Trim'] if (len(e.link_faces) == 1 or any(nm(g) == 'Glass' for g in e.link_faces if g is not f)) else None,
                 nrm_of=lambda v: (Vector((0, 1 if v.co.y > 0 else -1, 0)) if (v.is_boundary or any(e.other_vert(v).is_boundary for e in v.link_edges)) else
                                   sum((f.normal for f in v.link_faces if nm(f) != 'Well'), Vector()).normalized()
                                   if any(nm(f) != 'Well' for f in v.link_faces) else v.normal),
                 lip=0.04)
    # (where the panels curl round an arch's edge, their inside would fall into the wheel well: it goes round it)
    R_t = tyre_radius(S); Wi = well_inner(S); gone = set()
    for xa in axles(S):
        ctr = Vector((xa, 0.0, R_t)); rr = R_t + S['arch_gap'] + 0.012
        for v in inner.values():
            if abs(v.co.y) < Wi - 0.01:
                continue
            d = Vector((v.co.x - ctr.x, 0.0, v.co.z - ctr.z))
            if d.length < rr:
                if v.co.z < ctr.z:                      # (below the axle the well is open: nothing to go round)
                    gone.add(v); continue
                d = d.normalized() if d.length > 1e-6 else Vector((0, 0, 1))
                v.co.x, v.co.z = ctr.x + d.x * rr, ctr.z + d.z * rr
    if gone:
        bmesh.ops.delete(bm, geom=list(gone), context='VERTS')
    # ---- the wheel wells, from the inside
    for f in src.faces:
        if nm(f) != 'Well':
            continue
        cc = f.calc_center_median()
        if cc.z < zf - 0.02 or cc.x > xc or any(abs(v.co.y) > Ws - 0.06 for v in f.verts):
            continue                                    # (not near the arch's lip: the liner would show outside)
        cos_ = [v.co - f.normal * 0.012 for v in reversed(f.verts)]
        if not all(is_inside(full_bvh, c) for c in cos_):
            continue                                    # (nor where the body tucks in under the arch's edge)
        vs = [bm.verts.new(c) for c in cos_]
        bm.faces.new(vs).material_index = DM['Interior']
    src.free()

    def strip_xy(xs, z_of, mi, up=True):
        """A floor-like strip across the car (y from -half to half) along xs."""
        prev = None
        for x in xs:
            z = z_of(x)
            w = None
            if z is not None:                               # (the narrower of the width at its own height and just above)
                ws_ = [half_w(x, z + dz) for dz in (0.004, 0.03)]
                w = None if None in ws_ else min(ws_)
            if w is None or w < 0.05:                       # (outside the body here: the strip stops)
                prev = None
                continue
            row = (bm.verts.new((x, -w, z)), bm.verts.new((x, w, z)))
            if prev:
                f = bm.faces.new((prev[0], prev[1], row[1], row[0]))
                f.material_index = mi
                f.normal_update()
                if (f.normal.z > 0) != up:
                    f.normal_flip()
            prev = row

    def wall_yz(x, z0, z1, mi, face_x, margin=0.012):
        """A wall across the car at x, from z0 to z1, facing face_x (+1 forward / -1 back)."""
        prev = None
        n = max(2, int((z1 - z0) / 0.04))
        for k in range(n + 1):
            z = z0 + (z1 - z0) * k / n
            w = half_w(x, z, margin)
            if w is None or w < 0.05:
                prev = None
                continue
            row = (bm.verts.new((x, -w, z)), bm.verts.new((x, w, z)))
            if prev:
                f = bm.faces.new((prev[0], prev[1], row[1], row[0])); f.material_index = mi
                f.normal_update()
                if f.normal.x * face_x < 0:
                    f.normal_flip()
            prev = row

    step = 0.10 if lo else 0.04
    xs = list(np.arange(C['x_fw'], xb - 1e-6, -step)) + [xb]
    # (the floor follows the underside where it rises toward the ends, and stops where the body does)
    strip_xy(xs, lambda x: (lambda u: None if u is None else max(zf, u + 0.02))(under(x)), DM['Carpet'])
    z_dash = st_at(st, xc, 'zgb') - 0.015
    wall_yz(C['x_fw'], zf, z_dash - 0.05, DM['Interior'], -1, margin=0.07)
    # ---- dashboard
    x_r = H.x + 0.52
    xd, dx = (x_r + xc) / 2, (xc - x_r) / 2
    z_bot = H.z + 0.16
    zd, dz = (z_dash + z_bot) / 2, (z_dash - z_bot) / 2
    Wd = half_w(xd, zd, 0.065) or (Ws - 0.12)
    blob(bm, (xd, 0, zd), (dx, Wd, dz), e=5, nu=32, nv=12, mi=DM['Dash'])
    blob(bm, (H.x + 0.62, H.y, z_dash + 0.02), (0.085, 0.15, 0.045), e=3, nu=20, nv=10, mi=DM['Dash'])            # binnacle
    blob(bm, (H.x + 0.565, H.y, z_dash - 0.005), (0.008, 0.12, 0.036), e=5, nu=16, nv=8, mi=DM['Screen'])          # cluster
    blob(bm, (H.x + 0.64, 0.0, z_dash + 0.07), (0.010, 0.125, 0.075), e=6, nu=16, nv=8, mi=DM['Screen'],
         rot=Matrix.Rotation(math.radians(-14), 3, 'Y'))                                                                 # centre screen
    if not lo:
        for y in (0.10, -0.10, Wd - 0.11, -(Wd - 0.11)):
            blob(bm, (x_r - 0.004, y, z_dash - 0.075), (0.008, 0.055, 0.02), e=6, nu=12, nv=6, mi=DM['GlossBlack'])   # vents
    # ---- steering wheel and column
    W_ = C['wheel']
    wc = H + W_['c']; wn = W_['n']
    e1, e2 = torus(bm, wc, wn, W_['r'], W_['tube'], 16 if lo else 40, 5 if lo else 10, DM['Rubber'])
    hub = wc - wn * 0.02
    for d in (e1, -e1, -e2):
        a_ = hub + d * 0.05; b_ = wc + d * (W_['r'] - 0.008)
        mid = (a_ + b_) / 2
        obox(bm, mid, (d, d.cross(wn), wn), ((b_ - a_).length / 2, 0.018 if d.z > -0.5 else 0.024, 0.008), DM['DarkMetal'])
    blob(bm, hub, (0.03, 0.075, 0.065), e=3, nu=16, nv=8, mi=DM['Dash'], rot=frame(-wn))
    col_c = wc - wn * 0.20
    blob(bm, col_c, (0.17, 0.035, 0.04), e=2.4, nu=12, nv=8, mi=DM['Dash'], rot=frame(-wn))
    # ---- front seats
    for sy in (1, -1):
        y = sy * H.y
        rc = Matrix.Rotation(math.radians(-7), 3, 'Y'); rb = Matrix.Rotation(math.radians(-6), 3, 'Y')
        blob(bm, (H.x + 0.05, y, H.z - 0.115 - 0.06), (0.26, 0.24, 0.06), e=3.2, nu=24, nv=10, mi=DM['Seat'], rot=rc)   # cushion
        for s2 in (1, -1):                                                                                             # its bolsters
            blob(bm, (H.x + 0.02, y + s2 * 0.225, H.z - 0.115 - 0.035), (0.22, 0.045, 0.05), e=3, nu=14, nv=8, mi=DM['Seat'], rot=rc)
        obox(bm, (H.x + 0.05, y, (zf + H.z - 0.23) / 2), ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
             (0.17, 0.17, max(0.01, (H.z - 0.23 - zf) / 2)), DM['Interior'])                                         # base
        blob(bm, (H.x - 0.25 - 0.055, y, H.z + 0.30), (0.055, 0.25, 0.37), e=3.2, nu=24, nv=12, mi=DM['Seat'], rot=rb)  # back
        blob(bm, (H.x - 0.27, y, H.z + 0.80), (0.05, 0.13, 0.095), e=3, nu=16, nv=10, mi=DM['Seat'])                   # headrest
        for s2 in (1, -1):
            obox(bm, (H.x - 0.285, y + s2 * 0.07, H.z + 0.68), ((1, 0, 0), (0, 1, 0), (0, 0, 1)), (0.006, 0.006, 0.04), DM['Chrome'])
    # ---- console and gear lever
    zc_top = H.z + 0.04
    blob(bm, (H.x + 0.20, 0, (zf + zc_top) / 2), (0.44, 0.095, (zc_top - zf) / 2), e=5, nu=20, nv=8, mi=DM['Interior'])
    blob(bm, (H.x + 0.40, 0, zc_top + 0.05), (0.015, 0.015, 0.05), e=2, nu=8, nv=6, mi=DM['DarkMetal'])
    blob(bm, (H.x + 0.40, 0, zc_top + 0.10), (0.03, 0.025, 0.022), e=2.5, nu=10, nv=6, mi=DM['Dash'])
    # ---- rear bench
    R_ = C['rear']
    Wb = min(0.62, well_inner(S) - 0.02, half_w(R_.x, R_.z - 0.1, 0.06) or 0.62)
    blob(bm, (R_.x + 0.06, 0, R_.z - 0.17), (0.25, Wb, 0.065), e=3.2, nu=28, nv=10, mi=DM['Seat'],
         rot=Matrix.Rotation(math.radians(-6), 3, 'Y'))
    obox(bm, (R_.x + 0.06, 0, (zf + R_.z - 0.22) / 2), ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
         (0.22, Wb - 0.02, max(0.01, (R_.z - 0.22 - zf) / 2)), DM['Interior'])
    rbk = Matrix.Rotation(math.radians(-20), 3, 'Y')
    bk_c = Vector((R_.x - 0.30, 0, R_.z + 0.22))
    blob(bm, bk_c, (0.06, Wb, 0.33), e=3.2, nu=28, nv=12, mi=DM['Seat'], rot=rbk)
    bk_top = bk_c + rbk @ Vector((0, 0, 0.33))
    for y in (0.36, -0.36):
        blob(bm, (bk_top.x - 0.02, y, bk_top.z + 0.09), (0.045, 0.12, 0.08), e=3, nu=14, nv=8, mi=DM['Seat'])
    # ---- parcel shelf (and the sedan's wall in front of the boot)
    x0 = bk_top.x - 0.06
    x_end = (zone_x(st, 'deck') or xb) if key == 'sedan' else xb + 0.02
    xs = list(np.arange(x0, x_end - 1e-6, -step)) + [x_end]
    strip_xy(xs, lambda x: st_at(st, x, 'zgb') - 0.035, DM['Interior'])
    if key == 'sedan':
        wall_yz(x0, zf, st_at(st, x0, 'zgb') - 0.035, DM['Interior'], 1)
    # ---- the rear-view mirror
    xm = C['x_ws_top'] - 0.10
    loc, n_, i_, d_ = full_bvh.ray_cast(Vector((xm, 0, 0.5)), Vector((0, 0, 1)), 3.0)
    if loc is not None:
        blob(bm, (xm - 0.01, 0, loc.z - 0.13), (0.014, 0.11, 0.032), e=4, nu=14, nv=8, mi=DM['Dash'])
        obox(bm, (xm, 0, loc.z - 0.07), ((1, 0, 0), (0, 1, 0), (0, 0, 1)), (0.008, 0.008, 0.05), DM['Dash'])
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-5, edges=bm.edges)
    return obj_from_bm(bm, key + ('_cabin_lo' if lo else '_cabin'))


# ---------------------------------------------------------------- the driver's door
DOOR_OPEN = math.radians(65)
DOOR_GAP = 0.0022           # each side of the cut: a 4.4 mm gap all round


def split_door(key, body, st, items, C):
    """Cuts the driver's (left, front) door out of the body along its lines: the front
    edge from the A-pillar's foot around the front arch, the sill, the B-pillar; above
    the window's foot the door keeps the glass and its seals (not the A-pillar, the
    roof or the windscreen). The door gets an inner panel 4.5 cm in (3 cm at the
    frame), an armrest and a handle, rims (seals) round its edge and its window, its
    mirror and outer handle. Returns (door object in body coordinates, hinge)."""
    P = DETAILS[key]
    front, sill, rear = P['doors'][0], P['doors'][1], P['doors'][2]
    z_sill = sill[0][1]
    # (the custom normals go first: through the cut they would turn stale; they are transferred again after it)
    with bpy.context.temp_override(object=body, active_object=body):
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    bm = bmesh.new(); bm.from_mesh(body.data)
    L_band = bm.faces.layers.int.get('band'); L_zone = bm.faces.layers.int.get('zone'); L_side = bm.faces.layers.int.get('side')
    names = [mname(m) for m in body.data.materials]
    z_top = max(st_at(st, x, 'zrr') for x in np.linspace(rear[0][0], front[0][0], 20)) + 0.05

    def cut(p0, p1, zlo, zhi, extend=0.0):
        (x0, z0), (x1, z1) = p0, p1
        no = Vector((z1 - z0, 0, -(x1 - x0))).normalized()
        co = Vector((x0, 0, z0))
        geom = set()
        for f in bm.faces:
            cc = f.calc_center_median()
            if cc.y > 0.25 and zlo - 0.04 < cc.z < zhi + 0.04 and abs((cc - co).dot(no)) < 0.12:
                geom.add(f); geom.update(f.edges); geom.update(f.verts)
        if geom:
            # (vertices within 4 mm of the cut go onto it first: no slivers along the cut, which the gap would fold)
            for v in [g for g in geom if isinstance(g, bmesh.types.BMVert)]:
                dd = (v.co - co).dot(no)
                if abs(dd) < 0.007:
                    v.co -= no * dd
            bmesh.ops.bisect_plane(bm, geom=list(geom), plane_co=co, plane_no=no)

    for p0, p1 in zip(front[:-1], front[1:]):
        cut(p0, p1, min(p0[1], p1[1]), max(p0[1], p1[1]))
    xr0, xr1 = rear[0][0], rear[-1][0]
    cut((xr0, z_sill), rear[-1], z_sill, rear[-1][1])              # (the cut and the door's outline: the same line)
    cut(rear[-1], (xr1, z_top), rear[-1][1], z_top)
    cut((front[-1][0] + 0.05, z_sill), (xr0 - 0.05, z_sill), z_sill, z_sill)

    def in_door(f):
        if f[L_side] != 1:
            return False
        zone = ZONES[f[L_zone] - 1] if f[L_zone] > 0 else None
        j = f[L_band]
        if zone == 'screen':
            ok = 1 <= j <= 7
        elif zone in ('window', 'bpillar'):
            ok = 1 <= j <= 8
        elif zone == 'hood':
            ok = 1 <= j <= 5
        else:
            ok = False
        if not ok:
            return False
        # every corner within the outline (a face the cuts missed stays with the body: no overlap)
        return all(v.co.z > z_sill - 0.0005 and v.co.x < line_x(front, v.co.z) + 0.0005 and
                   v.co.x > line_x(rear, min(v.co.z, rear[-1][1])) - 0.0005 for v in f.verts)
    door_faces = [f for f in bm.faces if in_door(f)]
    dbm = bmesh.new()
    vmap = {}
    L_door_mat = []
    for f in door_faces:
        vs = []
        for v in f.verts:
            if v not in vmap:
                vmap[v] = dbm.verts.new(v.co)
            vs.append(vmap[v])
        g = dbm.faces.new(vs); g.material_index = DM[names[f.material_index]]; g.smooth = True
    bmesh.ops.delete(bm, geom=door_faces, context='FACES')
    bmesh.ops.dissolve_degenerate(bm, dist=1e-5, edges=bm.edges)
    z_front_top = front[0][1]

    def clamp_gap(bm_, door_side):
        """Along the cut edges (the front, the B-pillar, the sill) the edge keeps at least the gap's half from the line."""
        s_ = 1 if door_side else -1
        for v in bm_.verts:
            if not any(e.is_boundary for e in v.link_edges) or v.co.y < 0.3:
                continue
            x, z = v.co.x, v.co.z
            xf_, xr_ = line_x(front, z), line_x(rear, min(z, rear[-1][1]))
            if z <= z_front_top + 0.002 and abs(x - xf_) < 0.008:
                v.co.x = min(x, xf_ - DOOR_GAP) if door_side else max(x, xf_ + DOOR_GAP)
            if abs(x - xr_) < 0.008 and z_sill - 0.01 < z < z_top:
                v.co.x = max(x, xr_ + DOOR_GAP) if door_side else min(x, xr_ - DOOR_GAP)
            if abs(z - z_sill) < 0.008 and xr_ - 0.01 < x < line_x(front, z_sill) + 0.01:
                v.co.z = max(z, z_sill + DOOR_GAP) if door_side else min(z, z_sill - DOOR_GAP)
    move_boundary(bm, DOOR_GAP)
    clamp_gap(bm, False)
    bm.to_mesh(body.data); bm.free()
    for p in body.data.polygons:
        p.use_smooth = True
    # the door: its gap, its inside
    move_boundary(dbm, DOOR_GAP)
    clamp_gap(dbm, True)
    dbm.normal_update()
    # (its outline and the gap across it, for the check that no ray leaves the cabin except through the gap)
    gap_quads = []
    for e in dbm.edges:
        if e.is_boundary:
            a, b = e.verts
            da, db = inward(a), inward(b)
            if da is not None and db is not None:
                gap_quads.append((a.co.copy(), b.co.copy(), b.co - db * 0.008, a.co - da * 0.008))
    outer_faces = [f for f in dbm.faces if f.material_index != DM['Glass']]
    zg = lambda x: st_at(st, x, 'zgb')
    # (the inner panel's edge 1.2 cm in from the outer edge: the door's edge leans in, clear of the opening's jamb)
    pull = {}
    for v in dbm.verts:
        if not any(e.is_boundary for e in v.link_edges):
            continue
        d = inward(v)
        if d is not None:
            pull[v] = d * 0.015
    new = offset_shell(dbm, outer_faces, lambda v: 0.045 if v.co.z < zg(v.co.x) + 0.004 else 0.022,
                 lambda f: DM['Interior'],
                 lambda e, f: DM['Trim'] if (len(e.link_faces) == 1 or any(g.material_index == DM['Glass'] for g in e.link_faces if g is not f)) else None,
                 nrm_of=lambda v: Vector((0, 1, 0)) if v.is_boundary else v.normal)
    for v, m in pull.items():
        if v in new:
            new[v].co += m
    # the inside stays within the door's outline (where the outer edges meet the sill and the waist the
    # offsets can lean past it)
    for v in new.values():
        lo_, hi_ = line_x(rear, min(v.co.z, rear[-1][1])) + 0.012, line_x(front, v.co.z) - 0.012
        if v.co.z < zg(v.co.x) + 0.02:
            v.co.x = min(max(v.co.x, lo_), hi_)
    bmesh.ops.dissolve_degenerate(dbm, dist=1e-5, edges=dbm.edges)
    # armrest and handle on the inner panel
    bvh = BVHTree.FromBMesh(dbm)
    H = C['H']
    xm = (line_x(front, H.z) + line_x(rear, H.z)) / 2
    loc, n_, i_, d_ = bvh.ray_cast(Vector((xm, 0.2, H.z + 0.16)), Vector((0, 1, 0)), 2.0)
    if loc is not None:
        blob(dbm, (xm - 0.05, loc.y - 0.03, H.z + 0.16), (0.17, 0.035, 0.024), e=3, nu=16, nv=8, mi=DM['Interior'])
        blob(dbm, (xm + 0.18, loc.y - 0.012, H.z + 0.26), (0.05, 0.012, 0.014), e=3, nu=10, nv=6, mi=DM['Chrome'])
    # the outer items (mirror, handle)
    if items is not None:
        tmp = bmesh.new(); tmp.from_mesh(items.data)
        off = len(dbm.verts)
        vm = [dbm.verts.new(v.co) for v in tmp.verts]
        for f in tmp.faces:
            g = dbm.faces.new([vm[v.index] for v in f.verts]); g.material_index = f.material_index
        tmp.free()
        bpy.data.objects.remove(items, do_unlink=True)
    # the hinge: a vertical axis at the door's front edge, just outside its widest point there
    xs_ = [v.co.x for v in dbm.verts]
    xmax = max(xs_)
    y_h = max(v.co.y for v in dbm.verts if v.co.x > xmax - 0.25 and v.co.z < zg(v.co.x)) + 0.004
    hinge = Vector((xmax + 0.003, y_h, 0.0))
    bmesh.ops.dissolve_degenerate(dbm, dist=1e-5, edges=dbm.edges)
    ob = obj_from_bm(dbm, key + '_door')
    return ob, hinge, gap_quads


# ---------------------------------------------------------------- one car
def clean_scene():
    sc = bpy.context.scene
    for o in list(sc.collection.all_objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for me in list(bpy.data.meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)


def build_body(key, level, arch_segs):
    global ARCH_SEGS
    S = SPECS[key]
    ARCH_SEGS = arch_segs
    ob, st = build_cage(key)
    subdivide(ob, level)
    body_materials(ob, '#9a1b1f')
    surf = Surface(ob)
    smooth = cut_arches(ob, S)
    smooth.hide_render = True; smooth.name = key + '_smooth'
    full = BVHTree.FromObject(ob, bpy.context.evaluated_depsgraph_get())
    ARCH_SEGS = 64
    me = ob.data; me.calc_loop_triangles()
    co = [v.co for v in me.vertices]
    ob['volume'] = sum(co[t.vertices[0]].dot(co[t.vertices[1]].cross(co[t.vertices[2]])) for t in me.loop_triangles) / 6
    return ob, st, surf, full, smooth


def build_car(key, wheel_fit):
    """The near model (body, details, cabin, the driver's door, one wheel) and the far one."""
    S = SPECS[key]
    LOW.update(patch=1.0, blob=1.0, strips_off=False)
    body, st, surf, full, smooth = build_body(key, 2, 64)
    det, items = add_details(key, surf, S, body, split_door=True)
    C = cabin_spec(key, S, st, wheel_fit)
    door, hinge, gap_quads = split_door(key, body, st, items, C)
    transfer_normals(body, smooth)                 # (again: the door's cut went through a mesh without them)
    cab = build_cabin(key, S, st, body, C, full)
    wh = wheel_object(S, key + '_wheel')
    # the far model: the cage subdivided once, lighter details and wheels, no door cut
    LOW.update(patch=0.34, blob=0.5, strips_off=True)
    b1, _, surf1, full1, smooth1 = build_body(key, 1, 32)
    b1.name = key + '_body_lo'
    det1, _ = add_details(key, surf1, S, b1, split_door=False)
    cab1 = build_cabin(key, S, st, b1, C, full1, lo=True)
    w1 = []
    xf, xr = axles(S); R = tyre_radius(S); t = S['track'] / 2
    for (x, sy) in ((xf, 1), (xr, 1), (xf, -1), (xr, -1)):
        bmw = wheel(S, segs=16, lo=True)
        me = bpy.data.meshes.new('w1'); bmw.to_mesh(me); bmw.free()
        o = bpy.data.objects.new('w1', me); bpy.context.scene.collection.objects.link(o)
        for nm_ in ('Rubber', 'Rim', 'DarkMetal', 'Caliper', 'Rim2'):
            o.data.materials.append(render_material(nm_))
        o.location = (x, sy * t, R); o.scale = (1, sy, 1)
        w1.append(o)
    LOW.update(patch=1.0, blob=1.0, strips_off=False)
    return dict(S=S, st=st, C=C, body=body, det=det, door=door, hinge=hinge, cab=cab, wheel=wh, surf=surf, gap_quads=gap_quads,
                lod1=[b1, det1, cab1] + w1, surf1=surf1, vol=body['volume'], vol1=b1['volume'])


# ---------------------------------------------------------------- the game's file
# The game's materials: Paint (each car its colour), Glass, Trim (everything else: an
# 8x8 atlas of colour, roughness and metalness; each face's texture coordinates are
# its cell's centre), Lamps (an 8x1 strip, one cell per function, which the game
# lights per car: daytime running lights, tail, brake, indicators, reverse) and
# Plate (where the game puts its number plates).
ATLAS = ['Trim', 'GlossBlack', 'Chrome', 'Rubber', 'Interior', 'Seat', 'Dash', 'Screen', 'Carpet', 'Headliner', 'Well', 'Rim',
         'DarkMetal', 'Caliper', 'Rim2', 'MirrorGlass', 'Grille']
LAMP_CELLS = ['DRL', 'Tail', 'Brake', 'IndL', 'IndR', 'Reverse', 'Lens', 'Reflector']
GAME_MATS = ['Paint', 'Glass', 'Trim', 'Lamps', 'Plate']


def game_mat(name):
    if name in ('Paint', 'Glass', 'Plate'):
        return GAME_MATS.index(name)
    return 3 if name in LAMP_CELLS else 2


def cell_uv(name):
    if name in LAMP_CELLS:
        return ((LAMP_CELLS.index(name) + 0.5) / 8, 0.5)
    i = ATLAS.index(name) if name in ATLAS else 0
    return (((i % 8) + 0.5) / 8, ((i // 8) + 0.5) / 8)


def game_materials():
    if bpy.data.materials.get('Trim') is not None:
        return [bpy.data.materials[n] for n in GAME_MATS]
    def img(name, w, h, cells, value, colorspace):
        im = bpy.data.images.new(name, w, h, alpha=True)
        px = np.ones((h, w, 4), np.float32)
        cw, ch = w // 8, max(1, h // 8)
        for i, n in enumerate(cells):                       # (rows from the bottom, as Blender's texture coordinates)
            r, c = i // 8, i % 8
            px[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw, :] = value(n)
        im.colorspace_settings.name = colorspace            # (first: changing it later regenerates the image, black)
        im.pixels = px.ravel().tolist()
        im.update(); im.pack()
        return im

    def hexv(h):
        h = h.lstrip('#')
        return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0]
    col = img('car_trim_color', 64, 64, ATLAS, lambda n: hexv(RENDER_LOOK[n][0]), 'sRGB')
    mr = img('car_trim_mr', 64, 64, ATLAS, lambda n: [1.0, RENDER_LOOK[n][1], RENDER_LOOK[n][2], 1.0], 'Non-Color')
    lamp = img('car_lamps', 8, 1, LAMP_CELLS, lambda n: hexv(RENDER_LOOK[n][0]), 'sRGB')
    out = []
    for n in GAME_MATS:
        m = bpy.data.materials.new(n); m.use_nodes = True
        nt = m.node_tree; b = nt.nodes['Principled BSDF']
        if n == 'Paint':
            b.inputs['Base Color'].default_value = srgb('#9a1b1f') + (1,); b.inputs['Metallic'].default_value = 0.45
            b.inputs['Roughness'].default_value = 0.32
        elif n == 'Glass':
            b.inputs['Base Color'].default_value = srgb('#0d1316') + (1,); b.inputs['Alpha'].default_value = 0.55
            b.inputs['Roughness'].default_value = 0.05
            m.surface_render_method = 'BLENDED'
        elif n == 'Plate':
            b.inputs['Base Color'].default_value = srgb('#f2c200') + (1,); b.inputs['Roughness'].default_value = 0.4
        else:
            t = nt.nodes.new('ShaderNodeTexImage'); t.image = col if n == 'Trim' else lamp; t.interpolation = 'Closest'
            nt.links.new(t.outputs['Color'], b.inputs['Base Color'])
            if n == 'Trim':
                t2 = nt.nodes.new('ShaderNodeTexImage'); t2.image = mr; t2.interpolation = 'Closest'
                sep = nt.nodes.new('ShaderNodeSeparateColor')
                nt.links.new(t2.outputs['Color'], sep.inputs['Color'])
                nt.links.new(sep.outputs['Green'], b.inputs['Roughness']); nt.links.new(sep.outputs['Blue'], b.inputs['Metallic'])
            else:
                b.inputs['Roughness'].default_value = 0.15; b.inputs['Metallic'].default_value = 0.0
        out.append(m)
    return out


def game_mesh(objs, name, offset=Vector()):
    """One mesh of the given objects (world placement applied, then shifted by -offset)
    with the game's materials and the atlas coordinates. (Joined copies: the panels'
    custom normals are kept.)"""
    sc = bpy.context.scene
    copies = []
    for o in objs:
        c = o.copy(); c.data = o.data.copy(); sc.collection.objects.link(c)
        mw = Matrix.Translation(-offset) @ o.matrix_world
        c.matrix_world = Matrix()
        c.data.transform(mw)
        if mw.determinant() < 0:
            c.data.flip_normals()
        for p in c.data.polygons:                    # each face's part, by its material's name
            pass
        names = [mname(m) for m in c.data.materials]
        idx = [p.material_index for p in c.data.polygons]
        part = [names[i] if i < len(names) else 'Trim' for i in idx]
        uvl = c.data.uv_layers.new(name='UVMap') if not c.data.uv_layers else c.data.uv_layers[0]
        uv = np.zeros((len(c.data.loops), 2), np.float32)
        for p, n_ in zip(c.data.polygons, part):
            uv[p.loop_start:p.loop_start + p.loop_total] = cell_uv(n_)
        uvl.data.foreach_set('uv', uv.ravel())
        c.data.materials.clear()
        for m in game_materials():
            c.data.materials.append(m)
        c.data.polygons.foreach_set('material_index', [game_mat(n_) for n_ in part])
        copies.append(c)
    with bpy.context.temp_override(active_object=copies[0], selected_editable_objects=copies, object=copies[0]):
        bpy.ops.object.join()
    ob = copies[0]
    other = bpy.data.objects.get(name)               # (the build's own object of that name steps aside)
    if other is not None and other is not ob:
        other.name = name + '_build'
    ob.name = name; ob.data.name = name
    assert ob.name == name, ob.name
    for k in list(ob.keys()):                         # (the build's own properties stay out of the game's file)
        del ob[k]
    sc.collection.objects.unlink(ob)
    ob.use_fake_user = True
    return ob


def game_objects(key, car):
    S = car['S']; C = car['C']; H = C['H']; hg = car['hinge']
    xf, xr = axles(S)
    body = game_mesh([car['body'], car['det'], car['cab']], key + '_body')
    door = game_mesh([car['door']], key + '_door', offset=hg)
    wheel = game_mesh([car['wheel']], key + '_wheel')
    lod1 = game_mesh(car['lod1'], key + '_lod1')
    # (game axes: x = the car's left, y up, z forward)
    body['wheels_front'] = round(xf, 4); body['wheels_rear'] = round(xr, 4)
    body['track'] = round(S['track'] / 2, 4); body['wheel_r'] = round(tyre_radius(S), 4)
    body['half_width'] = round(S['width'] / 2, 4); body['length'] = S['length']
    body['seat'] = [round(H.y, 4), round(H.z, 4), round(H.x, 4)]
    body['door_hinge'] = [round(hg.y, 4), 0.0, round(hg.x, 4)]
    body['door_open'] = round(DOOR_OPEN, 4)
    body['vmax'] = S['vmax']
    for k in ('mass', 'cog_h', 'drive', 'power_kw', 'torque_nm', 'gears', 'final_drive', 'bump', 'droop'):
        body[k] = S[k]
    return [body, door, wheel, lod1]


def export_glb(objs, path):
    sc = bpy.data.scenes.new('export')
    for o in objs:
        sc.collection.objects.link(o)
    win = bpy.context.window
    old = win.scene
    win.scene = sc
    bpy.ops.export_scene.gltf(filepath=path, export_format='GLB', use_active_scene=True, export_extras=True,
                              export_apply=False, export_yup=True, export_materials='EXPORT', export_normals=True,
                              export_texcoords=True, export_draco_mesh_compression_enable=False, export_animations=False,
                              export_skins=False, export_morph=False, export_cameras=False, export_lights=False)
    win.scene = old
    tris = {}
    for o in objs:
        me = o.data; me.calc_loop_triangles()
        tris[o.name] = dict(triangles=len(me.loop_triangles),
                            materials=sorted({GAME_MATS[p.material_index] for p in me.polygons}))
    return tris


# ---------------------------------------------------------------- checks
def bvh_of(objs, door_angle=None, hinge=None, pitch=0.0, roll=0.0):
    V, F = [], []
    rot = Matrix.Rotation(pitch, 4, 'Y') @ Matrix.Rotation(roll, 4, 'X')
    for o in objs:
        mw = o.matrix_world
        if door_angle is not None and o.name.endswith('_door'):
            mw = Matrix.Translation(hinge) @ Matrix.Rotation(-door_angle, 4, 'Z') @ Matrix.Translation(-hinge) @ mw
        mw = rot @ mw
        base = len(V)
        V += [(mw @ v.co) for v in o.data.vertices]
        F += [[base + i for i in p.vertices] for p in o.data.polygons]
    return BVHTree.FromPolygons(V, F)


def is_inside(bv, p):
    """Is p inside the (closed) surface: an odd number of crossings straight down."""
    n, o = 0, Vector(p)
    for _ in range(50):
        loc, nrm, i_, d = bv.ray_cast(o, Vector((0, 0, -1)), 5.0)
        if loc is None:
            break
        n += 1
        o = loc + Vector((0, 0, -1e-5))
    return n % 2 == 1


def check_car(key, car, drivers):
    S = car['S']; C = car['C']; R = tyre_radius(S); xf, xr = axles(S); out = {}; fails = []
    def verts(o):
        mw = o.matrix_world
        return np.array([(mw @ v.co)[:] for v in o.data.vertices])
    body_v = np.concatenate([verts(car['body']), verts(car['door'])])
    det_v = verts(car['det'])
    names = [mname(m) for m in car['det'].data.materials]
    # 1. dimensions
    no_mirror = det_v[np.abs(det_v[:, 1]) < S['width'] / 2 + 0.01]
    allv = np.concatenate([body_v, no_mirror])
    fin_x = DETAILS[key]['fin']['x']
    top = allv[(np.abs(allv[:, 0] - fin_x) > 0.1) | (np.abs(allv[:, 1]) > 0.03)][:, 2].max()   # (the antenna not counted)
    mid = body_v[(body_v[:, 0] < xf - R) & (body_v[:, 0] > xr + R)]
    dims = dict(length=float(allv[:, 0].max() - allv[:, 0].min()), width=float(-2 * body_v[:, 1].min()), height=float(top),
                front_overhang=float(allv[:, 0].max() - xf), rear_overhang=float(xr - allv[:, 0].min()),
                wheelbase=xf - xr, track=S['track'], clearance=float(mid[:, 2].min()))
    for k, v in dims.items():
        if abs(v - S[k]) > 0.01:
            fails.append('%s %.3f (spec %.3f)' % (k, v, S[k]))
    out['dimensions'] = {k: round(v, 4) for k, v in dims.items()}
    # 2. wheels: clear of the body at full lock and at the game's body roll and pitch
    tyre = np.array([v.co[:] for v in car['wheel'].data.vertices])
    tyre_mat = np.array([car['wheel'].data.polygons[i].material_index for i in range(len(car['wheel'].data.polygons))])
    t2 = S['track'] / 2
    worst, where = 9.0, None
    statics = [car['body'], car['det']]
    for bump in (0.0, S['bump']):                     # the wheel at rest and up at its bump stop (the game's suspension)
            bv = bvh_of(statics)
            for (x, sy, front) in ((xf, 1, True), (xr, 1, False), (xf, -1, True), (xr, -1, False)):
                for steer in (np.linspace(-STEER, STEER, 9) if front else [0.0]):
                    rs = Matrix.Rotation(steer, 3, 'Z')
                    for p in tyre[::3]:
                        q = rs @ Vector((p[0], sy * p[1], p[2])) + Vector((x, sy * t2, R + bump))
                        loc, n_, i_, d = bv.find_nearest(q, 0.2)
                        if loc is not None:
                            inside = d < 0.05 and is_inside(bv, q)
                            if (-d if inside else d) < worst:
                                worst = -d if inside else d
                                where = dict(axle=round(x, 3), side=sy, steer=round(float(steer), 2), bump=bump,
                                             point=[round(v, 3) for v in q], body=[round(v, 3) for v in loc])
    out['tyre_clearance_min'] = round(worst, 4); out['tyre_clearance_where'] = where
    if worst < 0.015:
        fails.append('tyre clearance %.3f' % worst)
    outer = t2 + tyre[:, 1].max()
    out['tyre_outer_vs_body'] = round(outer - S['width'] / 2, 4)
    if outer > S['width'] / 2 + 0.005:
        fails.append('tyre outside the body by %.3f' % (outer - S['width'] / 2))
    # 3. the mesh: the details sit on the body (not in it, not floating); rays from the cabin always hit something
    mesh_info = {}
    for o in (car['body'], car['det'], car['cab'], car['door']):
        me = o.data
        small = sum(1 for p in me.polygons if p.area < 1e-9)
        mesh_info[o.name] = dict(faces=len(me.polygons), degenerate=small)
        if small:
            fails.append('%s: %d degenerate faces' % (o.name, small))
    out['mesh'] = mesh_info
    every = bvh_of([car['body'], car['det'], car['cab'], car['door']])
    V_, F_ = [], []
    for q in car['gap_quads']:
        F_.append(list(range(len(V_), len(V_) + 4))); V_ += list(q)
    for o in (car['body'], car['det'], car['cab'], car['door']):
        base = len(V_); V_ += [v.co.copy() for v in o.data.vertices]; F_ += [[base + i for i in p.vertices] for p in o.data.polygons]
    sealed = BVHTree.FromPolygons(V_, F_)            # (the door's 4 mm gap closed, as its seals would)
    H = C['H']
    holes = 0
    rng = np.random.default_rng(1)
    dirs = rng.normal(size=(3000, 3)); dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    for origin in (H + Vector((0, 0, 0.55)), Vector((H.x - 0.9, 0, H.z + 0.4)), Vector((H.x + 0.3, -0.36, H.z + 0.3))):
        for d in dirs:
            loc, n_, i_, dist = sealed.ray_cast(origin, Vector(d), 5.0)
            if loc is None:
                holes += 1
                out.setdefault('cabin_ray_leaves', []).append(dict(origin=[round(v, 3) for v in origin], dir=[round(v, 3) for v in d]))
    out['cabin_rays_missing'] = holes
    if holes:
        fails.append('%d rays from the cabin leave the car' % holes)
    # 4. the door: its gap, and its swing to 65 degrees clear of the body
    hg = car['hinge']
    dv = verts(car['door'])
    bb = BVHTree.FromObject(car['body'], bpy.context.evaluated_depsgraph_get())
    gaps = []
    dme = car['door'].data
    edge_count = {}
    for p in dme.polygons:
        for e in p.edge_keys:
            edge_count[e] = edge_count.get(e, 0) + 1
    bverts = {v for e, n in edge_count.items() if n == 1 for v in e}
    for vi in list(bverts)[::4]:
        co = dme.vertices[vi].co
        loc, n_, i_, d = bb.find_nearest(co, 0.05)
        if loc is not None and co.y > 0.5:
            gaps.append((d, co.copy(), loc.copy()))
    gl = np.array([g[0] for g in gaps])
    out['door_gap'] = dict(p2=round(float(np.percentile(gl, 2)), 4), median=round(float(np.median(gl)), 4))
    out['door_gap_smallest'] = [dict(gap=round(g, 4), at=[round(v, 3) for v in c], body=[round(v, 4) for v in l]) for g, c, l in sorted(gaps, key=lambda t: t[0])[:6]]
    if not (0.003 <= out['door_gap']['median'] <= 0.006) or out['door_gap']['p2'] < 0.0025:
        fails.append('door gap %s' % out['door_gap'])
    hit = 0
    parts_ = {n: bvh_of([car[n]]) for n in ('body', 'det', 'cab')}
    prev = None
    sample = dv[::5]
    for a in np.linspace(0, DOOR_OPEN, 27):
        M = Matrix.Translation(hg) @ Matrix.Rotation(-a, 4, 'Z') @ Matrix.Translation(-hg)
        cur = [M @ Vector(p) for p in sample]
        if prev is not None and a > math.radians(3):
            for p0, p1 in zip(prev, cur):
                d = p1 - p0
                if d.length < 1e-6:
                    continue
                for pn, bvp in parts_.items():
                    loc, n_, i_, dist = bvp.ray_cast(p0, d.normalized(), d.length)
                    if loc is not None:
                        hit += 1
                        out.setdefault('door_sweep_where', []).append(dict(part=pn, deg=round(math.degrees(a), 1),
                                                                           door_point=[round(v, 3) for v in p0], at=[round(v, 3) for v in loc]))
        prev = cur
    out['door_sweep_hits'] = hit
    if hit:
        fails.append('the door hits the body %d times on its way open' % hit)
    # 5. the drivers: head under the roof, nothing through the dashboard, door, roof or wheel, hands on the rim
    W_ = C['wheel']; wc = H + W_['c']; wn = W_['n']
    parts = {'dashboard': [], 'wheel': [], 'roof': [], 'door': []}
    cm = car['cab'].data
    cnames = [mname(m) for m in cm.materials]
    # split the cabin's faces into parts by material (dash, wheel) and the roof's inner skin; the door is its own
    def sub_bvh(o, pick):
        V = [v.co.copy() for v in o.data.vertices]
        F = [list(p.vertices) for p in o.data.polygons if pick(p)]
        return BVHTree.FromPolygons(V, F) if F else None
    bv_parts = dict(
        dashboard=sub_bvh(car['cab'], lambda p: cnames[p.material_index] in ('Dash', 'Screen') and p.center.x > H.x + 0.4),
        wheel=sub_bvh(car['cab'], lambda p: cnames[p.material_index] in ('Rubber', 'DarkMetal') and (p.center - wc).length < W_['r'] + 0.05),
        roof=sub_bvh(car['cab'], lambda p: cnames[p.material_index] == 'Headliner'),
        door=sub_bvh(car['door'], lambda p: mname(car['door'].data.materials[p.material_index]) in ('Interior', 'Chrome')))
    drv = {}
    for cid, d in drivers.items():
        P = d['pts'] + np.array(H[:])
        Q = P[::3]; hand = d['hand'][::3]
        r = dict()
        for part, bvp in bv_parts.items():
            if bvp is None:
                continue
            pen = 0.0
            for p, hd in zip(Q, hand):
                if part == 'wheel' and hd:
                    continue
                loc, n_, i_, dist = bvp.find_nearest(Vector(p), 0.08)
                if loc is not None and (Vector(p) - loc).dot(n_) < 0:
                    pen = max(pen, dist)
            r[part + '_penetration'] = round(pen, 4)
            if pen > 0.003:
                fails.append('%s: %s %.3f m into the %s' % (cid, 'body', pen, part))
        # head room: from the head's top straight up to the headliner
        ht = P[P[:, 2] > P[:, 2].max() - 0.02]
        room = 9.0
        for p in ht[::5]:
            loc, n_, i_, dist = every.ray_cast(Vector(p), Vector((0, 0, 1)), 1.0)
            if loc is not None:
                room = min(room, dist)
        r['head_room'] = round(room, 4)
        if room < 0.03:
            fails.append('%s: head room %.3f' % (cid, room))
        # hands to the rim
        h = P[d['hand']]
        far = 0.0
        for sg in (1, -1):
            hc = Vector(h[(h[:, 1] - H.y) * sg > 0].mean(0))
            v = hc - wc; vn = v.dot(wn); vp = (v - wn * vn).length
            far = max(far, math.hypot(vn, vp - W_['r']) - W_['tube'])
        r['hands_to_rim'] = round(far, 4)
        if far > 0.06:
            fails.append('%s: a hand %.3f m from the wheel' % (cid, far))
        drv[cid] = r
    out['drivers'] = drv
    # 6. lamps: every function, on its side and facing its way
    lamp = {}
    for o in (car['det'], car['door']):
        nm_ = [mname(m) for m in o.data.materials]
        mw = o.matrix_world
        for p in o.data.polygons:
            n = nm_[p.material_index]
            if n in ('DRL', 'Tail', 'Brake', 'IndL', 'IndR', 'Reverse'):
                c = mw @ p.center
                lamp.setdefault(n, []).append((c.x, c.y, c.z))
    lamps = {}
    for n in ('DRL', 'Tail', 'Brake', 'IndL', 'IndR', 'Reverse'):
        A_ = np.array(lamp.get(n, []))
        if not len(A_):
            fails.append('no %s faces' % n); continue
        lamps[n] = dict(faces=len(A_), front=int((A_[:, 0] > 0).sum()), rear=int((A_[:, 0] < 0).sum()),
                        left=int((A_[:, 1] > 0).sum()), right=int((A_[:, 1] < 0).sum()))
    if 'IndL' in lamps and lamps['IndL']['right']:
        fails.append('left indicator faces on the right')
    if 'IndR' in lamps and lamps['IndR']['left']:
        fails.append('right indicator faces on the left')
    for n, want in (('IndL', ('front', 'rear')), ('IndR', ('front', 'rear')), ('DRL', ('front',)), ('Brake', ('rear',)),
                    ('Tail', ('rear',)), ('Reverse', ('rear',))):
        for w in want:
            if n in lamps and not lamps[n][w]:
                fails.append('%s missing at the %s' % (n, w))
    for n in ('DRL',):
        if n in lamps and lamps[n]['rear']:
            fails.append('%s at the rear' % n)
    for n in ('Brake', 'Tail', 'Reverse'):
        if n in lamps and lamps[n]['front']:
            fails.append('%s at the front' % n)
    mirror_ind = [c for c in lamp.get('IndL', []) if abs(c[1]) > S['width'] / 2]
    out['mirror_indicator_left'] = len(mirror_ind)
    if not mirror_ind:
        fails.append('no indicator on the left mirror')
    out['lamps'] = lamps
    # 7. the outer shell faces out (a positive volume), the details lie just on it, the cabin stays inside it
    out['volume'] = dict(near=round(car['vol'], 3), far=round(car['vol1'], 3))
    if car['vol'] <= 0 or car['vol1'] <= 0:
        fails.append('the body is inside out (volume %s)' % out['volume'])
    def shell_bvh(objs, door=None):
        V, F = [], []
        for o in objs:
            base = len(V); V += [o.matrix_world @ v.co for v in o.data.vertices]
            F += [[base + i for i in p.vertices] for p in o.data.polygons]
        if door is not None:                          # (the door's outer skin and glass: its inside faces the cabin)
            nm_ = [mname(m) for m in door.data.materials]
            base = len(V); V += [door.matrix_world @ v.co for v in door.data.vertices]
            F += [[base + i for i in p.vertices] for p in door.data.polygons if nm_[p.material_index] in ('Paint', 'Glass')]
        return BVHTree.FromPolygons(V, F)
    def outside(cab, shell):
        """Cabin points outside the shell by more than 2 mm (rays both ways along x and down: odd crossings inside);
        the seals' rims and lips, which are built on the openings' edges, are not counted."""
        worst, n_out, at = 0.0, 0, None
        mw = cab.matrix_world
        nm_ = [mname(m) for m in cab.data.materials]
        pts = set()
        for p_ in cab.data.polygons:
            if nm_[p_.material_index] != 'Trim':
                pts.update(p_.vertices)
        for vi in sorted(pts)[::2]:
            p = mw @ cab.data.vertices[vi].co
            loc, nrm, i_, d = shell.find_nearest(p, 0.5)
            if loc is None or d < 0.002:
                continue
            votes = 0
            for dirv in (Vector((0, 0, -1)), Vector((1, 0, 0)), Vector((-1, 0, 0))):
                n, o = 0, p.copy()
                for _ in range(60):
                    hit, nn, ii, dd = shell.ray_cast(o, dirv, 9.0)
                    if hit is None:
                        break
                    n += 1; o = hit + dirv * 1e-5
                votes += n % 2
            if votes < 2:
                n_out += 1
                if d > worst:
                    worst, at = d, [round(q, 3) for q in p]
        return n_out, round(worst, 4), at
    n1, w1_, a1 = outside(car['cab'], shell_bvh([car['body']], car['door']))
    b1, det1, cab1 = car['lod1'][:3]
    n2, w2_, a2 = outside(cab1, shell_bvh([b1]))
    out['cabin_outside_body'] = dict(near=n1, near_worst=w1_, near_at=a1, far=n2, far_worst=w2_, far_at=a2)
    if n1 or n2:
        fails.append('cabin outside the body: %s' % out['cabin_outside_body'])
    def on_surface(det, surf):
        att = det.data.attributes.get('kind')
        if att is None:
            return None
        vals = np.zeros(len(det.data.vertices), np.int32); att.data.foreach_get('value', vals)
        lo_, hi_, bad = 9.0, 0.0, 0
        for v, k in zip(det.data.vertices, vals):
            if k != 1:
                continue
            loc, nrm, i_, d = surf.bvh.find_nearest(v.co, 0.05)
            d = 9.0 if loc is None else d
            lo_, hi_ = min(lo_, d), max(hi_, d)
            if not (0.0008 <= d <= 0.0065):
                bad += 1
        return dict(min=round(lo_, 4), max=round(hi_, 4), off=bad)
    out['details_on_body'] = dict(near=on_surface(car['det'], car['surf']), far=on_surface(det1, car['surf1']))
    for k, r in out['details_on_body'].items():
        if r and r['off']:
            fails.append('%s details off the body: %s' % (k, r))
    out['wheel_fit'] = dict(centre_from_hip=[round(v, 3) for v in W_['c']], tilt_deg=round(W_['tilt'], 1), radius=W_['r'],
                            fit_err=round(W_['fit_err'], 4))
    out['seat_hip'] = [round(v, 4) for v in H]
    out['fails'] = fails
    out['ok'] = not fails
    return out




# ---------------------------------------------------------------- studio
def studio_car(key, car, drivers, door_angle=0.0):
    """The near model in the studio: four wheels, the door at an angle, the player at the wheel."""
    S = car['S']; xf, xr = axles(S); R = tyre_radius(S); t = S['track'] / 2
    for i, (x, sy) in enumerate(((xf, 1), (xr, 1), (xf, -1), (xr, -1))):
        o = car['wheel'] if i == 0 else car['wheel'].copy()
        if i:
            bpy.context.scene.collection.objects.link(o)
        o.location = (x, sy * t, R); o.scale = (1, sy, 1)
    hg = car['hinge']
    car['door'].matrix_world = Matrix.Translation(hg) @ Matrix.Rotation(-door_angle, 4, 'Z') @ Matrix.Translation(-hg)
    if 'player' in drivers and 'driver_ob' not in car:
        o = bpy.data.objects.new(key + '_driver', drivers['player']['mesh']); bpy.context.scene.collection.objects.link(o)
        o.location = car['C']['H']
        m = new_principled('Driver', '#8a6a55', 0.6)
        o.data.materials.clear(); o.data.materials.append(m)
        car['driver_ob'] = o
    for o in car['lod1']:
        o.hide_render = True
    for n in os.environ.get('CARS_HIDE', '').split(','):
        if n and n in car:
            car[n].hide_render = True


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    drivers = measure_drivers()
    wheel_fit = fit_wheel(drivers)
    log('steering wheel: centre %s from the hip joint, tilt %.0f, radius %.3f, the hands within %.3f of the rim' %
        (tuple(round(v, 3) for v in wheel_fit['c']), wheel_fit['tilt'], wheel_fit['r'], wheel_fit['fit_err']))
    keys = ONLY or list(SPECS)
    exports, report = [], {}
    for key in keys:
        clean_scene()
        car = build_car(key, wheel_fit)
        rep = check_car(key, car, drivers)
        log(key, json.dumps({k: rep[k] for k in ('dimensions', 'tyre_clearance_min', 'door_gap', 'door_sweep_hits', 'cabin_rays_missing')}))
        log(key, 'drivers', json.dumps(rep['drivers']))
        log(key, 'FAILS' if rep['fails'] else 'ok', rep['fails'])
        exports += game_objects(key, car)
        report[key] = rep
        if RENDERS:
            os.makedirs(RENDERS, exist_ok=True)
            cam = studio()
            studio_car(key, car, drivers)
            views = VIEWS_EXTRA if os.environ.get('CARS_EXTRA_ONLY') else VIEWS + VIEWS_EXTRA
            H = car['C']['H']
            for n, loc, tgt, lens in views:
                shoot(cam, '%s_%s' % (key, n), loc, tgt, lens)
            shoot(cam, key + '_interior', (H.x + 0.25, 1.75, H.z + 0.85), (H.x + 0.35, 0.1, H.z + 0.35), 26)
            shoot(cam, key + '_wheel', (axles(car['S'])[0] + 0.55, 1.9, 0.55), (axles(car['S'])[0], 0.8, 0.33), 50)
            studio_car(key, car, drivers, door_angle=DOOR_OPEN)
            shoot(cam, key + '_door', (H.x + 2.6, 3.6, 1.6), (H.x + 0.3, 0.6, 0.8), 35)
            car['door'].matrix_world = Matrix()
            for o in car['lod1']:                           # the far model beside the near one
                o.hide_render = False
                o.location.y -= 2.4
            shoot(cam, key + '_lod', (7.5, 3.5, 2.2), (0, -1.2, 0.6), 40)
    os.makedirs(OUTDIR, exist_ok=True)
    tris = export_glb(exports, os.path.join(OUTDIR, 'cars.glb'))
    for key in keys:
        report[key]['game_triangles'] = {k: v for k, v in tris.items() if k.startswith(key + '_')}
    report['ok'] = all(report[k]['ok'] for k in keys)
    json.dump(report, open(os.path.join(OUTDIR, 'cars_report.json'), 'w'), indent=1, ensure_ascii=False)
    log('wrote', os.path.join(OUTDIR, 'cars.glb'), '%.2f MB' % (os.path.getsize(os.path.join(OUTDIR, 'cars.glb')) / 1e6),
        'ok' if report['ok'] else 'WITH FAILS')


main()
