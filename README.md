# Sunset Bay

A low-poly open-city game that runs in the browser (desktop and phone).

Play: open `index.html`, or the GitHub Pages link of this repository.

## Sunset Bay, the white city (`city/`)

A second game built entirely from Blender models, in Hebrew. Open `city/` on the GitHub Pages link. It's a Mediterranean
city by the sea on a sunny day:
- **The city:** white Bauhaus buildings (long balconies with rounded ends, ribbon windows, shutters, AC units, solar
  water heaters, shops with Hebrew signs), white classic buildings, and a district of glass towers, with more towers on
  the skyline.
- **The seafront:** a promenade with palms and benches, a beach with umbrellas, and the sea.
- **The streets:** palms and trees, lamps, crosswalks and traffic lights.
- **Cars and people:** Hyundai i20s and sedans with Israeli number plates, and people. Traffic stops at red lights,
  pedestrians cross with the lights, and you can walk, take any car, and do delivery and taxi jobs (paid in shekels).

The assets come from `blender/export_city_assets.py` (`city/assets/city.glb` and the textures in `city/assets/tex/`)
and `blender/export_game_assets.py` (`city/assets/actors.glb`). Like the cars and people in the first game, they load
from a web server: to play locally, run `python3 -m http.server` in the repository and open `http://localhost:8000/city/`.

On desktop the cars and people are detailed models made in Blender (`assets/sunset_bay.glb`, built by
`blender/export_game_assets.py`): a Hyundai i20 (the SUVs), a sedan, and six people who walk, run and aim.
They load from the web server, so to see them locally serve the folder (for example `python3 -m http.server`)
rather than opening the file directly. On phones, or if the models can't load, the game uses its simple models.
