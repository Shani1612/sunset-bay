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

Tested with Blender 5.1. The rendered shots are in `renders/`.
