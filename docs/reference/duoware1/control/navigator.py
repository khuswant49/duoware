"""
DUO-WARE Gyro Waypoint Navigator ("Turn, then Drive Straight, then Brake on Time")

The overhead camera decides WHERE to go; the ESP32's MPU gyro does the precise motion:
  1. TURNING  : Camera computes the bearing error to the target -> one closed-loop gyro
                turn on the ESP32 ('T<deg>'). No more streaming L/R every frame.
  2. SETTLING : Wait briefly so the camera + heading filter catch up, then re-check.
                If still off by more than align_tol_deg, turn again (small correction).
  3. DRIVING  : Drive forward on a gyro-locked heading ('H<yaw>'), speed set with 'V<pwm>'.
                The camera nudges the locked heading if the car drifts, and a distance
                profile slows the car down as it approaches the target.
  4. BRAKING  : Predictive reverse brake ('X<ms>'). The car's speed is measured from the
                camera; the brake fires when the remaining distance equals the stopping
                distance (reaction delay + braking distance), so the car comes to rest ON
                the target instead of rolling past it. After each stop the actual
                overshoot is measured and the reaction delay is corrected for next time.
                Accelerometer assist: the brake command carries the car's speed
                ('X<ms>,<m/s>'); the ESP32 integrates the measured deceleration and cuts
                the reverse the moment the car has stopped (no backwards roll). It then
                reports the speed it removed, from which the navigator learns the
                px->m/s scale needed for that cut.
  5. ARRIVED  : Stopped within arrive_radius_px (or just past the target).
  6. STALLED  : Driving but not getting closer (blocked / wheels slipping) -> brake,
                wait stall_retry_sec, then try again with a fresh turn.

Robustness:
  - If the target marker disappears mid-approach (usually because the car is driving
    over it), the last seen target position is kept.
  - If the car marker is briefly lost while driving (motion blur), its position is
    dead-reckoned from the measured speed so the brake still fires on time.

Sign convention (matches firmware + vision): positive heading error = target is to the
RIGHT = positive gyro yaw.

If the gyro is not ready (no MPU / still calibrating), turning falls back to short
open-loop pulse steps ('<' / '>') so navigation still works, just more slowly.
"""

import math
import time
import logging
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger("duo_ware.navigator")


class NavState(str, Enum):
    IDLE = "IDLE"
    TURNING = "TURNING"
    SETTLING = "SETTLING"
    DRIVING = "DRIVING"
    BRAKING = "BRAKING"
    ARRIVED = "ARRIVED"
    WAITING_VISION = "WAITING_VISION"
    STALLED = "STALLED"


# States in which a vanished target marker is remembered instead of cancelling the approach
_ACTIVE_STATES = (NavState.TURNING, NavState.SETTLING, NavState.DRIVING,
                  NavState.BRAKING, NavState.WAITING_VISION, NavState.STALLED,
                  NavState.ARRIVED)  # ARRIVED: the parked car usually covers the target marker


