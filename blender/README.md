# Sunset Bay in Blender

`sunset_bay_city.py` builds a realistic version of a Sunset Bay street in Blender, entirely from code, and renders it with Cycles.

**What it builds:**
- Buildings with real window openings (reveals, frames, mullions, glass, some windows lit), stone sills, lintels and keystones, string courses, cornices with dentils, parapets, balconies with railings, window AC units, and rooftop plant and water towers.
- Shopfronts with lit windows, awnings and signs.
- Cars with smooth subdivided bodies, wheel arches cut with booleans, glass greenhouse, mirrors, head and tail lights, rounded tyres, five-spoke rims, brake discs and calipers, and clear-coated metallic paint.
- The street: asphalt, road markings and a crosswalk, curbs, paved sidewalks, street lamps with real spot lights, hydrants and bins.
- A physical sunset sky with a matching sun.

## Render

```
blender --background --python sunset_bay_city.py -- renders            # all shots, 1600×900
blender --background --python sunset_bay_city.py -- renders --quick    # fast preview, 960×540
blender --background --python sunset_bay_city.py -- renders --only 02_hero_car_front
blender --background --python sunset_bay_city.py -- renders --blend city.blend   # also save the scene
```

It also runs with the `bpy` Python module (`pip install bpy`): `python sunset_bay_city.py -- renders`.

Tested with Blender 5.1. The rendered shots are in `renders/`.
