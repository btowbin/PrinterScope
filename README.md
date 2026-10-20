# CNC Microscope Controller

Windows desktop software for an Ender 3 v2 converted into an XYZ microscope with a
Daheng IMX178 USB3 camera.

## Safety first

This software sends ordinary Marlin `G28`, `G0`, `M400`, and `M112` commands. On a
standard Ender 3, Z homing moves toward the lower Z end-stop. Before connecting real
hardware, verify the Z end-stop, objective clearance, cable routing, travel limits,
and direction of motion. Test with the objective removed or well clear of the sample.
The software confirmation dialog is not a substitute for a physical limit switch or
an accessible power switch.

## Install and run

1. Install 64-bit Python 3.11 or newer on Windows.
2. In PowerShell, open this folder and create an environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   py -m pip install -r requirements.txt
   ```

3. For real camera control, install the Daheng Galaxy Windows SDK and the matching
   `gxipy` package supplied with it. The application automatically discovers `gxipy`
   in the standard Galaxy SDK installation folder. The bundled binding requires NumPy
   1.26; the supplied requirements file therefore excludes NumPy 2.x. Connect the
   IMX178 directly to a USB3 port.
4. Start the application:

   ```powershell
   py run_microscope.py
   ```

   After this one-time setup, you can also double-click `start_microscope.bat`.

Simulation is enabled initially. Connect, confirm homing, jog to one or more useful
locations, and select **Add current** at each. Configure repeats, interval, exposure,
autofocus/Z-stack settings, and an output folder before starting acquisition.

The movement controls provide three profiles. **Coarse** uses 5 mm XY / 0.5 mm Z,
**Fine** uses 0.1 mm XY / 0.01 mm Z, and **Super fine** uses the standard Ender 3 v2
motor increments of 0.0125 mm XY / 0.0025 mm Z. Each profile also reduces the movement
rate. Select **Custom** to unlock independent X, Y, and Z step-size fields for a
calibrated machine or an asymmetric movement requirement.

Step presets are editable: double-click any value in the preset table (step sizes in
mm, speeds in mm/s) and type a new number. **Custom** is stored separately, so your
custom steps survive switching to Coarse, Fine or Super fine and back.

**Move to position (fast)** takes typed X, Y and Z values and repositions at the rapid
speeds shown beneath the fields: Z moves first when going up (retracting), XY moves
first when going down. Marlin still caps each axis at its own configured maximum speed.
This move does not apply the backlash approach; use a saved position's **Move to** button
when you need the backlash-corrected approach.

**Previous** and **Next** step through the position list, and **Update XYZ** sets the
selected position to the current stage coordinates in one click. While a position is selected, the
arrow keys work as Previous/Next (Up or Left is previous, Down or Right is next), except
while you are typing in a text field.

In **Positions of interest**, select a position, move to it, then move away and use
**Update from current**: re-teach the selected position (X/Y/Z, X/Y only, or Z only), or
apply the same shift (current position minus the selected position) to every position on
all axes, X/Y only, or Z only. Shifting all positions asks for confirmation first.

Position tracking: after homing, and after every move, the controller reads the
position back from Marlin (`M114`) rather than trusting its own bookkeeping. Homing
therefore respects any firmware home offset, a move that ends more than 0.02 mm from its
target raises an error and resynchronises the display to the printer's value, and
**Read position from printer** re-reads it after you move the stage from the printer's
LCD. After an emergency stop, or if the position cannot be read, the app requires homing
again. Note that Marlin reports where the firmware believes the axes are, so this catches
offsets and communication problems but cannot detect steps lost mechanically; that would
need a physical encoder.

Backlash correction is enabled by default. Before every saved stage position, the
controller retracts Z, preloads X and Y below the target, finishes X/Y in the positive
direction, and finishes Z in the negative direction toward the sample. Positions must
have enough clearance from the travel limits for the configured preload distances.

**Start live view** shows the camera while manual stage controls remain active.
**Capture & save snapshot** takes a fresh exposure and saves it in the selected output
folder. **Save current view** saves the last displayed full-resolution frame without
taking a new exposure. Hardware acquisition is software-triggered: the camera is not
left streaming during a stage move. For every frame, acquisition starts only after the
stage has settled, one exposure is triggered and copied, and acquisition then stops.
Live view repeats this single-frame sequence and can therefore be slower than continuous
streaming.

## Autofocus and the position log

Autofocus scans a Z-stack around the position and moves to the sharpest plane. Tick **Two-step
autofocus** to follow the normal (coarse) scan with a fine scan centred on its peak, using the
**Fine range** and **Fine spacing** fields. The fine range must be at least twice the coarse spacing
so it covers the gap between coarse planes, and the fine spacing must be smaller than the coarse
spacing. If the coarse peak lands on the edge of the coarse range, the fine scan simply extends
beyond it. With **Save Z-stack** on, both scans are saved: `…_ZStack.tif` (coarse) and
`…_ZStackFine.tif`. The gamepad's B button uses the same autofocus settings.

**Staggered autofocus** saves time on long time-lapses. The first time point autofocuses every
position. After that, time point t (counting from 0) refocuses only positions t, t+n, t+2n, ...
(positions counted from 1), so each position is refocused once every n time points. With n = 3 and
seven positions, the time points are: all seven, then 1, 4, 7, then 2, 5, then 3, 6, then 1, 4, 7
again. Positions that are not refocused are imaged at their last focused position (including any
well-centering correction), and no Z-stack is saved for them. Scans are still centred on the
position's stored Z, so total drift should stay within half the scan range. The `autofocus` column in
`positions.csv` says whether a row was refocused.

Every acquisition also writes `positions.csv` in the output folder, one row per saved image:
the run start time, timestamp and elapsed seconds, time index, point index and name, the planned
X/Y/Z of the position, the actual X/Y/Z where the image was captured (after autofocus and well
centering), and the names of the image and Z-stack files. A new run appends to the same file.
If the file cannot be written, for example because it is open in Excel, the status bar says so once
and the acquisition carries on.

## Finding the printer's port

With Simulation off, **Refresh** lists the serial ports and pre-selects the one that looks
like an Ender 3 (the CH340 USB chip on its mainboard; Bluetooth ports are ignored).
**Auto-detect** goes further: it asks each likely port for Marlin's firmware reply (`M115`),
trying 115200 and then 250000 baud, and selects the port and baud rate that answer. It takes
a few seconds per port because Ender boards reset when a port is opened, and it only talks to
USB serial adapters (or, if none look like a printer, to every non-Bluetooth port). Close
Cura, Pronterface or any other program using the printer first.

## Positions list: undo

Every change to the positions list can be undone with **Undo** (or Ctrl+Z when no text field
has focus): adding, removing, **Clear all**, updating one or all positions, and loading a
list or a session. Undo steps back through up to 100 changes. The history is not saved with
the session.

## Remembered settings and sessions

The app remembers its settings between runs: it saves them when you close it (and every 30
seconds), and restores them at the next start. This covers the hardware mode, serial port and
baud rate, movement presets, rapid speeds, backlash, acquisition and autofocus settings, well
centering, ROI, output folder, image format, display scaling, gamepad settings and the list of
positions. Nothing that moves the stage is restored; you still connect and home yourself.
The file is `last_session.json` in `%APPDATA%\CNCMicroscope` on Windows.

Use **File → Save session…** (Ctrl+S) to write the same information to a file of your choice,
for example one per experiment, and **File → Load session…** (Ctrl+O) to bring it back. While the
stage is connected, loading keeps the current hardware mode and port. A saved value that is no
longer valid, such as a serial port that is not present, is ignored and the status bar says so.
**File → Forget remembered settings** deletes the automatic file so the next start uses the defaults.

## Gamepad control

Tick **Gamepad control** in the Hardware box to drive the stage with an Xbox-style
(XInput) controller such as the 8BitDo Ultimate 2C Wired. Connect and home first; the
pad is ignored while an acquisition runs.

| Control | Action |
| --- | --- |
| Left stick | Move X/Y; a harder push is faster (up to the active preset's XY speed) |
| Right stick up/down | Move Z (up = retract); slower, a fraction of the preset's Z speed |
| D-pad | One XY step (hold to repeat) |
| LB / RB | One Z step down (approach) / up (retract) |
| A | Add the current position |
| B | Autofocus here, using the Z range, spacing and focus measure in the acquisition panel |
| X | Capture and save a snapshot |
| Y | Cycle the speed level: Coarse, Fine, Super fine |

The speed level is the movement preset, so it sets both the stick speeds and the D-pad and
LB/RB step sizes. **Gamepad settings…** adjusts the stick deadzone, the response curve
(exponent 1 is linear, 2 is gentler near the centre), the Z stick speed fraction, axis
inversion, and a **lowest Z** the gamepad may reach, which protects the objective and sample.
Stick movement stops at the travel limits and at that Z floor, and the stage stops when you
release the stick or unplug the controller. If the controller does not respond, check the
mode switch on it (if it has one) is set to XInput.

## Images and display

The camera runs at its maximum 10-bit depth (the IMX178 has no 12- or 14-bit mode); data
are stored in 16-bit TIFF containers. Choose **Image format** before acquiring or saving:
uncompressed, lossless Deflate, or **lossy** JPEG 2000 (needs `imagecodecs`, included in
`requirements.txt`). Uncompressed and Deflate are bit-exact; JPEG 2000 is not. Its quality
is a target PSNR in dB relative to the full scale of the camera bit depth (default 50 dB,
roughly 3 counts RMS error at 10 bit, below typical shot noise; higher = better and
larger files, allowed range 20 to 80 dB). The target is recorded in the TIFF metadata.
Note that many viewers, including plain ImageJ/Fiji, cannot open JPEG 2000 TIFFs without
an extra plugin.

The preview has an **Autoscale** option and a histogram (log scale) with draggable blue
(minimum) and red (maximum) markers. Dragging a marker switches autoscale off; **Full
range** shows 0 to 1023. These controls affect only the display, never the saved data.

## Autofocus measures

The **Focus measure** menu provides these built-in options:

- **Tenengrad** — gradient energy; a robust general-purpose default.
- **Variance of Laplacian** — emphasizes fine edges and small high-frequency detail.
- **Image variance** — simple global contrast; best when illumination and specimen
  composition remain stable throughout the Z-stack.
- **Brenner gradient** — a fast two-pixel gradient measure.
- **Custom Python function** — loads a function from a selected local `.py` file.

Every measure uses the plane with the highest score as the best-focused plane.

### Custom autofocus function contract

Only select Python files you trust: selecting a custom function executes that file as
ordinary Python code on the computer. The file must define a function with this exact
name and signature:

```python
def focus_measure(image):
    # Calculate and return one score.
    return score
