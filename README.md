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

## Dressed character (`characters/`)

`characters/Human.glb` is an MB-Lab character. `blender/dress_character.py`
dresses it in a white T-shirt, jeans, socks and sneakers, rigged to its
skeleton:

- Each garment starts as a copy of the skin it covers, cut along smooth curves:
  crew neck, sleeve ends, hems, waist and the shoe collar. It is then moved off
  the body. It hangs straight below the chest and the knees, and it is smoothed,
  unfolded and pushed out until it keeps a minimum gap from the body and from
  the garment under it.
- The clothes keep the skin's bone weights:
  - the sleeve ends follow the upper arm,
  - the shirt hem follows the jeans,
  - the jeans hem rests on the shoes.
- Body faces under the clothes are hidden by a Mask modifier, so skin cannot
  show through in any pose.
- The script checks every garment in the rest pose and in a walking pose for
  three problems: skin through cloth, one garment through another, and folded
  cloth. The results are in `characters/human_dressed_report.json`, with 0
  problems in both poses.
- It also repairs body material values that were lost on the way to glTF: the
  skin was metallic, and the cornea, iris, pupil and nails had wrong values.
- It raises the rig by the sole thickness, so the shoes stand on z = 0.

```
blender -b -P blender/dress_character.py -- characters/Human.glb characters
blender -b -P blender/render_character.py -- characters/human_dressed.blend characters/renders
```

Outputs:

- `human_dressed.blend`, with procedural fabric materials:
  - denim with gold topstitching, back pockets and fading,
  - cotton jersey with a rib collar,
  - leather and suede sneakers.
- `human_dressed.glb`, for games: skinned, with flat PBR colours.
- `renders/`: studio renders, including the walking test pose.
