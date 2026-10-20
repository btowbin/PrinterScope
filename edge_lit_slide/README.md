# Edge-lit microscope-slide illuminator

This is a first prototype based on the photographed acrylic light guide. It
uses a **75 x 25 x 3 mm clear cast acrylic plate** with a shallow wavy pattern
engraved on its underside. A 5 mm white LED couples into the polished short
edge. The guide fits in an opaque printed holder so every sample position can
have its own independently switched light source.

## Files

- `edge_lit_slide_gradient.svg` — laser cut/engrave artwork at true size.
  Red is the cut outline; blue is the engraving. The LED is on the **left**.
- `edge_lit_slide_holder.scad` — parametric holder for the guide and a round
  5 mm LED. Adjust the values at the top if the measured material differs.

## Recommended optical stack

From top to bottom:

1. The specimen on its normal 75 x 25 mm glass slide.
2. Optional 0.3–0.8 mm opal/frosted diffuser film. Add this only if the camera
   can resolve the engraved waves; it costs brightness.
3. The 3 mm clear cast-acrylic light guide, smooth face upward and engraved
   face downward.
4. A white matte reflector card below the guide, separated by a small air gap.
   Do not glue the reflector across the optical area.
5. The black holder.

The clear acrylic is an illuminator, not the specimen slide. Keeping the wet
sample on ordinary glass avoids scratching, staining, or chemically attacking
the acrylic and allows the sample to be replaced without disturbing the LED.

## Laser process

1. Use **cast PMMA/acrylic**, not polycarbonate and preferably not extruded
   acrylic. Leave the protective film on the smooth/top face.
2. Vector-engrave the blue waves very lightly on the underside. Aim for a
   frosted line roughly 0.1–0.25 mm deep, not a through-cut.
3. Cut the red outline.
4. Polish only the left 25 mm edge where the LED enters. Keep the other three
   edges frosted or cover them with reflective white tape, then an opaque black
   outer layer to reduce light leakage and cross-talk.
5. Clean with water and mild soap. Avoid alcohol on stressed acrylic because
   it can cause crazing.

The engraving spacing becomes progressively tighter away from the LED. This
extracts less light near the source and more at the far end, improving
uniformity compared with evenly spaced waves.

## LED and wiring

- Start with one diffused, neutral-white **5 mm LED**, nominally 20 mA,
  4000–5000 K and as high a CRI as practical. A clear narrow-angle LED will
  make a stronger central hot spot.
- Butt the LED flat against the polished acrylic edge. A tiny amount of clear
  optical coupling gel improves transfer; do not use cyanoacrylate near the
  optical surface because its vapour can haze acrylic.
- Drive the LED with regulated current. For a simple 5 V test, use one white
  LED and **100 ohms** in series; this is about 15–20 mA for a typical 3.0–3.3 V
  LED. Verify the actual LED forward voltage and resistor temperature.
- For camera work, prefer DC current dimming. Low-frequency PWM can create
  bands when the camera uses a rolling shutter.

If one LED leaves a visible central gradient, the most effective revision is
three small white LEDs spaced across the 25 mm edge, not a more aggressive
engraving. They can still be wired and controlled as one light per slide.

## First test and tuning

1. Photograph a blank field using fixed exposure, gain, white balance, focus,
   and LED current.
2. Compare brightness in nine equal regions of the image.
3. If the LED end is too bright, mask the first 5–10 mm or make the first few
   engraved waves shallower.
4. If the far end is too dark, deepen only the far-third engraving or add white
   reflective tape to the far edge.
5. Add the opal film only after the gradient is acceptable; it smooths fine
   structure but does not correct a large end-to-end gradient.

## Important dimensions to verify before making a batch

- Actual microscope slide width: nominal standards vary around 25–26 mm.
- Available Z clearance: this stack adds about 4–5 mm below the specimen.
- LED body diameter and the height of its optical centre.
- Whether the holder must fit an existing bed grid or locating-pin pattern.

The SVG is intended as a tuning prototype. Make one guide first, measure its
uniformity with the actual camera and objective, and then freeze the engraving
settings before producing one illuminator per slide position.