```

The function input and output must follow these rules:

- `image` is a two-dimensional NumPy array from the monochrome camera.
- Its dtype is `numpy.uint16`, with valid 10-bit pixel values from 0 through 1023.
- The function must return one value convertible to a Python `float`. A Python number
  or a scalar NumPy number is accepted; arrays, tuples, and dictionaries are not.
- The returned score must be finite—not NaN or positive/negative infinity.
- A **larger score must mean better focus**, because the software selects the maximum.
- Negative scores are allowed if larger values still represent better focus.
- Do not modify `image` in place, because the same frame may be previewed or saved as
  part of a Z-stack.
- An exception from the function stops the acquisition and is shown in the interface.

The file is loaded once when acquisition starts, and the function is called once for
each autofocus Z-plane. Keep it reasonably fast so it does not delay acquisition.
See `custom_focus_example.py` for a complete working example.

## Automatic well centering

Enable **Center well in XY after Z AF** to identify the well in the focused image and
move it to the center of the camera field. Enabling this option also enables autofocus.
Use **Well centering settings…** to choose the detector, calibration, axis direction,
and maximum permitted XY correction.

The sequence at every stage position is:

1. Acquire the autofocus Z-stack and select the best Z-plane.
2. Capture a fresh image at that Z position.
3. Detect the well center in that image.
4. Convert its pixel offset to a calibrated XY stage correction.
5. Move XY with the configured backlash compensation while returning to the selected Z.
6. Capture and save a fresh, centered final image.

The built-in **Gradient centroid** detector smooths small bacterial texture and finds
the weighted center of the strongest large-scale edges. It works best when most of the
well boundary is visible and has stronger contrast than the worm and bacterial texture.
Always verify it on representative wells before unattended acquisition. The maximum
XY correction prevents a poor detection from commanding an unexpectedly large move.

### Well-centering calibration

Set X and Y calibration to the specimen-plane distance represented by one image pixel,
in micrometres per pixel. It can be measured by commanding a known stage displacement
and measuring the resulting image displacement:

```text
µm per pixel = commanded displacement in µm / observed displacement in pixels
```

The software calculates the image error from the frame center and applies:

```text
stage correction in mm = image error in pixels × µm per pixel / 1000
```

If a test correction moves the detected well farther from the image center, select
**Invert X correction** or **Invert Y correction** for that axis. Test this at low speed
with generous objective clearance before enabling unattended centering.

### Custom well-detector contract

Only select Python files you trust. A custom well-detector file executes as ordinary
Python code and must define this exact function:

```python
def find_well_center(image):
    return x_pixel, y_pixel
