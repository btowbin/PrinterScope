"""Generate a dense four-slide oblique-illumination holder in millimetres.

Slides are arrayed along X.  Each independently wired 25 x 75 mm LED board is
shifted upstream toward -X, so boards 2-4 nest beneath the preceding slide.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw

import generate_holder as cad


OUT_DIR = Path(__file__).resolve().parent
SLIDE_COUNT = 4
SLIDE_PITCH = 27.5
BASE_Y_MIN, BASE_Y_MAX = -47.0, 47.0


def build_multiplex_holder():
    theta = math.radians(cad.ANGLE_DEG)
    normal = np.array([math.sin(theta), 0.0, math.cos(theta)])

    tray_width = cad.BOARD[0] + 2.0 * (cad.BOARD_CLEARANCE + cad.TRAY_MARGIN_X)
    tray_length = cad.BOARD[1] + 2.0 * cad.TRAY_MARGIN_Y
    tray_z0 = cad.BASE_THICKNESS + math.sin(theta) * tray_width / 2.0

    board_underside_w = cad.TRAY_BACK_THICKNESS + cad.SOLDER_CLEARANCE
    board_top_w = board_underside_w + cad.BOARD[2]
    led_tip_w = board_top_w + cad.LED_BODY_HEIGHT
    optical_shift = cad.LED_TIP_TO_SLIDE_TOP_Z * math.tan(theta)

    slide_centres = np.array(
        [(i - (SLIDE_COUNT - 1) / 2.0) * SLIDE_PITCH for i in range(SLIDE_COUNT)]
    )
    tray_origins_x = slide_centres - optical_shift - normal[0] * led_tip_w

    led_tip_z = tray_z0 + normal[2] * led_tip_w
    slide_top_z = led_tip_z + cad.LED_TIP_TO_SLIDE_TOP_Z
    slide_bottom_z = slide_top_z - cad.SLIDE[2]

    # The first illuminator extends upstream of slide 1.  The last slide defines
    # the positive-X edge.  Four millimetres of perimeter remains around both.
    first_tray_min_x = tray_origins_x[0] - math.cos(theta) * tray_width / 2.0
    last_slide_max_x = slide_centres[-1] + cad.SLIDE[0] / 2.0
    base_x_min = math.floor(first_tray_min_x - 4.0)
    base_x_max = math.ceil(last_slide_max_x + 4.0)

    solids = [
        cad.box(
            (base_x_max - base_x_min, BASE_Y_MAX - BASE_Y_MIN, cad.BASE_THICKNESS),
            ((base_x_min + base_x_max) / 2.0, 0.0, cad.BASE_THICKNESS / 2.0),
        )
    ]
    transforms = []

    for tray_x0 in tray_origins_x:
        transform = cad.local_transform(float(tray_x0), tray_z0, cad.ANGLE_DEG)
        transforms.append(transform)

        # Support-free triangular wedge beneath this tray.
        u0, u1 = -tray_width / 2.0, tray_width / 2.0
        x_left = tray_x0 + math.cos(theta) * u0
        z_left = tray_z0 - math.sin(theta) * u0
        x_right = tray_x0 + math.cos(theta) * u1
        solids.append(
            cad.prism_y(
                [
                    (x_left, cad.BASE_THICKNESS),
                    (x_right, cad.BASE_THICKNESS),
                    (x_left, z_left),
                ],
                tray_length,
            )
        )

        # Tray back and side rails.
        solids.append(
            cad.local_box(
                (tray_width, tray_length, cad.TRAY_BACK_THICKNESS),
                (0.0, 0.0, cad.TRAY_BACK_THICKNESS / 2.0),
                transform,
            )
        )
        rail_width = 2.0
        clear_width = cad.BOARD[0] + 2.0 * cad.BOARD_CLEARANCE
        rail_center_u = clear_width / 2.0 + rail_width / 2.0
        rail_height = board_top_w + 1.0 - cad.TRAY_BACK_THICKNESS

        for sign in (-1.0, 1.0):
            solids.append(
                cad.local_box(
                    (rail_width, tray_length, rail_height),
                    (
                        sign * rail_center_u,
                        0.0,
                        cad.TRAY_BACK_THICKNESS + rail_height / 2.0,
                    ),
                    transform,
                )
            )
            solids.append(
                cad.local_box(
                    (rail_width + 1.2, tray_length - 1.0, 0.6),
                    (
                        sign * (clear_width / 2.0 - 0.6 + rail_width / 2.0),
                        0.0,
                        board_underside_w - 0.3,
                    ),
                    transform,
                )
            )
            for v in (-25.0, 25.0):
                solids.append(
                    cad.local_box(
                        (rail_width + 1.0, 8.0, 0.8),
                        (
                            sign * (clear_width / 2.0 - 0.5 + rail_width / 2.0),
                            v,
                            board_top_w + 0.6,
                        ),
                        transform,
                    )
                )

        board_clear_length = cad.BOARD[1] + 2.0 * cad.BOARD_CLEARANCE
        for sign in (-1.0, 1.0):
            solids.append(
                cad.local_box(
                    (tray_width, 1.75, board_top_w + 1.0 - cad.TRAY_BACK_THICKNESS),
                    (
                        0.0,
                        sign * (board_clear_length / 2.0 + 0.875),
                        (cad.TRAY_BACK_THICKNESS + board_top_w + 1.0) / 2.0,
                    ),
                    transform,
                )
            )

    # Common end towers and ledges support all four slides while leaving their
    # central 68 mm fully open for illumination and imaging.
    slide_clear_length = cad.SLIDE[1] + 2.0 * cad.SLIDE_CLEARANCE
    tower_y_thickness = 4.4
    tower_inner_y = slide_clear_length / 2.0
    array_min_x = slide_centres[0] - cad.SLIDE[0] / 2.0
    array_max_x = slide_centres[-1] + cad.SLIDE[0] / 2.0
    tower_width_x = array_max_x - array_min_x + 8.0
    tower_center_x = (array_min_x + array_max_x) / 2.0
    tower_top_z = slide_top_z + 1.2

    for sign in (-1.0, 1.0):
        tower_center_y = sign * (tower_inner_y + tower_y_thickness / 2.0)
        solids.append(
            cad.box(
                (tower_width_x, tower_y_thickness, tower_top_z - cad.BASE_THICKNESS),
                (tower_center_x, tower_center_y, (cad.BASE_THICKNESS + tower_top_z) / 2.0),
            )
        )
        ledge_depth = 3.3
        solids.append(
            cad.box(
                (array_max_x - array_min_x + 4.0, ledge_depth, 3.0),
                (
                    tower_center_x,
                    sign * (tower_inner_y - ledge_depth / 2.0),
                    slide_bottom_z - 1.5,
                ),
            )
        )

        # Outer guides.
        guide_height = 2.2
        guide_width = 1.8
        for x in (
            array_min_x - cad.SLIDE_CLEARANCE - guide_width / 2.0,
            array_max_x + cad.SLIDE_CLEARANCE + guide_width / 2.0,
        ):
            solids.append(
                cad.box(
                    (guide_width, 6.0, guide_height),
                    (x, sign * (tower_inner_y - 2.2), slide_bottom_z + 0.9),
                )
            )

        # Shared internal dividers use the otherwise unused gap between slides.
        divider_width = SLIDE_PITCH - cad.SLIDE[0] - 2.0 * cad.SLIDE_CLEARANCE
        for left, right in zip(slide_centres[:-1], slide_centres[1:]):
            solids.append(
                cad.box(
                    (divider_width, 6.0, guide_height),
                    (
                        (left + right) / 2.0,
                        sign * (tower_inner_y - 2.2),
                        slide_bottom_z + 0.9,
                    ),
                )
            )

    invalid = [i for i, mesh in enumerate(solids) if not mesh.is_volume]
    if invalid:
        raise RuntimeError(f"Non-volume input solids: {invalid}")
    combined = trimesh.boolean.union(solids, engine="manifold")
    if isinstance(combined, list):
        combined = trimesh.util.concatenate(combined)

    cutters = []
    # Support-free arched openings remove most of the material from the two long
    # end towers.  The 45-degree roofs print without generated support.
    window_bottom_z = 8.0
    window_top_z = slide_bottom_z - 4.0
    window_half_width = 10.0
    roof_rise = 10.0
    for sign in (-1.0, 1.0):
        tower_center_y = sign * (tower_inner_y + tower_y_thickness / 2.0)
        for slide_x in slide_centres:
            window = cad.prism_y(
                [
                    (slide_x - window_half_width, window_bottom_z),
                    (slide_x + window_half_width, window_bottom_z),
                    (slide_x + window_half_width, window_top_z - roof_rise),
                    (slide_x, window_top_z),
                    (slide_x - window_half_width, window_top_z - roof_rise),
                ],
                tower_y_thickness + 2.0,
            )
            window.apply_translation([0.0, tower_center_y, 0.0])
            cutters.append(window)

    # Six accessible M4 base holes, outside the end towers.
    mount_x = [base_x_min + 4.0, (base_x_min + base_x_max) / 2.0, base_x_max - 4.0]
    for x in mount_x:
        for y in (-44.0, 44.0):
            cutters.append(
                trimesh.creation.cylinder(
                    radius=2.15,
                    height=cad.BASE_THICKNESS + 2.0,
                    sections=48,
                    transform=trimesh.transformations.translation_matrix(
                        [x, y, cad.BASE_THICKNESS / 2.0]
                    ),
                )
            )

    # One isolated two-wire outlet per illuminator through the negative-Y tower.
    for transform in transforms:
        cutters.append(
            cad.local_box(
                (9.0, 15.0, cad.SOLDER_CLEARANCE + 0.6),
                (
                    0.0,
                    -tray_length / 2.0 - 0.5,
                    cad.TRAY_BACK_THICKNESS + (cad.SOLDER_CLEARANCE + 0.6) / 2.0,
                ),
                transform,
            )
        )

    combined = trimesh.boolean.difference(
        [combined, trimesh.boolean.union(cutters, engine="manifold")],
        engine="manifold",
    )

    refs = {
        "slide_count": SLIDE_COUNT,
        "slide_pitch": SLIDE_PITCH,
        "slide_centres_x": slide_centres.tolist(),
        "led_tip_centres_x": (slide_centres - optical_shift).tolist(),
        "pcb_centres_x": (
            tray_origins_x + normal[0] * (board_underside_w + cad.BOARD[2] / 2.0)
        ).tolist(),
        "slide_top_z": float(slide_top_z),
        "led_tip_z": float(led_tip_z),
        "tip_to_slide_top_z": float(slide_top_z - led_tip_z),
        "base_x_min": float(base_x_min),
        "base_x_max": float(base_x_max),
    }
    return combined, refs


def render_top_layout(refs, output: Path):
    """Draw slides and PCBs over one another to make the nesting explicit."""
    width, height = 1300, 760
    image = Image.new("RGBA", (width, height), (248, 249, 251, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    margin_x, margin_y = 100, 90
    xmin, xmax = refs["base_x_min"], refs["base_x_max"]
    ymin, ymax = BASE_Y_MIN, BASE_Y_MAX
    scale = min((width - 2 * margin_x) / (xmax - xmin), (height - 2 * margin_y) / (ymax - ymin))

    def p(x, y):
        return (
            margin_x + (x - xmin) * scale,
            height - margin_y - (y - ymin) * scale,
        )

    def rect(cx, cy, sx, sy, fill, outline, line=3):
        x0, y0 = p(cx - sx / 2.0, cy - sy / 2.0)
        x1, y1 = p(cx + sx / 2.0, cy + sy / 2.0)
        draw.rectangle((x0, y1, x1, y0), fill=fill, outline=outline, width=line)

    rect((xmin + xmax) / 2.0, 0.0, xmax - xmin, ymax - ymin, (235, 238, 242, 255), (65, 70, 78, 255), 4)

    projected_board_width = cad.BOARD[0] * math.cos(math.radians(cad.ANGLE_DEG))
    # Draw all PCBs first and the slide boundaries above them, making the
    # footprint overlap unambiguous.
    for index, pcb_x in enumerate(refs["pcb_centres_x"], start=1):
        rect(pcb_x, 0.0, projected_board_width, cad.BOARD[1], (244, 170, 92, 255), (185, 85, 15, 255), 3)
        bx, by = p(pcb_x, -27.0)
        draw.text((bx - 26, by - 10), f"LED{index}", fill=(130, 55, 10, 255))
        # Cable leaves each PCB independently through negative Y.
        c0 = p(pcb_x, -cad.BOARD[1] / 2.0)
        c1 = p(pcb_x, BASE_Y_MIN)
        draw.line((c0, c1), fill=(35, 35, 35, 255), width=3)

    for index, slide_x in enumerate(refs["slide_centres_x"], start=1):
        rect(slide_x, 0.0, cad.SLIDE[0], cad.SLIDE[1], None, (20, 90, 175, 255), 5)
        sx, sy = p(slide_x, 28.0)
        draw.text((sx - 16, sy - 10), f"S{index}", fill=(15, 55, 105, 255))

    # Illumination direction.
    a0 = p(xmin + 8.0, ymax - 7.0)
    a1 = p(xmin + 28.0, ymax - 7.0)
    draw.line((a0, a1), fill=(20, 120, 55, 255), width=5)
    draw.polygon([(a1[0], a1[1]), (a1[0] - 14, a1[1] - 8), (a1[0] - 14, a1[1] + 8)], fill=(20, 120, 55, 255))
    draw.text((a0[0], a0[1] - 26), "+X light direction", fill=(15, 90, 40, 255))
    draw.text((70, 28), "Top view: four slides with nested upstream LED boards", fill=(20, 25, 30, 255))
    draw.text((70, 52), "Blue outline = microscope slide; orange = tilted LED PCB footprint; black = separate cable pair", fill=(55, 60, 68, 255))
    image.convert("RGB").save(output)


def main():
    holder, refs = build_multiplex_holder()
    stl = OUT_DIR / "oblique_led_4slide_multiplex_18deg.stl"
    preview = OUT_DIR / "oblique_led_4slide_multiplex_18deg_preview.png"
    holder.export(stl)
    cad.render_preview(holder, preview)
    render_top_layout(refs, OUT_DIR / "oblique_led_4slide_multiplex_top_layout.png")

    components = cad.connected_body_summaries(holder)
    print(f"Wrote: {stl}")
    print(f"Watertight: {holder.is_watertight}")
    print(f"Connected bodies: {len(components)}")
    print(f"Euler number: {holder.euler_number}")
    print(f"Bounds mm: {holder.bounds.tolist()}")
    print(f"Volume mm^3: {holder.volume:.1f}")
    for key, value in refs.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
