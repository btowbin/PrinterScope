# Compact Ender 3 V2 microscope adapter — R3, 30.5 mm barrel

Use `PRINT_base_R3.stl` and `PRINT_cap_R3.stl`: these smooth, watertight
models are already resting on their flat axial ends. Earlier downloads are
superseded. The older filenames in this directory have also been regenerated.

This two-piece split clamp is redesigned around the measured constraints:

- Optical barrel diameter: **30.5 mm**
- Bed to mounting-screw centre at Z home: **45 mm**
- Maximum tube-end distance below the mounting-screw centre: **8.5 mm**
- Adapter lower extent below the screw centre: **8.0 mm**
- Material between the carriage and the screw-head bearing seat: **12 mm**,
  matching the removed hot end
- Total local plate depth: **16 mm** (12 mm bearing length + 4 mm recess)
- M3 access pockets: **6.8 mm wide**, extending through the entire rear clamp
  wall; heads up to 6 mm diameter and 3 mm height are digitally checked
- Carriage-contact plate extends only **8 mm above** the screw line, reducing
  the chance of interference with upper roller screw heads
- Uncompressed clamp bore: **30.6 mm**, with a **2 mm split gap**

Align the lower end/shoulder of the 30.5 mm tube flush with the bottom of the
clamp. It will then sit 8.0 mm below the mounting-screw centreline.

## Hardware

- 2 × original M3 hot-end mounting screws. Their length was not measured;
  the previous instructions incorrectly assumed M3 × 12 mm. At a 12 mm
  bearing length, M3 × 16 projects 4 mm and M3 × 18 projects 6 mm into/through
  the carriage, exactly as with a 12 mm-thick original mount. Reusing the
  same screws preserves the original engagement and rear projection.
- 2 × M4 × 16 mm socket-head screws for the split cap
- 2 × ordinary M4 hex nuts, 7 mm across flats and about 3.2 mm thick

M3 × 12 mm screws cannot engage the carriage through a 12 mm bearing length.
Do not substitute those merely because the original mount was 12 mm thick.

## Printing

- PETG, ASA, ABS, or tough PLA/PLA+
- 0.20 mm layers
- 5 perimeters
- 5 top/bottom layers
- 40–60% infill
- Load the two `PRINT_*_R3.stl` files without rotating them; both have their
  flat lower axial face at Z=0. This also gives useful layer orientation for
  forces from tightening the cap.
- No supports should normally be needed. The small horizontal screw/nut
  passages require short bridges; check the sliced layers on your printer.
- Print at 100% scale. The bore has only 0.1 mm diametral clearance, so check
  the printed fit. Remove burrs or lightly sand a tight bore; do not force
  the barrel into it. Do not globally enlarge the model.

## Assembly

1. Bolt the base to the two original hot-end M3 holes. The slots are nominally
   14 mm apart and allow horizontal adjustment. The socket heads sit in the
   counterbores, below the optical barrel. Install and tighten these screws
   **before** inserting the tube and cap: the tube covers the tool access.
   Each seat is 12 mm from the carriage-contact face.
2. Insert an M4 nut in each rear-facing hex pocket on the base. The pockets
   replace the earlier plastic pilot threads.
3. Place the smooth 30.5 mm barrel in the rear half-clamp.
4. Align the tube's lower end flush with the clamp's lower edge.
5. Install the front cap with the two M4 screws and tighten alternately, only
   until the tube cannot rotate or slide. Do not crush the optical barrel.
   Leave a visible gap between the halves. A digital 0.4 mm closure produces
   tube contact while leaving a 1.6 mm gap; this confirms tightening travel,
   not a measured holding force. Check the real tube cannot slip or turn.
6. With power off, move the axes through their full range by hand and verify
   bed and frame clearance.
7. Raise or reposition the Z-limit switch if the objective still approaches
   the bed too closely. Perform the first powered approach over a soft pad.

## Verification and limits

`validation_report.json` records solid-intersection tests for both M3 shanks,
6 mm screw heads swept along their insertion paths, Allen-key paths, M4 clamp
screws and hex nuts, a 30.5 mm tube, and cap closure. Each exported part must
be one connected watertight solid. The lower extent is exactly 8.0 mm in the
smooth model, rather than the previous voxel model's 8.1 mm.

These are digital assembly checks, not a physical test on your printer. The
14 mm mounting pattern is still nominal and must match your particular V2.
The actual original screw length and protruding carriage bosses have not
been measured. Check the printed base seats squarely on the original mounting
surfaces and clears the roller heads before fitting the optics.

At a screw-line home height of 45 mm and a tube end 8 mm below it, the tube
end is 37 mm above the bed. Objective projection and specimen height reduce
that gap; the adapter alone cannot confirm objective clearance during homing.
