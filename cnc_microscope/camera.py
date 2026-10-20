from __future__ import annotations

from abc import ABC, abstractmethod
import importlib
import os
from pathlib import Path
import sys
import time

import numpy as np


_GALAXY_DLL_HANDLES: list[object] = []


def load_gxipy():
    """Import gxipy, auto-discovering the standard Galaxy SDK installation."""
    try:
        return importlib.import_module("gxipy")
    except ImportError as initial_error:
        roots: list[Path] = []
        for environment_name in ("ProgramFiles", "ProgramFiles(x86)"):
            program_files = os.environ.get(environment_name)
            if program_files:
                roots.append(Path(program_files) / "Daheng Imaging" / "GalaxySDK")

        checked: list[str] = []
        for root in roots:
            python_samples = root / "Development" / "Samples" / "Python"
            package = python_samples / "gxipy" / "__init__.py"
            checked.append(str(package))
            if not package.is_file():
                continue
            dll_directory = root / "APIDll" / "Win64"
            if dll_directory.is_dir() and hasattr(os, "add_dll_directory"):
                _GALAXY_DLL_HANDLES.append(os.add_dll_directory(str(dll_directory)))
            python_samples_text = str(python_samples)
            if python_samples_text not in sys.path:
                sys.path.insert(0, python_samples_text)
            try:
                return importlib.import_module("gxipy")
            except (ImportError, OSError) as exc:
                raise RuntimeError(
                    f"Galaxy SDK gxipy was found at {package}, but could not be loaded: {exc}"
                ) from exc

        locations = "; ".join(checked) if checked else "standard Galaxy SDK folders"
        raise RuntimeError(
            "gxipy was not found. Install the 64-bit Daheng Galaxy SDK with Python "
            f"samples enabled. Checked: {locations}"
        ) from initial_error


class Camera(ABC):
    is_connected: bool = False
    bit_depth: int = 10

    def set_bit_depth(self, bits: int) -> None:
        """Select the sensor/pixel bit depth (10 or 14); raise if unsupported."""
        if bits not in (10, 14):
            raise ValueError("Bit depth must be 10 or 14")
        self.bit_depth = bits

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def capture(self, exposure_ms: float) -> np.ndarray: ...


