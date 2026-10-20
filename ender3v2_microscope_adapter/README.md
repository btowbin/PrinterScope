# Ender 3 V2 microscope adapter — superseded universal version

> **Do not use this tall version for the 30.5 mm optical assembly.** It extends
> too far below the mounting screws for the measured 45 mm homing clearance.
> Use the compact split-clamp design in `../ender3v2_microscope_adapter_compact/`.

This adapter bolts to the two original hot-end mounting holes and holds a
cylindrical optical assembly in two padded V-cradles. It intentionally uses
heavy cable ties rather than a diameter-specific ring, so it can be tested
before the exact optical-tube diameter is known.

## Hardware

- 2 × M3 screws, approximately 12–16 mm long
- M3 washers/spacers providing a 3 mm gap behind the adapter
- 2 × heavy-duty cable ties, 7.6 mm wide and at least 300 mm long
- 1–2 mm neoprene, rubber, or dense felt for each cradle
- Optional third cable tie as a loose safety tether

## Installation

1. Print with the large flat rear face on the build plate. No supports are
   required.
2. Put rubber padding on both V-cradles.
3. Mount the adapter using the two central M3 slots. Place 3 mm of washers or
   spacers behind the adapter so the cable ties can pass behind it.
4. Feed one cable tie through each pair of side slots and around the smooth,
   rigid optical barrel. Do not clamp an adjustment ring, the objective, or
   the camera connector.
5. Tighten gradually and equally. Add a loose safety tether around the camera.
6. Raise the Z end-stop before homing and test above a soft sacrificial pad.

## Printing recommendation

- PETG, ASA, ABS, or a tough PLA/PLA+ for an initial low-speed test
- 0.20 mm layers
- 4–5 perimeters
- 5 top and bottom layers
- 35–50% infill

The supplied STL is voxel-generated at 0.4 mm resolution for immediate use.
For the cleanest surfaces, open the SCAD source in OpenSCAD and export a new
STL. The nominal carriage-hole spacing is 14 mm; the horizontal slots allow
for small production differences.

This is a test fixture. Begin at 10–20 mm/s and low acceleration. Inspect for
cracks or loosening before every run.
