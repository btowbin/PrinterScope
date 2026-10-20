"""TIFF writing: uncompressed, lossless Deflate, or lossy JPEG 2000."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import tifffile

UNCOMPRESSED = "Uncompressed"
DEFLATE = "Lossless compressed (Deflate)"
JPEG2000 = "JPEG 2000 compressed (lossy)"
TIFF_FORMATS = (UNCOMPRESSED, DEFLATE, JPEG2000)
BIT_DEPTHS = (10, 14)
# Lossy JPEG 2000 quality is a target PSNR in dB, measured against the full scale of the
# camera bit depth. ~50 dB is roughly 3 counts RMS error at 10 bit, below typical shot noise.
DEFAULT_JPEG2000_PSNR_DB = 50.0
JPEG2000_PSNR_RANGE_DB = (20.0, 80.0)


def max_value(bit_depth: int) -> int:
    return (1 << int(bit_depth)) - 1


def jpeg2000_level(psnr_db: float, bit_depth: int) -> float:
    """imagecodecs' ``level`` is a PSNR target relative to the 16-bit container's full scale
    (65535). Our data only use ``bit_depth`` bits, so shift the target to be relative to
    the real full scale (2**bits - 1): the user-facing dB figure then means what it says."""
    return float(psnr_db) + 20.0 * math.log10(65535.0 / max_value(bit_depth))


def validate_psnr(psnr_db: float) -> None:
    low, high = JPEG2000_PSNR_RANGE_DB
    if not low <= float(psnr_db) <= high:
        raise ValueError(f"JPEG 2000 quality must be between {low:g} and {high:g} dB.")


def _compression_kwargs(tiff_format: str, bit_depth: int = 10,
                        psnr_db: float = DEFAULT_JPEG2000_PSNR_DB) -> dict:
    if tiff_format == UNCOMPRESSED:
        return {}
    if tiff_format == DEFLATE:
        # Horizontal differencing markedly improves Deflate on 16-bit camera data.
        return {"compression": "zlib", "predictor": True}
    if tiff_format == JPEG2000:
        # Lossy 9/7 wavelet with a target PSNR; pixel values are NOT bit-exact.
        validate_psnr(psnr_db)
        return {"compression": "jpeg2000",
                "compressionargs": {"level": jpeg2000_level(psnr_db, bit_depth)}}
    raise ValueError(f"Unknown TIFF format: {tiff_format}")


def write_tiff(path: Path, image: np.ndarray, bit_depth: int = 10,
               tiff_format: str = UNCOMPRESSED, metadata: dict | None = None,
               jpeg2000_psnr_db: float = DEFAULT_JPEG2000_PSNR_DB) -> None:
    """Write camera data (stored in uint16) as a monochrome TIFF."""
    meta = {"bit_depth": int(bit_depth)}
    if tiff_format == JPEG2000:
        meta["jpeg2000_target_psnr_db"] = float(jpeg2000_psnr_db)
    if metadata:
        meta.update(metadata)
    try:
        tifffile.imwrite(Path(path), image.astype(np.uint16, copy=False),
                         photometric="minisblack", metadata=meta,
                         **_compression_kwargs(tiff_format, bit_depth, jpeg2000_psnr_db))
    except ImportError as exc:
        raise RuntimeError(
            f"'{tiff_format}' needs the imagecodecs package: "
            "py -m pip install imagecodecs"
        ) from exc
