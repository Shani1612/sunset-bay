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
- **Cars and people:** a hatchback, a sedan and a crossover with Israeli number plates, and people. Traffic stops at
  red lights and signals before it turns, pedestrians cross with the lights, and you can walk, take any car, and do
  delivery and taxi jobs (paid in shekels).

The assets come from `blender/export_city_assets.py` (`city/assets/city.glb` and the textures in `city/assets/tex/`),
`blender/export_game_assets.py` (`city/assets/actors.glb`) and `blender/make_cars.py` (`city/assets/cars.glb`). Like the cars and people in the first game, they load
from a web server: to play locally, run `python3 -m http.server` in the repository and open `http://localhost:8000/city/`.

On desktop the cars and people are detailed models made in Blender (`assets/sunset_bay.glb`, built by
`blender/export_game_assets.py`): a Hyundai i20 (the SUVs), a sedan, and six people who walk, run and aim.
They load from the web server, so to see them locally serve the folder (for example `python3 -m http.server`)
rather than opening the file directly. On phones, or if the models can't load, the game uses its simple models.

## The city's cars (`city/assets/cars.glb`)

Three original cars, with no brand or model copied, built in Blender from code by `blender/make_cars.py`:

| | Hatchback (`hatch`) | Sedan (`sedan`) | Crossover (`suv`) |
|---|---|---|---|
| Length × width × height | 4.05 × 1.75 × 1.46 m | 4.65 × 1.80 × 1.45 m | 4.50 × 1.86 × 1.66 m |
| Wheelbase, track | 2.58, 1.52 m | 2.70, 1.56 m | 2.68, 1.60 m |
| Overhangs (front, rear) | 0.80, 0.67 m | 0.92, 1.03 m | 0.92, 0.90 m |
| Tyres | 195/55 R16 | 205/55 R16 | 225/60 R17 |
| Ground clearance | 0.16 m | 0.16 m | 0.20 m |
| Wheels | five double spokes | ten spokes | five wide spokes |

```
blender -b -P blender/make_cars.py -- city/assets [--only hatch,sedan,suv] [--renders DIR] [--quick]
```

- **The body** is a subdivision surface over a control cage: rings of points at stations along the car, from the
  middle of the underside up the side to the middle of the roof. The cage's rows are the body's lines (the sill, a
  character line, the shoulder, the windows' seals, the roof rail), as sharp creases. Each face knows its zone (hood,
  windscreen, window, pillar...), so glass, paint and black trim follow the window outlines exactly.
- **The wheel arches** are cut out with cylinders sized to the tyre, with a gap of 4.7–5.5 cm, and the wells are
  deep enough for the tyre at full lock. Around the cuts the panels keep the smooth body's normals.
