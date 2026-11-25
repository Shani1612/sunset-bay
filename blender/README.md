# Sunset Bay in Blender

`sunset_bay_city.py` builds a realistic version of a Sunset Bay street in Blender, entirely from code, and renders it with Cycles.

**What it builds:**
- Buildings with real window openings (reveals, frames, mullions, glass, some windows lit), stone sills, lintels and keystones, string courses, cornices with dentils, parapets, balconies with railings, window AC units, and rooftop plant and water towers.
- Shopfronts with lit windows, awnings and signs.
- Two car models:
  - A **Hyundai i20 Active** (2015–2020), built to its published size (4.0 × 1.76 × 1.555 m, 2.57 m wheelbase, 195/55 R16). It has black arch and sill cladding, skid plates, roof rails, a spoiler, black B- and C-pillars, a hexagonal grille, fog lamps, swept headlights and wrap-around tail lights (all unlit, with clear glass), and a full interior.
  - A generic sedan with the same level of detail.
- People: men and women of different heights, builds and skin tones, built from a skeleton (Skin modifier + subdivision), wearing a T-shirt or long-sleeved top, jeans or trousers and shoes. They have a modelled head (jaw, nose, eyes with lids, eyebrows, lips, ears), short, long or tied-up hair, and stand, walk or look at a phone. About 30 of them are on the street: on the sidewalks, at the shop windows and on the crosswalk.
- The street: asphalt, road markings and a crosswalk, curbs, paved sidewalks, street lamps with real spot lights, hydrants and bins.
- A physical sunset sky with a matching sun.

## Render

```
blender --background --python sunset_bay_city.py -- renders            # all shots, 1600×900
blender --background --python sunset_bay_city.py -- renders --quick    # fast preview, 960×540
blender --background --python sunset_bay_city.py -- renders --only 02_hero_car_front
blender --background --python sunset_bay_city.py -- renders --blend city.blend   # also save the scene
blender --background --python sunset_bay_city.py -- renders --carstudio i20  # the i20 alone, ten studio shots
blender --background --python sunset_bay_city.py -- renders --carstudio      # the sedan alone
blender --background --python sunset_bay_city.py -- renders --peoplestudio   # six people and close-ups
```

It also runs with the `bpy` Python module (`pip install bpy`): `python sunset_bay_city.py -- renders`.

## City for the new game

```
blender --background --python export_city_assets.py      # or: python export_city_assets.py
```

writes `../city/assets/city.glb` and `../city/assets/tex/`, for the new game in `../city/`:
- **Textures:** the procedural brick, stone, plaster, concrete, roof, asphalt, paving and curb materials, baked to seamless tiles. The noise runs on a torus, so the edges meet. There's an albedo and a normal map for each.
- **20 building variants:** each comes in full detail and in a light version with plain windows, for distance.
- **Props:** the street lamp, hydrant, bin, bollard, manhole and a traffic signal.

Everything is Draco-compressed (under 1 MB).

## Game models

```
blender --background --python export_game_assets.py      # or: python export_game_assets.py
```

writes `../assets/sunset_bay.glb`, which the game (`index.html`) loads on desktop:
- **The i20 and the sedan:** body without wheels, about 15,000 triangles each, with simple materials (`Paint`, `Glass`, `Chrome`, `RedLens`, ...) so the game can repaint each car and light its brake lights. There is one wheel per car, centred on its axle.
- **Six people (three men, three women) in a neutral pose:** each is split at the shoulders and hips into torso, arms and legs, so the game can swing the limbs. A face goes to the bone it is nearest to relative to that bone's thickness. The node extras hold the joints, the hand positions and the inner cores, all in the game's axes.
- **Close-cropped hair:** exported as each vertex's height above the hairline, which the game's scalp shader turns into a sharp hairline.

Tested with Blender 5.1. The rendered shots are in `renders/`.
