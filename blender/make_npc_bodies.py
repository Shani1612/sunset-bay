"""The city's NPCs: realistic bodies from MB-Lab, one per character below.

    blender -b -P blender/make_npc_bodies.py -- <MB-Lab checkout> characters/npc [ids...]

MB-Lab (https://github.com/animate1978/MB-Lab, code GPL3, data AGPL3, the same
source as characters/Human.glb) is run without its interface: for each
character it builds the template (female or male), applies a phenotype (face
and build of a population) merged with a body type, sets age, mass and tone,
finalizes it (skeleton, weights), and exports it as <id>.glb in the same form
as Human.glb, ready for dress_character.py:

  * the mesh <id> and the armature <id>_skeleton (MB-Lab's 71 bones, A-pose),
  * the final shape baked in (no shape keys; no subdivision, displacement or
    corrective smoothing),
  * the MB-Lab material names, each with a plain PBR material that keeps its
    texture (glTF can't read MB-Lab's shader groups): the skin gets its
    population's albedo.
"""
import bpy, sys, os, re, json, time
import numpy as np

A = sys.argv[sys.argv.index('--') + 1:]
MBLAB, OUT = os.path.abspath(A[0]), os.path.abspath(A[1])
ONLY = set(A[2:])

# id: (template character, phenotype, body type, age, mass, tone)
# (age -1..1 is about 18..80 years; mass and tone -1..1)
NPCS = {
    'f_ca01': ('f_ca01', 'mediterranid', 'type_normobody', -0.35, 0.0, 0.1),
    'f_af01': ('f_af01', 'african', 'type_common03', -0.25, 0.05, 0.1),
    'f_as01': ('f_as01', 'east_asian', 'type_lightbody', -0.5, -0.1, 0.0),
    'f_la01': ('f_la01', 'south_american', 'type_common02', 0.05, 0.0, 0.0),
    'm_af01': ('m_af01', 'african', 'type_common01', -0.3, 0.0, 0.3),
    'm_as01': ('m_as01', 'east_asian', 'type_lightbody', -0.5, -0.1, 0.1),
    'm_ca01': ('m_ca01', 'nordid', 'type_common01', 0.0, 0.0, 0.2),
    'm_la01': ('m_la01', 'central_american', 'type_common03', 0.45, 0.0, -0.1),
}


def log(*a):
    print('[npc]', *a, flush=True)


sys.path.insert(0, os.path.dirname(MBLAB))
pkg = os.path.basename(MBLAB)
if not pkg.isidentifier():                       # the checkout's folder name must be importable
    link = os.path.join(OUT, '_mblab'); os.makedirs(link, exist_ok=True)
    if not os.path.exists(os.path.join(link, 'MB_Lab')):
        os.symlink(MBLAB, os.path.join(link, 'MB_Lab'))
    sys.path.insert(0, link); pkg = 'MB_Lab'
bpy.ops.wm.read_factory_settings(use_empty=True)
MB = __import__(pkg)
from importlib import import_module
# (MB-Lab's "remove censors" reads the add-on's preferences, which exist only
#  when it's installed through Blender's add-on manager; the censor patches are
#  covered by the clothes anyway)
import_module(pkg + '.algorithms').remove_censors = lambda: None
MB.register()
DATA = os.path.join(MBLAB, 'data')


def strip_stamp(name):
    """MBLab_skin31795687871.5206227 -> MBLab_skin3 (the stamp is a 10-digit time)."""
    return re.sub(r'\d{10}\.\d+$', '', name)


def simple_material(m):
    """A Principled BSDF that keeps the material's own colour texture (and the
    UV map it reads), which the glTF exporter can follow."""
    nt = m.node_tree
    tex = None
    if nt:
        imgs = [n for n in nt.nodes if n.type == 'TEX_IMAGE' and n.image]
        pref = [n for n in imgs if any(k in n.image.name for k in ('albedo', 'color', 'eyelash', 'teeth', 'nails'))
                and not any(k in n.image.name for k in ('bump', 'iris_bump'))]
        tex = (pref or imgs or [None])[0]
    img = tex.image if tex else None
    uv = None
    if tex and tex.inputs['Vector'].links:
        src = tex.inputs['Vector'].links[0].from_node
        if src.type == 'UVMAP':
            uv = src.uv_map
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    b = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(b.outputs['BSDF'], out.inputs['Surface'])
    if img is not None:
        t = nt.nodes.new('ShaderNodeTexImage'); t.image = img
        nt.links.new(t.outputs['Color'], b.inputs['Base Color'])
        if uv:
            u = nt.nodes.new('ShaderNodeUVMap'); u.uv_map = uv
            nt.links.new(u.outputs['UV'], t.inputs['Vector'])
    b.inputs['Metallic'].default_value = 0.0
    return img.name if img else None


