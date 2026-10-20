# Combined oblique LED-board and slide holder

`oblique_led_slide_holder_18deg_v2.stl` is a one-piece fixture for:

- one 25 x 75 x 1.6 mm LED circuit board;
- one 25 x 75 x 1.0 mm microscope slide;
- an 18 degree illumination angle toward positive X;
- a 50 mm vertical distance from the centre of the LED lens tips to the top
  surface of the slide.

The model assumes 5 mm through-hole LEDs with an 8.7 mm body height, mounted
flush to the PCB. The optical shift is 16.246 mm. Because the LEDs project from
the inclined PCB, the PCB tray origin is placed farther upstream so that the
actual LED lens-tip centre, not the PCB centre, has the correct shift.

## Print settings

- Material: matte-black PETG recommended; PLA is acceptable for a prototype.
- Orientation: leave the large base flat on the build plate.
- Layer height: 0.20 mm.
- Walls: 4 perimeters.
- Top/bottom layers: 5.
- Infill: 25%.
- Supports: none.
- Brim: 5 mm is recommended for the two tall slide-support towers.

## Assembly

1. Remove strings and lightly deburr the PCB pocket and slide guides.
2. Put the LED bodies and resistors on the face of the PCB that points toward
   the slide. Trim and solder the leads on the rear face. Route the two supply
   wires through the 10 mm cable tunnel at the negative-Y end.
3. Press the populated LED board into the inclined cradle. The short side tabs
   snap over the PCB edges. Insert one long edge first and flex the opposite
   tabs only as much as needed.
4. Fit the slide from above. It rests on the two end ledges; the central 68 mm
   remains unobstructed.
5. Fasten the base using the four 4.3 mm mounting holes.

The slide is intentionally gravity-retained so it can be exchanged quickly.
The PCB pocket includes 0.25 mm clearance per side and 5 mm clearance beneath
the PCB for solder joints and point-to-point wiring. A 10 mm wide tunnel passes
through the PCB end stop and adjacent support tower, providing an unobstructed
exit for the +12 V and return wires. The slide pocket includes 0.30 mm clearance
per side.

## Direction

When viewed from above, the PCB is upstream in negative X and its light points
toward positive X. The centre of the slide is X = 0. An embossed direction mark
was omitted to avoid adding reflective surfaces near the specimen.

## Regeneration

Run `generate_holder.py` with Python and `trimesh` plus the Manifold Boolean
engine installed. All optical and mechanical dimensions are parameters near the
top of the file.
