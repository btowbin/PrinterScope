# Four-slide multiplex oblique-illumination holder

This one-piece holder contains four adjacent 25 x 75 mm slide positions and four
independently wired 25 x 75 mm LED-board cradles.

The slide centres are spaced by 27.5 mm. Every illuminator points toward +X and
is shifted upstream toward -X. Consequently, the illuminators for slides 2, 3
and 4 nest substantially beneath slides 1, 2 and 3 respectively. This reduces
the total width compared with placing a separate illuminator beside each slide.

## Optical dimensions

- Illumination angle: 18 degrees.
- LED lens-tip centre to corresponding slide centre: 16.246 mm in X.
- LED lens-tip centre to slide top: 50.000 mm in Z.
- Slide pitch: 27.5 mm.
- Slide clearance: 0.30 mm per side.
- PCB clearance: 0.25 mm per side.
- Rear wiring depth: 5.0 mm.

The geometry assumes 1.6 mm PCBs, 1.0 mm slides and 8.7 mm-tall 5 mm LEDs
mounted flush to the PCB.

## Wiring

Each board is a separate electrical channel. Route its +12 V and switched return
through the cable tunnel directly behind it at the negative-Y end. Four MOSFET
modules and four Arduino outputs are required. Do not electrically connect the
switched-return wires together after the MOSFET outputs.

## Printing

- Material: matte-black PETG.
- Orientation: large base flat on the print bed.
- Layer height: 0.20 mm.
- Perimeters: 4.
- Top and bottom layers: 5.
- Infill: 25%.
- Supports: none.
- Brim: 6-8 mm recommended because of the two long, tall end towers.

The print occupies approximately 139 x 94 x 75 mm; confirm that the slicer's
brim also fits the build surface.
