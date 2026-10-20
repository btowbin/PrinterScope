"""Generate the combined oblique-LED-board and microscope-slide holder.

The STL is generated in millimetres.  The optical reference is the centre of
the tips of 8.7 mm-tall, 5 mm LEDs mounted against a 1.6 mm PCB.  The top of a
1.0 mm slide is 50.0 mm above that reference and displaced by tan(18 deg).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw


OUT_DIR = Path(__file__).resolve().parent

# Optical/mechanical inputs (mm, degrees)
ANGLE_DEG = 18.0
BOARD = (25.0, 75.0, 1.6)  # width X, length Y, thickness
BOARD_CLEARANCE = 0.25
LED_BODY_HEIGHT = 8.7
SLIDE = (25.0, 75.0, 1.0)
SLIDE_CLEARANCE = 0.30
LED_TIP_TO_SLIDE_TOP_Z = 50.0

# Printed-part dimensions
BASE_X_MIN, BASE_X_MAX = -43.0, 25.0
BASE_Y_MIN, BASE_Y_MAX = -45.0, 45.0
BASE_THICKNESS = 3.0
TRAY_BACK_THICKNESS = 1.6
# Space between the PCB underside and the printed tray back.  Five millimetres
# accommodates trimmed LED leads, point-to-point links and 0.14 mm2 wire.
SOLDER_CLEARANCE = 5.0
TRAY_MARGIN_X = 2.0
TRAY_MARGIN_Y = 2.0


def box(extents, center=(0.0, 0.0, 0.0), transform=None):
    mesh = trimesh.creation.box(extents=np.asarray(extents, dtype=float))
    if transform is not None:
        mesh.apply_transform(transform)
    mesh.apply_translation(np.asarray(center, dtype=float))
    return mesh


def local_transform(x0: float, z0: float, angle_deg: float):
    """Map tray local coordinates (u, v, w) to global (x, y, z).

    Positive local w is the LED pointing direction.  The resulting normal is
    tilted toward +X by angle_deg.
    """
    a = math.radians(angle_deg)
    return np.array(
        [
            [math.cos(a), 0.0, math.sin(a), x0],
            [0.0, 1.0, 0.0, 0.0],
            [-math.sin(a), 0.0, math.cos(a), z0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def local_box(extents, center, transform):
    m = box(extents, center=center)
    m.apply_transform(transform)
    return m


def prism_y(xz_points, y_length):
    """Create a watertight prism along Y from an ordered X/Z polygon."""
    pts = np.asarray(xz_points, dtype=float)
    n = len(pts)
    vertices = []
    for y in (-y_length / 2.0, y_length / 2.0):
        vertices.extend([[x, y, z] for x, z in pts])
    faces = []
    # Triangulated end caps; opposite winding on the two ends.
    for i in range(1, n - 1):
        faces.append((0, i + 1, i))
        faces.append((n, n + i, n + i + 1))
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j))
        faces.append((i, n + j, n + i))
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def build_holder():
    theta = math.radians(ANGLE_DEG)
    normal = np.array([math.sin(theta), 0.0, math.cos(theta)])

    tray_width = BOARD[0] + 2.0 * (BOARD_CLEARANCE + TRAY_MARGIN_X)
    tray_length = BOARD[1] + 2.0 * TRAY_MARGIN_Y

    # Put the lowest point of the inclined tray on the top of the base.
    tray_z0 = BASE_THICKNESS + math.sin(theta) * tray_width / 2.0

    board_underside_w = TRAY_BACK_THICKNESS + SOLDER_CLEARANCE
    board_top_w = board_underside_w + BOARD[2]
    led_tip_w = board_top_w + LED_BODY_HEIGHT

    # The centre of the LED tips must be 16.246 mm upstream of the slide centre.
    optical_shift = LED_TIP_TO_SLIDE_TOP_Z * math.tan(theta)
    tray_x0 = -optical_shift - normal[0] * led_tip_w
    transform = local_transform(tray_x0, tray_z0, ANGLE_DEG)

    led_tip_global = np.array([tray_x0, 0.0, tray_z0]) + normal * led_tip_w
    slide_top_z = led_tip_global[2] + LED_TIP_TO_SLIDE_TOP_Z
    slide_bottom_z = slide_top_z - SLIDE[2]

    solids = []

    # Base.
    solids.append(
        box(
            (BASE_X_MAX - BASE_X_MIN, BASE_Y_MAX - BASE_Y_MIN, BASE_THICKNESS),
            ((BASE_X_MIN + BASE_X_MAX) / 2.0, 0.0, BASE_THICKNESS / 2.0),
        )
    )

    # Solid wedge beneath the tray.  It makes the 18-degree surface printable
    # without support while the tray itself supplies a solder-side air gap.
    u0, u1 = -tray_width / 2.0, tray_width / 2.0
    x_left = tray_x0 + math.cos(theta) * u0
    z_left = tray_z0 - math.sin(theta) * u0
    x_right = tray_x0 + math.cos(theta) * u1
    z_right = tray_z0 - math.sin(theta) * u1
    wedge = prism_y(
        [(x_left, BASE_THICKNESS), (x_right, BASE_THICKNESS), (x_left, z_left)],
        tray_length,
    )
    solids.append(wedge)

    # Tilted tray back.
    solids.append(
        local_box(
            (tray_width, tray_length, TRAY_BACK_THICKNESS),
            (0.0, 0.0, TRAY_BACK_THICKNESS / 2.0),
            transform,
        )
    )

    # Side rails support the PCB with a 3 mm solder-joint cavity beneath it.
    rail_width = 2.0
    clear_width = BOARD[0] + 2.0 * BOARD_CLEARANCE
    rail_center_u = clear_width / 2.0 + rail_width / 2.0
    # Rails continue above the PCB so the short snap tabs are structurally
    # connected; their inner faces remain outside the board clearance envelope.
    rail_height = board_top_w + 1.0 - TRAY_BACK_THICKNESS
    for sign in (-1.0, 1.0):
        solids.append(
            local_box(
                (rail_width, tray_length, rail_height),
                (sign * rail_center_u, 0.0, TRAY_BACK_THICKNESS + rail_height / 2.0),
                transform,
            )
        )
        # Narrow ledge supporting the PCB edge.
        solids.append(
            local_box(
                (rail_width + 1.2, tray_length - 1.0, 0.6),
                (
                    sign * (clear_width / 2.0 - 0.6 + rail_width / 2.0),
                    0.0,
                    board_underside_w - 0.3,
                ),
                transform,
            )
        )
        # Four short snap tabs retain the board but leave most of the edge open.
        for v in (-25.0, 25.0):
            solids.append(
                local_box(
                    (rail_width + 1.0, 8.0, 0.8),
                    (
                        sign * (clear_width / 2.0 - 0.5 + rail_width / 2.0),
                        v,
                        board_top_w + 0.6,
                    ),
                    transform,
                )
            )

    # End stops locate the 75 mm PCB.  The board is pressed into the open top.
    board_clear_length = BOARD[1] + 2.0 * BOARD_CLEARANCE
    for sign in (-1.0, 1.0):
        solids.append(
            local_box(
                (tray_width, 1.75, board_top_w + 1.0 - TRAY_BACK_THICKNESS),
                (
                    0.0,
                    sign * (board_clear_length / 2.0 + 0.875),
                    (TRAY_BACK_THICKNESS + board_top_w + 1.0) / 2.0,
                ),
                transform,
            )
        )

    # Two end towers support only the ends of the microscope slide, preserving a
    # large unobstructed optical opening over its central 68 mm.
    slide_clear_length = SLIDE[1] + 2.0 * SLIDE_CLEARANCE
    tower_y_thickness = 4.4
    tower_inner_y = slide_clear_length / 2.0
    tower_width_x = 33.0
    tower_top_z = slide_top_z + 1.2
    for sign in (-1.0, 1.0):
        tower_center_y = sign * (tower_inner_y + tower_y_thickness / 2.0)
        solids.append(
            box(
                (tower_width_x, tower_y_thickness, tower_top_z - BASE_THICKNESS),
                (0.0, tower_center_y, (BASE_THICKNESS + tower_top_z) / 2.0),
            )
        )

        # 3.3 mm inward ledge; its top surface is exactly the slide underside.
        ledge_depth = 3.3
        ledge_center_y = sign * (tower_inner_y - ledge_depth / 2.0)
        solids.append(
            box(
                (29.0, ledge_depth, 3.0),
                (0.0, ledge_center_y, slide_bottom_z - 1.5),
            )
        )

        # Side guides at each slide corner.  A 0.30 mm clearance is included.
        guide_inner_x = SLIDE[0] / 2.0 + SLIDE_CLEARANCE
        guide_width = 1.8
        for xsign in (-1.0, 1.0):
            solids.append(
                box(
                    (guide_width, 6.0, 2.2),
                    (
                        xsign * (guide_inner_x + guide_width / 2.0),
                        sign * (tower_inner_y - 2.2),
                        slide_bottom_z + 0.9,
                    ),
                )
            )

    invalid = [
        (index, mesh.is_watertight, mesh.is_volume, float(mesh.volume))
        for index, mesh in enumerate(solids)
        if not mesh.is_volume
    ]
    if invalid:
        raise RuntimeError(f"Non-volume input solids: {invalid}")
    combined = trimesh.boolean.union(solids, engine="manifold")
    if isinstance(combined, list):
        combined = trimesh.util.concatenate(combined)

    # Four M4 clearance holes in the base for attaching the assembly.
    cutters = []
    for x in (-39.0, 21.0):
        for y in (-36.0, 36.0):
            cutters.append(
                trimesh.creation.cylinder(
                    radius=2.15,
                    height=BASE_THICKNESS + 2.0,
                    sections=48,
                    transform=trimesh.transformations.translation_matrix(
                        [x, y, BASE_THICKNESS / 2.0]
                    ),
                )
            )

    # Cable tunnel through the negative-Y PCB end stop and slide-support tower.
    # It opens directly into the 5 mm solder-side cavity, allowing the +12 V and
    # return wires to leave the assembly without being pinched under the PCB.
    cable_channel = local_box(
        (10.0, 15.0, SOLDER_CLEARANCE + 0.6),
        (
            0.0,
            -tray_length / 2.0 - 0.5,
            TRAY_BACK_THICKNESS + (SOLDER_CLEARANCE + 0.6) / 2.0,
        ),
        transform,
    )
    cutters.append(cable_channel)

    combined = trimesh.boolean.difference(
        [combined, trimesh.boolean.union(cutters, engine="manifold")],
        engine="manifold",
    )
    references = {
        "angle_deg": ANGLE_DEG,
        "led_tip_center_x": float(led_tip_global[0]),
        "led_tip_center_z": float(led_tip_global[2]),
        "slide_center_x": 0.0,
        "slide_bottom_z": float(slide_bottom_z),
        "slide_top_z": float(slide_top_z),
        "tip_to_slide_top_z": float(slide_top_z - led_tip_global[2]),
        "tip_to_slide_center_x": float(-led_tip_global[0]),
    }
    return combined, references


def render_preview(mesh, output: Path):
    """Write a simple orthographic isometric preview for visual QA."""
    width, height = 1200, 900
    margin = 55
    view = np.array([1.0, -1.25, 0.85], dtype=float)
    view /= np.linalg.norm(view)
    right = np.cross(np.array([0.0, 0.0, 1.0]), view)
    right /= np.linalg.norm(right)
    up = np.cross(view, right)
    up /= np.linalg.norm(up)

    vertices = np.asarray(mesh.vertices)
    px = vertices @ right
    py = -(vertices @ up)
    span_x = max(float(np.ptp(px)), 1.0)
    span_y = max(float(np.ptp(py)), 1.0)
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
    sx = (px - (px.min() + px.max()) / 2.0) * scale + width / 2.0
    sy = (py - (py.min() + py.max()) / 2.0) * scale + height / 2.0

    image = Image.new("RGB", (width, height), (245, 247, 250))
    draw = ImageDraw.Draw(image)
    centers = mesh.triangles_center
    order = np.argsort(centers @ view)
    light = np.array([0.4, -0.35, 0.85], dtype=float)
    light /= np.linalg.norm(light)
    normals = mesh.face_normals
    for face_index in order:
        face = mesh.faces[face_index]
        points = [(float(sx[i]), float(sy[i])) for i in face]
        shade = 0.35 + 0.65 * abs(float(np.dot(normals[face_index], light)))
        color = (
            int(40 + 55 * shade),
            int(70 + 85 * shade),
            int(95 + 105 * shade),
        )
        draw.polygon(points, fill=color, outline=(45, 55, 65))

    draw.text((20, 18), "18 deg oblique LED + 25 x 75 mm slide holder", fill=(20, 25, 30))
    draw.text((20, 42), "Preview only - STL units are millimetres", fill=(55, 60, 65))
    image.save(output)


def connected_body_summaries(mesh):
    """Summarize connected vertex components without requiring SciPy."""
    parent = np.arange(len(mesh.vertices), dtype=np.int64)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(a, b):
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[rb] = ra

    for a, b, c in mesh.faces:
        join(a, b)
        join(b, c)
    groups = {}
    for i in range(len(parent)):
        groups.setdefault(find(i), []).append(i)
    summaries = []
    vertices = np.asarray(mesh.vertices)
    for indices in groups.values():
        points = vertices[np.asarray(indices)]
        summaries.append((len(indices), points.min(axis=0).tolist(), points.max(axis=0).tolist()))
    return sorted(summaries, reverse=True)


def main():
    holder, refs = build_holder()
    output = OUT_DIR / "oblique_led_slide_holder_18deg_v2.stl"
    holder.export(output)
    render_preview(holder, OUT_DIR / "oblique_led_slide_holder_18deg_v2_preview.png")

    print(f"Wrote: {output}")
    print(f"Watertight: {holder.is_watertight}")
    components = connected_body_summaries(holder)
    print(f"Connected bodies: {len(components)}")
    if len(components) != 1:
        print(f"Component summaries: {components}")
    print(f"Euler number: {holder.euler_number}")
    print(f"Bounds mm: {holder.bounds.tolist()}")
    print(f"Volume mm^3: {holder.volume:.1f}")
    for key, value in refs.items():
        print(f"{key}: {value:.3f}")


if __name__ == "__main__":
    main()