```

The custom function must satisfy the following contract:

- `image` is a two-dimensional `numpy.uint16` array with 10-bit values from 0 to 1023.
- Return exactly two numeric values: `(x_pixel, y_pixel)`.
- Coordinates are zero-based image coordinates. X is the column measured left-to-right;
  Y is the row measured top-to-bottom.
- Both coordinates must be finite and inside the image bounds.
- Floating-point/subpixel coordinates are accepted.
- Do not modify the input image in place.
- Raise an exception with an explanatory message if the well cannot be identified. The
  software will stop the acquisition rather than move using an uncertain result.

See `custom_well_detector_example.py` for a complete example. For real samples, a
custom function can use segmentation, circular fitting, or a trained model, provided
its final output follows the same `(x_pixel, y_pixel)` contract.

## Image and acquisition behavior

- Files are named `Point0001_Time00001.tif`, and so on.
- Pixel values are 0–1023 (10-bit) in a broadly compatible unsigned 16-bit TIFF.
- Saved stacks are multi-page files named `Point0001_Time00001_ZStack.tif`.
- Autofocus scans the configured range around the saved point Z, uses a Tenengrad
  gradient focus score, scans high-to-low for a consistent Z approach, returns to the
  best Z with backlash preload, then captures a fresh final image.
- The interval is measured between the starts of time-point cycles. If acquisition
  takes longer than the interval, the next cycle begins immediately.
- Illumination is assumed to remain continuously on and is not controlled.

## Camera region of interest

Open **Camera ROI settings…** below the image preview to use only part of each camera
frame. The Daheng MER2-630 camera exposes a maximum IMX178 image of **3088 × 2064
pixels**. Enable the ROI and enter:

- **ROI width** — number of centered columns to use; `0` uses the full width.
- **ROI height** — number of centered rows to use; `0` uses the full height.

The X and Y origins are calculated automatically so the crop remains centered on the
camera image:

```text
X origin = (3088 − ROI width) / 2
Y origin = (2064 − ROI height) / 2
```

Integer pixel coordinates are rounded down. For example, a 1000 × 800 ROI begins at
X 1044 and Y 632. Width cannot exceed 3088 and height cannot exceed 2064.

The selected ROI is applied consistently to live view, autofocus scoring, custom focus
functions, well detection, snapshots, final TIFF images, and saved Z-stacks. Custom
functions therefore receive the cropped image, and custom well-detector coordinates
are relative to the cropped ROI. Automatic well centering centers the detected well
inside the selected ROI.

This implementation is a software ROI: the camera still transfers the full frame
over USB and the application crops it immediately afterward. It reduces saved image
dimensions and processing time, but does not increase the camera's sensor readout rate
or reduce USB bandwidth. An invalid ROI extending outside the actual camera frame stops
capture with an explanatory error.

The simulated camera also generates 3088 × 2064 frames, so centered ROI dimensions can
be tested with the same limits before connecting the physical camera.

## Hardware notes

The default Ender 3 serial rate is 115200 baud; some firmware uses 250000. The default
software travel limits are X 0–220 mm, Y 0–220 mm, and Z 0–250 mm. Edit
`MotionLimits` in `cnc_microscope/motion.py` if the safe physical envelope is smaller.
The Galaxy SDK varies slightly by release; the camera adapter tries `Mono10` first and
then `Mono10Packed`.

## Tests

```powershell
py -m pip install pytest
py -m pytest
```

