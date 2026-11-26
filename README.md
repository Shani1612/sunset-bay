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

## The player in the white city (`city/assets/player.glb`)

The dressed character is the player in `city/`. `blender/export_player.py` makes
the game version: the shoes are lighter, the hidden teeth are removed, the
textures are 1024 px, and the cloth is single-layer and double-sided.

It adds five animations on the skeleton:

- **Idle:** breathing and a slight weight shift, with the arms hanging straight
  beside the body.
- **Walk:** 1.34 m per 0.93 s cycle. The legs are solved with IK, so each
  standing foot stays planted. The arms stay straight and swing.
- **Run:** 2.4 m per 0.6 s, with a flight phase. The arms stay straight and
  swing.
- **Drive:** sitting, with hands on the wheel.
- **Jump.**

With the arms down, the inner arm rests against the shirt's side. A shape key on
the shirt, `ArmsDown`, presses the cloth under the arms in, just enough to clear
the arm and the sleeve in every frame of Idle, Walk and Run. The game turns it
on with those animations; driving and jumping hold the arms away.

It also adds cloth bones for the back of the shirt's hem, the sleeves and the
jeans legs. Each animation has the cloth swing built in. In the game, springs on
these bones also react to starting, stopping and turning. Each spring is held
within its own limits: the back of the hem swings back freely but hardly forward
(the body is there), and the sleeves and the jeans legs only sway (an arm or a
leg is inside).

The front of the hem rests on the thighs. Two hem bones at the hips carry it up
over a thigh that comes forward; over a thigh that goes back it just hangs.

Every frame of every animation is checked for skin through the clothes and for
the shirt through the jeans, and so is every spring at its limits. The checks
use the same 4 strongest bones per vertex that glTF keeps. The results are in
`player_report.json`:

- 0 problems standing, walking, running and jumping, and with the springs at
  their limits.
- When driving, the shirt overlaps the jeans at the lap. That part is hidden by
  the car's door.

```
blender -b -P blender/export_player.py -- characters/human_dressed.blend city/assets/player.glb
```

How the game plays it:

- It blends Idle, Walk and Run by speed: 1.75 m/s walking, 4.4 m/s with Shift
  or the run button.
- It scales the cycles to the speed, so the feet don't slide.
- It switches to Drive in a car and to Jump in the air.
- It sets `ArmsDown` to the weight of Idle, Walk and Run.
- If `player.glb` can't load, the game uses its simple figure.

`city/build_page.py` makes the published page.