- **The details** are drawn onto the body's surface by projection, just above it. Lamps, grilles and fog-lamp bezels
  are meshed along their outlines (their columns run from the outline's bottom to its top), so their edges are
  smooth curves, and so are the bands inside them (the light strip, the brake bar):
  - headlamps (a dark lens, a light strip, a projector, an amber indicator) and wrap-around tail lamps (tail,
    brake, indicator, reverse);
  - grilles, fog lamps, the bumpers' black parts, recesses for the number plates;
  - the panel gaps, the handles, the mirrors with their indicators, the wipers;
  - a spoiler, roof rails and arch trims for the crossover, and an antenna.
- **The cabin:** an inner skin under the panels (a light headliner above the windows), the floor, the dashboard with
  the instruments and a screen, the steering wheel and column, two seats, a console with a gear lever, the rear
  bench, a parcel shelf and the mirror. It is fitted to the game's drivers:
  - the script poses the player and the eight people in their Drive animation, as the game seats them;
  - the seat's height is where the longest legs' heels rest on the floor;
  - the steering wheel (41 cm, leaning back 15°) is placed nearest to all their hands.
- **The driver's door** (on the left, as in Israel) is cut out of the body along its lines: around the front arch,
  along the sill and the B-pillar, and above the window's foot with the glass and its frame. Its gap is 4.4 mm all
  round. It has an inside panel with an armrest and a handle, and seals on its edges. It carries its mirror and
  handle, and turns 65° on a hinge at its front edge. The opening has jambs with a seal lip.
- **Two levels of detail:**
  - **The near model:** about 45–55 thousand triangles with the cabin, plus 4.6 thousand per wheel.
  - **The far model:** the cage subdivided once, lighter details and wheels, and a simple cabin. About 11–13
    thousand triangles, and its wheels are part of it.
- **Materials for the game:**
  - `Paint`: each car's colour.
  - `Glass`.
  - `Trim`: everything else, from an 8×8 atlas of colour, roughness and metalness.
  - `Lamps`: one texel per lamp function, which the game lights per car.
  - `Plate`: where the game puts the number plates.

The script checks every car, and `city/assets/cars_report.json` holds the results (it fails a car on any problem):

- **Dimensions:** length, width, height, overhangs, wheelbase, track and clearance, against the table, within 1 cm.
- **Wheels:**
  - each tyre at least 1.5 cm from the body at every steering angle up to full lock (0.58 rad), at rest and pushed
    up to the suspension's bump stop (2.3, 2.6 and 3.1 cm, the travel the game allows);
  - no tyre sticking out of the body.
- **The mesh:**
  - no degenerate faces;
  - the closed body faces outwards (a positive volume);
  - the details on the body lie 0.8–6.5 mm above it (not sunk in, not floating);
  - nothing of the cabin, near or far model, sticks out of the body (by an odd number of crossings along three
    rays from each point);
  - no rays from inside the cabin escaping (with the door's gap counted as sealed).
- **The door:**
  - its gap is 3–6 mm;
  - it swings to 65° without touching the body, its details or the cabin.
- **The drivers:** for the player and each of the eight people at the wheel:
  - at least 3 cm of head room;
  - nothing of them inside the dashboard, the door, the headliner or the steering wheel;
  - both hands within 6 cm of the rim.
- **The lamps:** every function is there, on its side and at its end (the left indicator on the left, in front,
  behind and on the mirror).

**In the game** (`city/index.html`):
- **Detail:** cars use the near model within 35 m (25 m on phones).
- **Lamps:** running lights, faint tail lights, brake lights, reverse lights, and indicators blinking at 1.5 Hz.
- **Indicators:**
  - Traffic picks its next turn when it enters a street and signals for the last 30 m before the turn and through it.
  - The player's car signals by itself in a sharp, slow turn.
- **The door:** opens and shuts when someone gets in or out.
- **Seating:** the seat comes from the model.

**Physics** (`city/index.html`): a car is a rigid body on four tyres, stepped 120 times a second. The model's extras
give its mass (1,200, 1,400 and 1,650 kg), its centre of gravity, power and torque, gears and suspension travel.

- **Tyres:**
  - Each tyre's grip comes from its slip angle (a Pacejka curve) and the load on it, within a friction circle.
  - The load shifts forward under braking, back under acceleration, and outward in a corner.
  - A locked wheel slides against its own motion.
- **Engine and gearbox:**
  - A torque curve, an automatic six-speed gearbox and a limiter.
  - The hatchback and sedan drive their front wheels; the crossover all four.
  - Reverse engages on S once the car has stopped.
- **Brakes:** shared 65/35 front to rear. The handbrake locks the rear wheels, so the tail slides out.
- **Measured in the game** (`cg_physics.js`):

  | | 0–100 km/h | Braking from 100 km/h | Cornering | Handbrake slide |
  |---|---|---|---|---|
  | Hatchback | 9.8 s | 40 m | 0.94 g | 62° |
  | Sedan | 9.0 s | 40 m | 0.94 g | 50° |
  | Crossover | 9.4 s | 40 m | 0.92 g | 55° |

  Standing still, a car stays put.
- **Suspension (every car):**
  - Each wheel stands on the ground under it: a curb lifts it 15 cm.
  - The body rides on springs above the four wheels, and leans under braking, accelerating and cornering.
  - Each wheel's travel from the body is held within the model's bump (checked against its arches) and droop.
- **Collisions:**
  - Cars are boxes; buildings are boxes and posts are circles.
  - A hit gives both sides an impulse at the contact point, with friction, so a car hit off its centre spins.
    Momentum is kept (96% in the test, a sedan into a parked hatchback at 30 km/h).
  - At 40 m/s a car still stops at a wall (steps of 37 cm, less than its width).
  - A traffic car stops for a moment when it is nudged, and becomes a free body when it is really hit.
  - A parked car that is hit slides on its locked wheels.
- **Feedback:** the tyres leave marks on the road and squeal when they slide, and the engine's sound follows its
  revolutions. The speedometer shows the gear.

**Crash damage** (`city/index.html`): a car is damaged where it is hit, and only there.
- **The hit:** cars take a dent and sparks fly from about 8 km/h. The dent's depth grows with the square of the
  speed, up to 42 cm; its size grows with the speed.
- **The dent:** the panels around the point of contact are pushed in along the hit. They ripple as they crumple,
  and a hood or a boot lid buckles up. No point moves more than 50 cm from where it was.
- **Glass and lamps:** windows in the zone shatter into shards, and lamps there go dark.
- **Pieces breaking off:** in a hard hit, bits of bumper, grille and lamp come off, and so does the bumper's cover
  low down. They fall as bodies of their own: they bounce, tumble and stay on the road.
- **Wheels:** a wheel near the hit is knocked out of line (toe and camber), and the car pulls to that side.
- **Engine:** a heavy hit at the front costs the engine power, and it steams.
- **Shake:** the camera shakes with the player's crash.
- **Repair:** R, or the 🔧 button while the car is damaged.
- **Both models:** the near and the far model take the same damage, and only the hit car's geometry is copied.

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
- **Run:** 2.4 m per 0.6 s, with a flight phase. The elbows are bent about
  90°, the hands in loose fists, and the arms swing from the shoulder.
- **Drive:** sitting, with hands on the wheel.
- **Jump.**

With the arms down, the inner arm rests against the shirt's side. Two shape keys
on the shirt press the cloth under the arms in, just enough to clear the arm and
the sleeve in every frame: `ArmsDown` for Idle and Walk (the hands hang by the
hips), and `ArmsDownRun` for Run (only the upper arm is by the body). Each key is
measured on the cloth's faces as well as its corners. The game sets each key to
the weight of its animations; driving and jumping hold the arms away.

For the game it also fixes two of MB-Lab's textures (the city's people get the
same): the eyelash texture is grey cards with the lashes drawn on them and no
transparency, so it is replaced by dark lashes whose alpha is the lashes (no
white strips along the eyelids); and the skin texture's white background is
filled from the edges of its UV islands, so the smaller mipmaps don't draw light
lines along the seams.

It also adds cloth bones for the back of the shirt's hem, the sleeves and the
jeans legs. Each animation has the cloth swing built in. In the game, springs on
these bones also react to starting, stopping and turning. Each spring is held
within its own limits: the back of the hem swings back freely but hardly forward
(the body is there), and the sleeves and the jeans legs only sway (an arm or a
leg is inside).

The front of the hem rests on the thighs. Two hem bones at the hips carry it up
over a thigh that comes forward; over a thigh that goes back it just hangs.

Every frame of every animation is checked for skin through the clothes and for
the shirt through the jeans. So is every spring at its limits, and the Walk/Run
blends the game plays while speeding up or slowing down. Skin under the cloth
is measured against the cloth that was over it at rest, not a fold pressed
close (as in the armpit). The checks use the same 4 strongest bones per vertex
that glTF keeps. The results are in `player_report.json`:

- 0 problems standing, walking, running and jumping, with the springs at their
  limits, and in the blends.
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
- It sets `ArmsDown` to the weight of Idle and Walk, and `ArmsDownRun` to the
  weight of Run.
- If `player.glb` can't load, the game uses its simple figure.

`city/build_page.py` makes the published page.

## The city's people (`city/assets/npc/`)

The pedestrians and drivers are eight realistic characters made the way the
player is: MB-Lab bodies, the player's clothes fitted to each, hair, and the
same animation and checks.

| | Body (MB-Lab phenotype, body type) | Hair |
|---|---|---|
| `f_ca01` | Mediterranean woman, average | chin-length bob, dark brown |
| `f_af01` | African woman, average | full natural curls, black |
| `f_as01` | East Asian woman, slim | high ponytail, black |
| `f_la01` | Latin American woman, rounded | bun, chestnut |
| `m_af01` | African man, athletic | fade, black |
| `m_as01` | East Asian man, slim | short, swept to the side, black |
| `m_ca01` | Nordic man, average | crew cut, dark blond |
| `m_la01` | Central American man, heavy, older | short and combed, grey |

How they are made (`characters/npc/<id>.json` holds each one's eye colour, hair
style and hair colour):

```
blender -b -P blender/make_npc_bodies.py -- <MB-Lab checkout> characters/npc
blender -b -P blender/dress_character.py -- characters/npc/<id>.glb characters/npc/dressed characters/npc/<id>.json
blender -b -P blender/make_hair.py -- <MB-Lab checkout> characters/npc/dressed/<id>_dressed.blend characters/npc/<id>.blend <style> <r,g,b>
blender -b -P blender/export_player.py -- characters/npc/<id>.blend city/assets/npc/<id>.glb --npc
python3 blender/npc_manifest.py city/assets/npc
```

1. **Bodies:** `make_npc_bodies.py` runs [MB-Lab](https://github.com/animate1978/MB-Lab)
   without its interface. Each character gets its template (woman or man), a
   phenotype (face and build of a population), a body type, an age, mass and
   tone. The body is finalized with MB-Lab's skeleton (the player's) and saved
   like `Human.glb`.
2. **Clothes:** `dress_character.py` carries the player's finished T-shirt,
   jeans, socks and sneakers onto the body:
   - The two bodies' skins are matched through MB-Lab's shared UV layout.
   - The clothes move as the skin under them does.
   - They are set off the new body again: the collar, the sleeve ends and the
     waistband keep the player's gaps, and the shirt hangs from the chest.
   - They are checked like the player's.
3. **Hair:** `make_hair.py` builds hair as meshes a game can draw.
   - **Cap:** MB-Lab's scalp region, lifted off the skin by a thickness that
     varies around the head and thins at the hairline.
   - **Shells:** copies of the cap further out, with a strand texture whose
     transparency grows outward, for volume and a soft outline.
   - **Parts per style:** a bob's hanging hair, a ponytail with its hair tie, a
     bun.
   - **Rigging:** the hair is weighted to the head. The scalp under it is hidden
     by the body's mask, as the skin under the clothes is.
4. **Game version:** `export_player.py --npc` uses the player's pipeline. Its
   measures (the hem's height, where the sleeves start, the feet's places) are
   carried to each body by its skeleton's landmarks.
   - **Gait:** the stride scales with the leg length, and the cycle time with its
     square root.
   - **Arms:** they hang as far outside the hips as the player's do.
   - **Animations:** Idle, Walk, Run, Drive and Lie (knocked down: on the back,
     the head, back, seat, arms and heels solved down onto the ground).
5. **Checks:** every frame of every animation and the Walk/Run blends are
   checked as for the player, with the hair as a piece of cloth:
   - no scalp through it, no skin into it, none of it through the shirt;
   - skin under the cloth that would still come out through it is hidden by the
     mask. That covers a heavy body's armpit as the arms come down: up to a few
     dozen skin points, only where the cloth covers them.

   The reports are `city/assets/npc/<id>_report.json`. The only finding is the
   one the player has, at the wheel: the shirt's hem against the jeans in the
   lap, inside the car. `npc_manifest.py` leaves out anyone with other findings.
6. **Two levels of detail in one file, on one skeleton:**
   - **LOD0** (near, 33–37k triangles, 5 draw calls): lighter garments and
     shoes than the player's, with the hems at full detail.
   - **LOD1** (from 22 m, 12 m on phones; about 9k triangles, 3 draw calls):
     the body welded and decimated, with the head kept finer, and one hair layer.
     It is checked with a 1 cm tolerance.
   - **Colours:** the garments, the eyes and the nails share one material: a
     colour per vertex times an 8×1 palette texture (shirt, jeans, shoes). Each
     pedestrian gets their own palette, so their own clothes colours.
   - **File:** skin weights, texture coordinates and colours are stored as
     integers (core glTF), about 1.8–2 MB per person.

In the game (`city/index.html`) the people load after the city, so they don't
delay the start: until a person's model is in, a simple figure walks in their
place.
- **Instances:** each pedestrian or driver is a copy sharing the model's
  geometry, with their own skeleton and animation mixer.
- **Detail:** LOD0 or LOD1 by distance; far people's animations update about 12
  times a second.
- **Motion:** like the player, Idle, Walk and Run are blended by speed and timed
  to it, with the `ArmsDown` shapes.
- **Drivers** sit with Drive. A pedestrian hit by a car lies down (Lie), then
  gets up and runs away.

MB-Lab's code is GPL3 and its data (the base meshes and textures the bodies
come from) is AGPL3, as for `characters/Human.glb`.