def build(nid):
    tmpl, ethnic, preset, age, mass, tone = NPCS[nid]
    sex = 'female' if tmpl.startswith('f_') else 'male'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scn = bpy.context.scene
    scn.mblab_character_name = tmpl
    t0 = time.time()
    r = bpy.ops.mbast.init_character()
    assert r == {'FINISHED'} and MB.gui_status == 'ACTIVE_SESSION', (r, MB.gui_status, MB.gui_err_msg)
    H = MB.mblab_humanoid
    # the phenotype (face and build) with the body type's proportions on top;
    # load_character without mixing resets every morph it isn't given, so the
    # two are merged here and loaded once
    ph = json.load(open(os.path.join(DATA, 'phenotypes', tmpl + '_ptypes', ethnic + '.json')))['structural']
    pr = json.load(open(os.path.join(DATA, 'presets', 'human_%s_base' % sex, preset + '.json')))['structural']
    data = dict(ph); data.update(pr)
    H.load_character({'structural': data, 'metaproperties': {
        'character_age': age, 'character_mass': mass, 'character_tone': tone,
        'last_character_age': 0.0, 'last_character_mass': 0.0, 'last_character_tone': 0.0}})
    obj = H.get_object()
    obj.character_age = age; obj.character_mass = mass; obj.character_tone = tone
    r = bpy.ops.mbast.finalize_character()
    assert r == {'FINISHED'}, r
    body = next(o for o in bpy.data.objects if o.type == 'MESH' and o.parent and o.parent.type == 'ARMATURE')
    arm = body.parent
    for o in list(bpy.data.objects):
        if o not in (body, arm):
            bpy.data.objects.remove(o)
    # bake the final shape: the shape-key mix at rest, without the render modifiers
    for m in body.modifiers:
        m.show_viewport = (m.type == 'ARMATURE')
    for pb in arm.pose.bones:
        pb.matrix_basis.identity()
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    arm_mod = [m for m in body.modifiers if m.type == 'ARMATURE']
    for m in arm_mod:
        m.show_viewport = False
    dg = bpy.context.evaluated_depsgraph_get()
    ev = body.evaluated_get(dg); me2 = ev.to_mesh()
    co = np.empty(len(me2.vertices) * 3); me2.vertices.foreach_get('co', co)
    ev.to_mesh_clear()
    assert len(co) == len(body.data.vertices) * 3
    if body.data.shape_keys:
        body.shape_key_clear()
    body.data.vertices.foreach_set('co', co)
    body.data.update()
    for m in list(body.modifiers):
        if m.type != 'ARMATURE':
            body.modifiers.remove(m)
        else:
            m.show_viewport = True
    # names as in Human.glb
    body.name = body.data.name = nid
    arm.name = arm.data.name = nid + '_skeleton'
    texs = {}
    for m in body.data.materials:
        m.name = strip_stamp(m.name)
        texs[m.name] = simple_material(m)
    z = co.reshape(-1, 3)[:, 2]
    log(nid, 'height %.3f m' % (z.max() - z.min()), 'verts', len(body.data.vertices), 'in %.1f s' % (time.time() - t0))
    log('   textures', texs)
    bpy.ops.object.select_all(action='DESELECT')
    body.select_set(True); arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, nid + '.glb')
    bpy.ops.export_scene.gltf(filepath=path, export_format='GLB', use_selection=True, export_skins=True,
                              export_animations=False, export_morph=False, export_apply=False,
                              export_yup=True, export_image_format='AUTO')
    log('   exported', path, '%.1f MB' % (os.path.getsize(path) / 1e6))
    return dict(id=nid, sex=sex, template=tmpl, phenotype=ethnic, body_type=preset, age=age, mass=mass, tone=tone,
                height=round(float(z.max() - z.min()), 3))


info = {}
for nid in NPCS:
    if ONLY and nid not in ONLY:
        continue
    info[nid] = build(nid)
meta = os.path.join(OUT, 'npc_bodies.json')
old = json.load(open(meta)) if os.path.exists(meta) else {}
old.update(info)
json.dump(old, open(meta, 'w'), indent=1)