class SimulatedCamera(Camera):
    """Synthetic 10-bit camera; focus depends on the supplied Z callback."""

    def __init__(self, z_provider=lambda: 5.0, focus_z: float = 5.0, bit_depth: int = 10):
        self.bit_depth = bit_depth
        self.z_provider = z_provider
        self.focus_z = focus_z
        self.is_connected = False
        self._checker_mask: np.ndarray | None = None

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def capture(self, exposure_ms: float) -> np.ndarray:
        if not self.is_connected:
            raise RuntimeError("Camera is not connected")
        time.sleep(min(exposure_ms / 1000.0, 0.2))
        # Match the usable image size exposed by the Daheng IMX178 camera so ROI
        # settings behave identically in simulation and hardware modes.
        height, width = 2064, 3088
        if self._checker_mask is None:
            yy, xx = np.ogrid[:height, :width]
            self._checker_mask = ((xx // 48) + (yy // 48)) % 2 == 1
        defocus = abs(self.z_provider() - self.focus_z)
        contrast = max(0.03, float(np.exp(-((defocus / 0.18) ** 2))))
        scale = ((1 << self.bit_depth) - 1) / 1023.0
        image = np.full((height, width), round(220 * scale), dtype=np.uint16)
        image[self._checker_mask] = np.uint16(round((220 + 520 * contrast) * scale))
        return image


class DahengCamera(Camera):
    """Daheng MER/IMX178 adapter using the Galaxy SDK gxipy module."""

    def __init__(self, device_index: int = 1, bit_depth: int = 10):
        self.bit_depth = bit_depth
        self.device_index = device_index
        self.device_manager = None
        self.device = None
        self._gx = None
        self._acquiring = False
        self.is_connected = False

    def connect(self) -> None:
        gx = load_gxipy()
        self._gx = gx
        self.device_manager = gx.DeviceManager()
        count, _ = self.device_manager.update_device_list()
        if count < self.device_index:
            raise RuntimeError("No Daheng camera was detected")
        self.device = self.device_manager.open_device_by_index(self.device_index)
        try:
            self._apply_pixel_format(self.bit_depth)
        except Exception:
            self.device.close_device()
            self.device = None
            raise
        try:
            self.device.TriggerMode.set(gx.GxSwitchEntry.ON)
            self.device.TriggerSource.set(gx.GxTriggerSourceEntry.SOFTWARE)
        except Exception as exc:
            self.device.close_device()
            self.device = None
            raise RuntimeError(
                "Camera does not support the required software-triggered acquisition mode"
            ) from exc
        # The exposure field in the microscope UI must be authoritative.
        # A camera profile saved with automatic exposure/gain enabled can
        # otherwise override it and produce unexpectedly dark live frames.
        try:
            self.device.ExposureAuto.set(gx.GxAutoEntry.OFF)
        except Exception:
            pass
        try:
            self.device.GainAuto.set(gx.GxAutoEntry.OFF)
        except Exception:
            pass
        try:
            self.device.Gain.set(0.0)
        except Exception:
            # Gain is not writable on every Daheng model/configuration.
            pass
        try:
            # A small driver buffer limits latency if images arrive while the
            # stage is moving.
            self.device.data_stream[0].set_acquisition_buffer_number(2)
        except Exception:
            pass
        self.is_connected = True

    def _apply_pixel_format(self, bits: int) -> None:
        gx = self._gx
        entries = {10: ("MONO10", "MONO10_PACKED"), 14: ("MONO14", "MONO14_PACKED")}
        if bits not in entries:
            raise ValueError("Bit depth must be 10 or 14")
        last_error: Exception | None = None
        for entry_name in entries[bits]:
            entry = getattr(gx.GxPixelFormatEntry, entry_name, None)
            if entry is None:
                continue
            try:
                self.device.PixelFormat.set(entry)
                return
            except Exception as exc:  # unsupported by this camera model
                last_error = exc
        raise RuntimeError(
            f"This camera does not expose a {bits}-bit mono pixel format"
            + (f" ({last_error})" if last_error else "")
        )

    def set_bit_depth(self, bits: int) -> None:
        if bits not in (10, 14):
            raise ValueError("Bit depth must be 10 or 14")
        if self.device is None:
            self.bit_depth = bits
            return
        previous = self.bit_depth
        try:
            self._apply_pixel_format(bits)
        except Exception:
            try:  # restore the format that was working
                self._apply_pixel_format(previous)
            except Exception:
                pass
            raise
        self.bit_depth = bits

    def disconnect(self) -> None:
        if self.device is not None:
            try:
                if self._acquiring:
                    self.device.stream_off()
            finally:
                self.device.close_device()
        self.device = None
        self.device_manager = None
        self._gx = None
        self._acquiring = False
        self.is_connected = False

    def capture(self, exposure_ms: float) -> np.ndarray:
        if self.device is None:
            raise RuntimeError("Camera is not connected")
        self.device.ExposureTime.set(float(exposure_ms) * 1000.0)
        stream = self.device.data_stream[0]
        result: np.ndarray | None = None
        try:
            # Acquisition is deliberately inactive while the stage moves.
            # Start it only after settling, request exactly one exposure, copy
            # that frame, and stop acquisition again before returning.
            self.device.stream_on()
            self._acquiring = True
            time.sleep(0.1)  # Vendor examples allow acquisition start to settle.
            self.device.TriggerSoftware.send_command()
            raw = stream.get_image(timeout=max(1000, int(exposure_ms + 2000)))
            if raw is None:
                raise TimeoutError("Camera did not return a software-triggered image")
            frame_status = getattr(self._gx, "GxFrameStatusList", None)
            if frame_status is not None and raw.get_status() != frame_status.SUCCESS:
                raise RuntimeError("Camera returned an incomplete USB frame")
            array = raw.get_numpy_array()
            if array is None:
                raise RuntimeError("Camera returned an empty image")
            # Own the pixels before stream_off releases SDK-managed buffers.
            result = np.array(array, dtype=np.uint16, copy=True)
        finally:
            if self._acquiring:
                try:
                    self.device.stream_off()
                finally:
                    self._acquiring = False

        if result is None:
            raise RuntimeError("Camera acquisition ended without an image")
        # Packed/unpacked SDK paths can differ; guarantee the selected value range.
        limit = (1 << self.bit_depth) - 1
        if result.max(initial=0) > limit:
            result = result >> max(0, int(result.max()).bit_length() - self.bit_depth)
        return np.clip(result, 0, limit).astype(np.uint16, copy=False)