@dataclass
class NavConfig:
    arrive_radius_px: float = 30.0      # Stop when this close to the target
    near_radius_px: float = 70.0        # Inside this, no more steering (bearing gets unstable)
    decel_radius_px: float = 160.0      # Start slowing down inside this distance
    align_tol_deg: float = 7.0          # Heading error accepted before driving
    realign_deg: float = 30.0           # While driving, stop and re-turn if error exceeds this
    steer_deg: float = 4.0              # While driving, nudge the gyro heading above this error
    steer_interval_sec: float = 0.4     # Min time between heading nudges
    cruise_pwm: int = 125
    min_pwm: int = 85                   # Slowest driving PWM that still moves the car
    speed_step_pwm: int = 8             # Only resend speed when it changes this much
    settle_sec: float = 0.35            # Camera/filter catch-up after a turn
    turn_min_sec: float = 0.25          # Ignore telemetry this long after sending a turn (5 Hz lag)
    turn_timeout_sec: float = 4.5       # Matches firmware 4 s turn watchdog + margin
    max_turn_retries: int = 3           # Correction turns before driving anyway
    pulse_step_sec: float = 0.30        # Pause between pulse steps when gyro is unavailable
    lost_vision_sec: float = 0.5        # Brake if the car marker is lost this long while driving
    stall_sec: float = 3.0              # Driving this long without progress = stalled
    stall_progress_px: float = 10.0     # Progress needed within stall_sec
    stall_retry_sec: float = 2.0        # Pause before retrying after a stall
    # --- Predictive braking ---
    stop_offset_px: float = 0.0         # Point that should stop on the target, measured forward
                                        # from the car marker (e.g. marker at rear -> car centre)
    brake_latency_sec: float = 0.20     # Camera + processing + Bluetooth reaction delay
    brake_decel_px_s2: float = 2000.0   # Deceleration while motors are reversed
    brake_min_ms: int = 40
    brake_max_ms: int = 250
    brake_min_speed_px_s: float = 40.0  # Slower than this -> plain stop ('K') is enough
    brake_settle_sec: float = 0.35      # Wait after the brake before measuring where it stopped
    brake_learn_rate: float = 0.5       # How much of each stop's error is learned (0..1)
    brake_extra_limits: Tuple[float, float] = (-0.15, 0.6)  # Learned delay correction range (s)
    brake_learn_max_step_sec: float = 0.08  # One stop can shift the learned delay by at most this
    dead_reckon_sec: float = 0.6        # Predict position this long while the car marker is lost
    # --- Accelerometer brake assist (firmware reports brakeDv / brakeStopMs / brakeId) ---
    brake_assist_max_ms: int = 350      # Reverse limit when the ESP32 can cut the brake at the stop
    accel_learn_rate: float = 0.4       # How fast the px->m/s scale adapts
    # --- Measurement sanity limits ---
    max_speed_px_s: float = 1500.0      # Ignore camera speed readings above this (detection jumps)
    pass_margin_px: float = 12.0        # Moving away from the closest point by this much = passed the target
    unit: str = "px"                    # Label for distances in status text ("mm" in multi-camera mode)

    # Fields holding distances (px), speeds (px/s) or accelerations (px/s^2). The navigator itself is
    # unit-agnostic; scaled() converts these, e.g. to millimetres for the multi-camera world frame.
    _DISTANCE_FIELDS = ("arrive_radius_px", "near_radius_px", "decel_radius_px", "stall_progress_px",
                        "stop_offset_px", "brake_decel_px_s2", "brake_min_speed_px_s",
                        "max_speed_px_s", "pass_margin_px")

    def scaled(self, factor: float, unit: Optional[str] = None) -> "NavConfig":
        """Copy with every distance-based setting multiplied by factor (e.g. mm per px)."""
        if factor == 1.0:
            return replace(self, unit=unit or self.unit)
        return replace(self, unit=unit or self.unit,
                       **{f: getattr(self, f) * factor for f in self._DISTANCE_FIELDS})


