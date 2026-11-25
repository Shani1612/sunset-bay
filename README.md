# Sunset Bay

A low-poly open-city game that runs in the browser (desktop and phone).

Play: open `index.html`, or the GitHub Pages link of this repository.

## Sunset Bay Downtown (`city/`)

A second, new game built entirely from the Blender scene: open `city/` on the GitHub Pages link. It's a downtown of
3 × 3 blocks inside a ring of buildings, made of the Blender buildings (brick, stone, plaster and concrete facades
with real window openings, frames, sills, cornices, balconies, AC units, lit shops with signs and awnings), streets
with markings, crosswalks and traffic lights, the street lamps, hydrants, bins and bollards, and the i20, the sedan and
the people. Traffic drives on lanes and stops at red lights, pedestrians walk around the blocks and cross with the
lights, and you can walk, take any car, and do delivery and taxi jobs.

The assets come from `blender/export_city_assets.py` (`city/assets/city.glb` and the textures in `city/assets/tex/`)
and `blender/export_game_assets.py` (`city/assets/actors.glb`). Like the cars and people in the first game, they load
from a web server: to play locally, run `python3 -m http.server` in the repository and open `http://localhost:8000/city/`.

On desktop the cars and people are detailed models made in Blender (`assets/sunset_bay.glb`, built by
`blender/export_game_assets.py`): a Hyundai i20 (the SUVs), a sedan, and six people who walk, run and aim.
They load from the web server, so to see them locally serve the folder (for example `python3 -m http.server`)
rather than opening the file directly. On phones, or if the models can't load, the game uses its simple models.
