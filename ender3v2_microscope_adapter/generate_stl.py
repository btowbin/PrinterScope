"""Generate a printable binary STL without requiring a local CAD installation.

The authoritative editable model is ender3v2_microscope_adapter.scad.  This
script voxelises the same geometry at 0.4 mm and emits only exposed faces.
"""

from pathlib import Path
import struct
import numpy as np

RESOLUTION = 0.4
OUT = Path(__file__).with_name("ender3v2_microscope_adapter.stl")
PREVIEW = Path(__file__).with_name("adapter_preview.png")


def capsule_distance_x(x, z, cx, half_segment):
    dx = np.maximum(np.abs(x - cx) - half_segment, 0.0)
    return np.sqrt(dx * dx + z * z)


def build_voxels():
    r = RESOLUTION
    xs = np.arange(-31.0 + r / 2, 31.0, r)
    ys = np.arange(-0.4 + r / 2, 23.2, r)
    zs = np.arange(-46.0 + r / 2, 46.0, r)
    x = xs[:, None, None]
    y = ys[None, :, None]
    z = zs[None, None, :]

    plate = (np.abs(x) <= 30) & (y >= 0) & (y <= 5) & (np.abs(z) <= 45)
    solid = plate.copy()

    for zc in (-24.0, 24.0):
        band = (
            (np.abs(x) <= 27)
            & (y >= 5)
            & (y <= 22)
            & (np.abs(z - zc) <= 7)
            & (y <= 7 + 0.55 * np.abs(x))
        )
        solid |= band

    # Nominal 14 mm M3 pattern, with 7 x 3.8 mm horizontal slots.
    mount_holes = np.zeros_like(solid)
    for xc in (-7.0, 7.0):
        mount_holes |= (
            capsule_distance_x(x, z, xc, (7.0 - 3.8) / 2) <= 1.9
        ) & (y >= -0.5) & (y <= 5.5)

    tie_slots = np.zeros_like(solid)
    for zc in (-24.0, 24.0):
        for xc in (-23.0, 23.0):
            tie_slots |= (
                (np.abs(x - xc) <= 2.5)
                & (y >= -0.5)
                & (y <= 22.5)
                & (np.abs(z - zc) <= 5.5)
            )

    solid &= ~(mount_holes | tie_slots)
    origin = np.array([xs[0] - r / 2, ys[0] - r / 2, zs[0] - r / 2])
    return solid, origin


def write_binary_stl(voxels, origin):
    r = RESOLUTION
    # Each entry: neighbour offset, outward normal, four face corners.
    faces = [
        ((-1, 0, 0), (-1, 0, 0), [(0,0,0),(0,0,1),(0,1,1),(0,1,0)]),
        (( 1, 0, 0), ( 1, 0, 0), [(1,0,0),(1,1,0),(1,1,1),(1,0,1)]),
        ((0, -1, 0), (0,-1, 0), [(0,0,0),(1,0,0),(1,0,1),(0,0,1)]),
        ((0,  1, 0), (0, 1, 0), [(0,1,0),(0,1,1),(1,1,1),(1,1,0)]),
        ((0, 0,-1), (0, 0,-1), [(0,0,0),(0,1,0),(1,1,0),(1,0,0)]),
        ((0, 0, 1), (0, 0, 1), [(0,0,1),(1,0,1),(1,1,1),(0,1,1)]),
    ]

    padded = np.pad(voxels, 1, constant_values=False)
    triangle_count = 0
    masks = []
    for (di, dj, dk), _, _ in faces:
        neighbour = padded[
            1+di:1+di+voxels.shape[0],
            1+dj:1+dj+voxels.shape[1],
            1+dk:1+dk+voxels.shape[2],
        ]
        mask = voxels & ~neighbour
        masks.append(mask)
        triangle_count += int(mask.sum()) * 2

    header = b"Ender 3 V2 universal microscope adapter".ljust(80, b" ")
    with OUT.open("wb") as f:
        f.write(header)
        f.write(struct.pack("<I", triangle_count))
        for mask, (_, normal, corners) in zip(masks, faces):
            indices = np.argwhere(mask)
            n = tuple(float(v) for v in normal)
            for idx in indices:
                base = origin + idx * r
                verts = [tuple(base + np.array(c) * r) for c in corners]
                for tri in ((0,1,2), (0,2,3)):
                    f.write(struct.pack("<3f", *n))
                    for vi in tri:
                        f.write(struct.pack("<3f", *verts[vi]))
                    f.write(struct.pack("<H", 0))

    return triangle_count


def write_preview(voxels):
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(12, 5), constrained_layout=True)
    views = [
        (voxels.max(axis=1).T, "Front", "X (left/right)", "Z (vertical)"),
        (voxels.max(axis=0).T, "Side", "Y (forward)", "Z (vertical)"),
        (voxels[:, :, np.argmin(np.abs(np.arange(voxels.shape[2]) -
                                      (voxels.shape[2] * 0.24)))].T,
         "Cradle section", "X (left/right)", "Y (forward)"),
    ]
    for ax, (data, title, xlabel, ylabel) in zip(axes, views):
        ax.imshow(data, origin="lower", cmap="Greys", interpolation="nearest")
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_aspect("equal")
    fig.suptitle("Ender 3 V2 universal microscope adapter")
    fig.savefig(PREVIEW, dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    voxels, origin = build_voxels()
    triangles = write_binary_stl(voxels, origin)
    write_preview(voxels)
    print(f"Wrote {OUT.name}: {triangles:,} triangles")
    print(f"Wrote {PREVIEW.name}")
    print(f"Voxel resolution: {RESOLUTION:.1f} mm")
    print(f"Solid voxels: {int(voxels.sum()):,}")

