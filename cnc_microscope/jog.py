"""Joystick jogging: turns a requested velocity into short, limit-checked move segments."""
from __future__ import annotations

from collections.abc import Callable
import queue
import threading
import time

from .models import Position


def axis_target(position: float, velocity: float, dt: float, low: float, high: float) -> float:
    """Next coordinate on one axis; never moves further past a bound it already violates."""
    if velocity == 0:
        return position
    target = position + velocity * dt
    if velocity < 0:
        return position if position <= low else max(target, low)
    return position if position >= high else min(target, high)


class JogRunner:
    """A single worker thread that owns all gamepad-driven motion.

    Velocity jogging sends one short segment per tick, but never queues more
    than ``max_ahead_s`` of motion in the firmware, so releasing the stick stops
    the stage almost at once. Discrete tasks (steps, autofocus) run on the same
    thread so they can never interleave with the segments.
    """

    def __init__(self, get_motion: Callable, *, z_floor: Callable[[], float] = lambda: 0.0,
                 blocked: Callable[[], bool] = lambda: False,
                 on_position: Callable[[Position], None] | None = None,
                 on_error: Callable[[Exception], None] | None = None,
                 tick_s: float = 0.05, max_ahead_s: float = 0.12):
        self.get_motion = get_motion
        self.z_floor = z_floor
        self.blocked = blocked
        self.on_position = on_position
        self.on_error = on_error
        self.tick_s = tick_s
        self.max_ahead_s = max_ahead_s
        self._velocity = (0.0, 0.0, 0.0)
        self._lock = threading.Lock()
        self._tasks: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._moving = False
        self._busy_until = 0.0
        self._running_task = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, wait: bool = False) -> None:
        self.set_velocity(0.0, 0.0, 0.0)
        self._stop.set()
        if wait and self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3.0)

    def halt(self) -> None:
        """Immediately stop issuing motion (used before an emergency stop)."""
        with self._lock:
            self._velocity = (0.0, 0.0, 0.0)
        try:
            while True:
                self._tasks.get_nowait()
        except queue.Empty:
            pass
        self._stop.set()

    def set_velocity(self, vx: float, vy: float, vz: float) -> None:
        with self._lock:
            self._velocity = (vx, vy, vz)

    def submit(self, task: Callable[[], None]) -> None:
        self._tasks.put(task)

    def is_busy(self) -> bool:
        with self._lock:
            velocity = self._velocity
        return (any(velocity) or self._moving or self._running_task
                or not self._tasks.empty())

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._tick()
            except Exception as exc:  # keep the worker alive; report and stand still
                self.set_velocity(0.0, 0.0, 0.0)
                self._moving = False
                if self.on_error:
                    self.on_error(exc)
            remaining = self.tick_s - (time.monotonic() - started)
            if remaining > 0:
                self._stop.wait(remaining)

    def _tick(self) -> None:
        try:
            task = self._tasks.get_nowait()
        except queue.Empty:
            task = None
        if task is not None:
            self._finish()
            self._running_task = True
            try:
                task()
            finally:
                self._running_task = False
                self._notify()
            return
        with self._lock:
            velocity = self._velocity
        if self.blocked() or not any(velocity):
            if self._moving:
                self._finish()
            return
        now = time.monotonic()
        if self._busy_until - now > self.max_ahead_s:
            return  # firmware queue is full enough
        if self._step(velocity, self.tick_s):
            self._moving = True
            self._busy_until = max(now, self._busy_until) + self.tick_s
            self._notify()
        elif self._moving:
            self._finish()

    def _step(self, velocity, dt: float) -> bool:
        motion = self.get_motion()
        if motion is None or not motion.is_homed:
            return False
        limits, p = motion.limits, motion.position
        vx, vy, vz = velocity
        target = Position(
            axis_target(p.x, vx, dt, limits.x_min, limits.x_max),
            axis_target(p.y, vy, dt, limits.y_min, limits.y_max),
            axis_target(p.z, vz, dt, max(limits.z_min, self.z_floor()), limits.z_max),
        )
        distance = ((target.x - p.x) ** 2 + (target.y - p.y) ** 2 + (target.z - p.z) ** 2) ** 0.5
        if distance < 1e-6:
            return False
        motion.jog_segment(target, max(1.0, distance / dt * 60.0))
        return True

    def _finish(self) -> None:
        remaining = self._busy_until - time.monotonic()
        if remaining > 0:
            time.sleep(min(remaining, 1.0))
        self._moving = False
        self._busy_until = 0.0
        motion = self.get_motion()
        if motion is not None and motion.is_homed:
            motion.finish_jog()
        self._notify()

    def _notify(self) -> None:
        motion = self.get_motion()
        if self.on_position and motion is not None and motion.is_connected:
            self.on_position(motion.position)