class GyroWaypointNavigator:
    """Drives the car to one target point at a time. Call update() every perception frame."""

    def __init__(self, config: Optional[NavConfig] = None):
        self.config = config or NavConfig()
        # Learned reaction-delay correction (s). Positive = brake earlier. Survives reset().
        self.brake_extra_sec: float = 0.0
        self.last_stop_error_px: Optional[float] = None
        # Accelerometer-learned values. Survive reset().
        self.mps_per_px: Optional[float] = None   # Camera px/s -> car m/s
        self.brake_report_id: Optional[int] = None
        self.pending_brake_speed_px_s: Optional[float] = None
        self.reset()

    def reset(self):
        self.state = NavState.IDLE
        self.target: Optional[Tuple[float, float]] = None
        self.target_held: bool = False
        self.last_dist: float = 0.0
        self.last_err: float = 0.0
        self.min_dist_seen: float = float("inf")
        self.turn_retries: int = 0
        self.state_since: float = 0.0
        self.last_steer_time: float = 0.0
        self.last_seen_time: float = 0.0
        self.last_pos: Optional[Tuple[float, float]] = None
        self.last_angle: Optional[float] = None
        self.speed_px_s: float = 0.0
        self.sent_speed: Optional[int] = None
        self.locked_heading: Optional[float] = None
        self.moving: bool = False
        self.progress_dist: float = float("inf")
        self.progress_time: float = 0.0
        self.brake_ms: int = 0
        self.brake_speed_px_s: float = 0.0
        self.brake_clean: bool = False
        self.brake_dir: Tuple[float, float] = (1.0, 0.0)
        self.status: str = "Idle"

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _wrap(deg: float) -> float:
        return math.degrees(math.atan2(math.sin(math.radians(deg)), math.cos(math.radians(deg))))

    @staticmethod
    def geometry(car_pos: Tuple[float, float], car_angle: float,
                 target: Tuple[float, float]) -> Tuple[float, float]:
        """Returns (distance_px, heading_error_deg). Positive error = target is to the right."""
        dx = target[0] - car_pos[0]
        dy = target[1] - car_pos[1]
        err = math.degrees(math.atan2(dy, dx)) - car_angle
        return math.hypot(dx, dy), GyroWaypointNavigator._wrap(err)

    def _stop_point(self, pos: Tuple[float, float], angle: float) -> Tuple[float, float]:
        off = self.config.stop_offset_px
        if not off:
            return pos
        a = math.radians(angle)
        return (pos[0] + off * math.cos(a), pos[1] + off * math.sin(a))

    def _set_state(self, state: NavState, now: float):
        if state != self.state:
            logger.info(f"[NAV] {self.state.value} -> {state.value}")
        self.state = state
        self.state_since = now

    @staticmethod
    def _gyro(client) -> Dict[str, Any]:
        try:
            return client.get_gyro_telemetry() or {}
        except Exception:
            return {}

    def _gyro_ready(self, client) -> bool:
        g = self._gyro(client)
        age = g.get("ageSec")
        if age is None and "ageSec" in g:
            return False  # Real link that has never delivered telemetry
        if age is not None and age > 1.0:
            return False  # Telemetry stopped arriving -> do not trust stale yaw/state
        return bool(g.get("gyroConnected")) and bool(g.get("gyroCalibrated"))

    def brake(self, client):
        """Stops the car if the navigator started it moving."""
        if self.moving:
            client.send_line("K")
            self.moving = False
            self.sent_speed = None

    def _set_speed(self, client, pwm: int):
        if self.sent_speed is None or abs(pwm - self.sent_speed) >= self.config.speed_step_pwm:
            client.send_line(f"V{pwm}")
            self.sent_speed = pwm

    def _speed_for(self, dist: float, cruise: int) -> int:
        c = self.config
        cruise = max(c.min_pwm, cruise)
        if dist >= c.decel_radius_px:
            return cruise
        frac = (dist - c.arrive_radius_px) / max(1.0, c.decel_radius_px - c.arrive_radius_px)
        frac = max(0.0, min(1.0, frac))
        return int(c.min_pwm + (cruise - c.min_pwm) * frac)

    def stopping_distance(self, speed_px_s: Optional[float] = None) -> float:
        """Distance the car still travels after the brake decision at the given speed."""
        c = self.config
        v = self.speed_px_s if speed_px_s is None else speed_px_s
        return v * (c.brake_latency_sec + self.brake_extra_sec) + v * v / (2.0 * c.brake_decel_px_s2)

    def _learn_from_accel(self, client):
        """Uses the ESP32's accelerometer report of the last brake (speed removed, time to stop)."""
        g = self._gyro(client)
        rid = g.get("brakeId")
        if rid is None:
            return
        if self.brake_report_id is None or rid == self.brake_report_id:
            self.brake_report_id = rid
            return
        self.brake_report_id = rid
        v_px = self.pending_brake_speed_px_s
        self.pending_brake_speed_px_s = None
        dv = float(g.get("brakeDv") or 0.0)
        if not v_px or dv <= 0.02:
            return
        c = self.config
        k = dv / v_px
        self.mps_per_px = k if self.mps_per_px is None else self.mps_per_px + c.accel_learn_rate * (k - self.mps_per_px)
        stop_ms = float(g.get("brakeStopMs") or 0.0)
        # Braking strength is not learned from stop_ms: the car is often already slowing when the
        # brake fires, which skews it. Stopping-distance errors are learned via brake_extra_sec.
        logger.info(f"[NAV] Accelerometer: brake removed {dv:.2f} m/s from {v_px:.0f} {self.config.unit}/s "
                    f"({'cut at stop after ' + str(int(stop_ms)) + ' ms' if stop_ms else 'timed'}) "
                    f"-> scale {self.mps_per_px * 1000:.3f} mm per {self.config.unit}")

    # ------------------------------------------------------------------ actions

    def _start_turn(self, err: float, client, now: float):
        self.brake(client)
        if self._gyro_ready(client):
            client.send_line(f"T{err:.1f}")
            self.status = f"Gyro turn {err:+.1f}°"
        else:
            # No gyro: short open-loop pulse toward the target, then re-check via camera
            client.send_line(">" if err > 0 else "<")
            self.status = f"Pulse turn {'right' if err > 0 else 'left'} (gyro not ready, err {err:+.1f}°)"
        self.moving = True
        self._set_state(NavState.TURNING, now)

    def _start_drive(self, dist: float, err: float, client, now: float, cruise: int):
        self._set_speed(client, self._speed_for(dist, cruise))
        g = self._gyro(client)
        if self._gyro_ready(client) and "yaw" in g:
            # Lock the gyro heading, pre-corrected by the small remaining camera error
            self.locked_heading = self._wrap(float(g["yaw"]) + err)
            client.send_line(f"H{self.locked_heading:.1f}")
        else:
            self.locked_heading = None
            client.send_line("F")
        self.moving = True
        self.speed_px_s = 0.0
        self.min_dist_seen = dist
        self.progress_dist, self.progress_time = dist, now
        self.last_steer_time = now
        self._set_state(NavState.DRIVING, now)
        self.status = f"Driving straight ({dist:.0f}{self.config.unit})"

    def _start_brake(self, dist: float, client, now: float, reason: str):
        """Fires the reverse brake sized to the current speed."""
        c = self.config
        v = self.speed_px_s
        self.brake_speed_px_s = v
        # Only learn from stops where the camera actually saw the car when the brake fired
        self.brake_clean = reason != "estimated" and now - self.last_seen_time <= 0.2
        if self.last_angle is not None:
            a = math.radians(self.last_angle)
            self.brake_dir = (math.cos(a), math.sin(a))
        if v < c.brake_min_speed_px_s:
            self.brake_ms = 0
            client.send_line("K")
        elif self.mps_per_px is not None:
            # Accelerometer assist: allow a longer reverse; the ESP32 cuts it when the car has stopped
            planned = v / c.brake_decel_px_s2 * 1000.0
            self.brake_ms = int(max(c.brake_min_ms, min(c.brake_assist_max_ms, planned * 1.6 + 30)))
            client.send_line(f"X{self.brake_ms},{v * self.mps_per_px:.3f}")
            self.pending_brake_speed_px_s = v
        else:
            self.brake_ms = int(max(c.brake_min_ms, min(c.brake_max_ms, v / c.brake_decel_px_s2 * 1000.0)))
            client.send_line(f"X{self.brake_ms}")
            self.pending_brake_speed_px_s = v  # First brakes teach the px->m/s scale
        self.moving = False
        self.sent_speed = None
        self._set_state(NavState.BRAKING, now)
        self.status = (f"Braking {self.brake_ms}ms at {v:.0f}{self.config.unit}/s, {dist:.0f}{self.config.unit} out "
                       f"(stop dist {self.stopping_distance():.0f}{self.config.unit}) - {reason}")
        logger.info(f"[NAV] {self.status}")

    def _finish_brake(self, stop_pt: Tuple[float, float], dist: float, now: float) -> NavState:
        """Measures where the car stopped, learns from it, and decides arrived vs re-approach."""
        c = self.config
        t = self.target
        # Signed distance along the travel direction: + = rolled past the target, - = stopped short
        along = (stop_pt[0] - t[0]) * self.brake_dir[0] + (stop_pt[1] - t[1]) * self.brake_dir[1]
        self.last_stop_error_px = along
        if self.brake_clean and self.brake_speed_px_s >= c.brake_min_speed_px_s:
            # Rolled past by `along` px at speed v -> the effective delay was along / v longer
            step = c.brake_learn_rate * along / self.brake_speed_px_s
            step = max(-c.brake_learn_max_step_sec, min(c.brake_learn_max_step_sec, step))
            lo, hi = c.brake_extra_limits
            self.brake_extra_sec = max(lo, min(hi, self.brake_extra_sec + step))
        logger.info(f"[NAV] Stopped {abs(along):.0f}{self.config.unit} {'past' if along > 0 else 'short of'} target "
                    f"-> brake delay correction now {self.brake_extra_sec * 1000:+.0f}ms"
                    f"{'' if self.brake_clean else ' (not learned: car was not visible at brake time)'}")
        if dist <= c.arrive_radius_px * 1.5 or along > 0:
            self._set_state(NavState.ARRIVED, now)
            self.status = f"Arrived ({dist:.0f}{self.config.unit}, {abs(along):.0f}{self.config.unit} {'past' if along > 0 else 'short'})"
        else:
            self.turn_retries = 0
            self._set_state(NavState.IDLE, now)
            self.status = f"Stopped {abs(along):.0f}{self.config.unit} short - re-approaching"
        return self.state

    # ------------------------------------------------------------------ main step

    def update(self, car_pos: Optional[Tuple[float, float]], car_angle: Optional[float],
               target: Optional[Tuple[float, float]], client, now: Optional[float] = None,
               cruise_pwm: Optional[int] = None) -> NavState:
        """
        One control step. Returns the current NavState; NavState.ARRIVED means the target
        was reached and the car is stopped. Changing the target restarts the approach.
        """
        now = time.time() if now is None else now
        c = self.config
        cruise = int(cruise_pwm if cruise_pwm is not None else c.cruise_pwm)
        self._learn_from_accel(client)

        # Target marker vanished mid-approach (car driving over it) -> keep the last one
        self.target_held = False
        if target is None:
            if self.target is not None and self.state in _ACTIVE_STATES:
                target = self.target
                self.target_held = True
            else:
                self.brake(client)
                self.target = None
                self._set_state(NavState.IDLE, now)
                self.status = "No target"
                return self.state

        # New target (moved > arrive radius) -> restart approach
        if self.target is None or math.hypot(target[0] - self.target[0], target[1] - self.target[1]) > c.arrive_radius_px:
            if self.state == NavState.DRIVING:
                self.brake(client)
            self.turn_retries = 0
            self.min_dist_seen = float("inf")
            self._set_state(NavState.IDLE, now)
        self.target = target

        # ---- Car pose: camera, or dead-reckoned while briefly lost during a drive
        estimated = False
        if car_pos is not None and car_angle is not None:
            if self.state == NavState.DRIVING and self.last_pos is not None:
                dt = now - self.last_seen_time
                if 0.02 <= dt <= 0.5:
                    inst = math.hypot(car_pos[0] - self.last_pos[0], car_pos[1] - self.last_pos[1]) / dt
                    self.speed_px_s += 0.5 * (min(inst, c.max_speed_px_s) - self.speed_px_s)
            self.last_pos, self.last_angle, self.last_seen_time = car_pos, car_angle, now
        elif self.state == NavState.DRIVING and self.last_pos is not None and self.last_angle is not None:
            lost = now - self.last_seen_time
            if lost > max(c.dead_reckon_sec, c.lost_vision_sec):
                self.brake(client)
                self._set_state(NavState.WAITING_VISION, now)
                self.status = "Car marker lost - stopped"
                return self.state
            a = math.radians(self.last_angle)
            car_pos = (self.last_pos[0] + self.speed_px_s * lost * math.cos(a),
                       self.last_pos[1] + self.speed_px_s * lost * math.sin(a))
            car_angle = self.last_angle
            estimated = True
        elif self.state == NavState.BRAKING and now - self.state_since < 2.0:
            return self.state  # Wait for the car to reappear after the brake
        else:
            if self.state not in (NavState.TURNING, NavState.DRIVING):
                self.status = "Waiting for car marker..."
            return self.state

        stop_pt = self._stop_point(car_pos, car_angle)
        dist, err = self.geometry(stop_pt, car_angle, target)
        self.last_dist, self.last_err = dist, err

        # ---- Braking: wait for the brake + settle, then measure where it actually stopped
        if self.state == NavState.BRAKING:
            if now - self.state_since < self.brake_ms / 1000.0 + c.brake_settle_sec:
                return self.state
            return self._finish_brake(stop_pt, dist, now)

        # ---- Arrived (stay arrived until the target moves or the car is pushed well away)
        if self.state == NavState.ARRIVED:
            if dist <= c.arrive_radius_px * 2.0 or self.target_held:
                return self.state
            self._set_state(NavState.IDLE, now)
        if self.state != NavState.DRIVING and dist <= c.arrive_radius_px:
            self.brake(client)
            self._set_state(NavState.ARRIVED, now)
            self.status = f"Arrived ({dist:.0f}{self.config.unit})"
            return self.state

        if self.state == NavState.STALLED:
            if now - self.state_since < c.stall_retry_sec:
                return self.state
            self.turn_retries = 0
            self._set_state(NavState.IDLE, now)

        if self.state in (NavState.IDLE, NavState.WAITING_VISION):
            if abs(err) > c.align_tol_deg:
                self._start_turn(err, client, now)
            else:
                self._start_drive(dist, err, client, now, cruise)
            return self.state

        if self.state == NavState.TURNING:
            elapsed = now - self.state_since
            if self._gyro_ready(client):
                turning = self._gyro(client).get("movementState") == "TURNING_AUTO"
                done = (elapsed >= c.turn_min_sec and not turning) or elapsed >= c.turn_timeout_sec
            else:
                done = elapsed >= c.pulse_step_sec
            if done:
                self.moving = False
                self._set_state(NavState.SETTLING, now)
                self.status = "Settling after turn"
            return self.state

        if self.state == NavState.SETTLING:
            if now - self.state_since < c.settle_sec:
                return self.state
            gyro_ok = self._gyro_ready(client)
            if abs(err) > c.align_tol_deg and (self.turn_retries < c.max_turn_retries or not gyro_ok):
                self.turn_retries += 1
                self._start_turn(err, client, now)
            else:
                self.turn_retries = 0
                self._start_drive(dist, err, client, now, cruise)
            return self.state

        if self.state == NavState.DRIVING:
            # Predictive brake: remaining distance has shrunk to the stopping distance
            stop_dist = self.stopping_distance()
            if dist <= max(stop_dist, c.arrive_radius_px * 0.5):
                self._start_brake(dist, client, now, "estimated" if estimated else "on target")
                return self.state

            # Passed the target: we were close and are now moving away -> brake now
            self.min_dist_seen = min(self.min_dist_seen, dist)
            if self.min_dist_seen < c.near_radius_px and dist > self.min_dist_seen + c.pass_margin_px:
                self._start_brake(dist, client, now, "passed closest point")
                return self.state

            if estimated:
                return self.state  # No steering or stall checks on a predicted position

            # Not getting closer (blocked / slipping) -> brake and retry later
            if dist < self.progress_dist - c.stall_progress_px:
                self.progress_dist, self.progress_time = dist, now
            elif now - self.progress_time > c.stall_sec:
                self.brake(client)
                self._set_state(NavState.STALLED, now)
                self.status = f"STALLED: no progress for {c.stall_sec:.0f}s ({dist:.0f}{self.config.unit} left) - retrying"
                logger.warning(f"[NAV] {self.status}")
                return self.state

            # Badly off course -> stop and turn again
            if abs(err) > c.realign_deg and dist > c.near_radius_px:
                self.brake(client)
                self.turn_retries = 0
                self._start_turn(err, client, now)
                return self.state

            self._set_speed(client, self._speed_for(dist, cruise))

            # Small drift -> nudge the gyro-locked heading (car keeps moving)
            if (self.locked_heading is not None and dist > c.near_radius_px
                    and abs(err) > c.steer_deg and now - self.last_steer_time >= c.steer_interval_sec):
                yaw = self._gyro(client).get("yaw")
                if yaw is not None:
                    self.locked_heading = self._wrap(float(yaw) + err)
                    client.send_line(f"H{self.locked_heading:.1f}")
                    self.last_steer_time = now

            self.status = (f"Driving straight ({dist:.0f}{self.config.unit}, {self.speed_px_s:.0f}{self.config.unit}/s, "
                           f"brake at {stop_dist:.0f}{self.config.unit}, err {err:+.1f}°)")
            return self.state

        return self.state

    def status_dict(self) -> Dict[str, Any]:
        return {
            "nav_state": self.state.value,
            "nav_status": self.status,
            "distance_px": round(self.last_dist, 1),
            "heading_error_deg": round(self.last_err, 1),
            "locked_heading": round(self.locked_heading, 1) if self.locked_heading is not None else None,
            "speed_pwm": self.sent_speed or 0,
            "speed_px_s": round(self.speed_px_s),
            "brake_extra_ms": round(self.brake_extra_sec * 1000),
            "last_stop_error_px": round(self.last_stop_error_px, 1) if self.last_stop_error_px is not None else None,
            "target_held": self.target_held,
            "accel_scale_mm_per_px": round(self.mps_per_px * 1000, 3) if self.mps_per_px else None,
        }
