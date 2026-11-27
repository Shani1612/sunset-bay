"""The game's list of the city's people, from export_player.py --npc's reports.

    python3 blender/npc_manifest.py city/assets/npc

writes city/assets/npc/npcs.json: per person (by file name) their sex, height,
hip height (for the car seat), the metres and seconds of their Walk and Run
cycles (the game times the animations to the walking speed) and hair colour
(linear RGB). A person whose report has failed checks is left out.
"""
import sys, os, json, glob

d = sys.argv[1] if len(sys.argv) > 1 else 'city/assets/npc'
# accepted, as for the player: at the wheel the thighs press the shirt's hem into the jeans' waist, under
# the car's door (city/index.html seats drivers inside the car body)
ACCEPTED = {('Drive', 'TShirt>Jeans')}
out = {}
for rp in sorted(glob.glob(os.path.join(d, '*_report.json'))):
    R = json.load(open(rp))
    if 'npc' not in R:
        continue
    nid = R['npc']['id']
    if not os.path.exists(os.path.join(d, nid + '.glb')):
        continue
    bad = []
    for table in ('checks', 'checks_lod1 (1 cm)'):
        for anim, found in R.get(table, {}).items():
            for k, v in found.items():
                if (anim, k) not in ACCEPTED:
                    bad.append('%s %s %s %s' % (table, anim, k, v))
    if bad:
        print('left out', nid, bad[:4])
        continue
    n = R['npc']
    out[nid] = {k: n[k] for k in ('sex', 'height', 'hip', 'walk', 'run', 'hair')}
json.dump(out, open(os.path.join(d, 'npcs.json'), 'w'), indent=1, sort_keys=True)
print('npcs.json:', len(out), 'people:', ', '.join(sorted(out)))
