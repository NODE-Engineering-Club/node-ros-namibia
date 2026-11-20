"""Open-sea vessel dynamics for the Gazebo sim: waves, wind, current, real thrust limits.

Gazebo's own sim (basicWorld) applies /cmd_vel to the hull as a velocity, so
the boat always does exactly what it is told: it can never be stopped by a
head sea or pushed off its line by a current. This node replaces that with a
force model so a sea state can say NO:

  /control/effort (the same throttle/yaw the Pico gets on the real boat)
     -> skid-steer mix like pico_bridge, assuming yaw_invert was commissioned
        so that +z turns the boat left (bench test 2.2); yaw_sign flips it
     -> 2x Blue Robotics T200 thrust curve (forward/reverse limits, deadband)
     -> 3-DOF manoeuvring model (surge, sway, yaw; Fossen-style, relative to
        the water) with added mass and linear + quadratic drag
     -> + wind drag on the topsides
     -> + water velocity: Benguela current + Stokes drift + wave orbital
        motion, from a JONSWAP spectrum with directional spread, at the
        boat's position (finite depth: the mission area is 3-9 m deep)
     -> heave / roll / pitch: the hull (1.5 m) is far shorter than the
        waves (~100 m), so it rides the surface and its slope.
  The resulting 6-DOF motion is sent to Gazebo's VelocityControl
  (/sim/hull_twist, bridged to /model/asket/cmd_vel), closed on the pose
  Gazebo reports, so GPS, IMU and /odom all come from Gazebo as before.

It also publishes what MAVROS would on the real boat for the speed PID
(/mavros/local_position/velocity_body), and /sim/sea/metrics for the trial
report (thrust saturation, speed through the water, roll/pitch, wave height).

Presets (sea_state param), for the Luderitz coast: a long SW swell plus a
short, steep wind sea from the wind direction (that is what rolls a small boat):
             swell          wind   current   wind sea
  calm      Hs 0.3 m  6 s   3 m/s  0.10 m/s  0.1 m 2.5 s
  moderate  Hs 1.0 m  9 s   8 m/s  0.25 m/s  0.4 m 3.5 s
  rough     Hs 2.0 m 11 s  12 m/s  0.40 m/s  0.8 m 4.5 s   (default)
  extreme   Hs 3.0 m 13 s  16 m/s  0.60 m/s  1.3 m 5.5 s
Directions are "coming from" for waves and wind (meteorological), "going
to" for the current (oceanographic). Defaults: SW swell, S wind, current
to the N. Any single value can be overridden with its own parameter.

Not modelled: breaking surf (H > 0.78 depth; a warning is logged), green
water on deck, propeller ventilation when the stern lifts, tide level
changes (a 1.5 m tide only changes the depth used for the waves).
"""
import math

import numpy as np
import rclpy
from geometry_msgs.msg import Twist, TwistStamped
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray
from tf2_msgs.msg import TFMessage

G = 9.81
RHO = 1025.0
RHO_AIR = 1.225

PRESETS = {
    #           swell hs  tp   wind  current  wind-sea hs  tp
    "calm":     (0.3,  6.0,  3.0, 0.10,        0.1, 2.5),
    "moderate": (1.0,  9.0,  8.0, 0.25,        0.4, 3.5),
    "rough":    (2.0, 11.0, 12.0, 0.40,        0.8, 4.5),
    "extreme":  (3.0, 13.0, 16.0, 0.60,        1.3, 5.5),
}

METRICS_FIELDS = (
    "t", "x", "y", "heading_deg", "sog", "stw", "eta", "roll_deg", "pitch_deg",
    "thrust_left_n", "thrust_right_n", "saturated", "effort_x", "effort_z",
    "water_vx", "water_vy",
)


def wavenumber(omega, depth):
    """Solve omega^2 = g k tanh(k h) (Newton, from the deep-water guess)."""
    k = omega * omega / G
    for _ in range(30):
        th = math.tanh(k * depth)
        f = G * k * th - omega * omega
        df = G * th + G * k * depth * (1.0 - th * th)
        k -= f / df
    return k


def jonswap(omega, hs, tp, gamma=3.3):
    wp = 2.0 * math.pi / tp
    sigma = np.where(omega <= wp, 0.07, 0.09)
    r = np.exp(-((omega - wp) ** 2) / (2.0 * sigma ** 2 * wp ** 2))
    s = (5.0 / 16.0) * hs ** 2 * wp ** 4 * omega ** -5 * np.exp(-1.25 * (wp / omega) ** 4)
    s *= gamma ** r
    s *= 1.0 - 0.287 * math.log(gamma)   # Goda's normalisation keeps Hs
    return s


def t200_thrust(n, t_fwd, t_rev):
    """Normalised command -1..1 -> newtons. Roughly the T200 curve at 14.8 V:
    a +-25 us deadband round 1500 us, then thrust ~ 0.35 n + 0.65 n^2."""
    a = abs(n)
    if a < 0.05:
        return 0.0
    a = (a - 0.05) / 0.95
    t = 0.35 * a + 0.65 * a * a
    return t * t_fwd if n > 0 else -t * t_rev


class SeaStateNode(Node):
    def __init__(self, **kwargs):
        super().__init__("sea_state", **kwargs)
        p = self.declare_parameter
        p("sea_state", "rough")
        p("hs", -1.0)                # m, significant wave height (-1 = preset)
        p("tp", -1.0)                # s, peak period
        p("wave_from_deg", 225.0)    # SW swell
        p("wave_spread_s", 6.0)      # cos^2s spreading; bigger = narrower
        p("wind_speed", -1.0)        # m/s
        p("wind_from_deg", 180.0)    # southerly
        p("current_speed", -1.0)     # m/s
        p("current_to_deg", 0.0)     # Benguela: northward
        p("depth", 6.0)              # m (sonar, 2026-11-17: 3.3-9 m)
        p("n_components", 80)
        p("seed", 7)
        # Hull. URDF says 14 kg (hull only); with packs, Jetson and sonar the
        # boat is heavier. Drag picked for ~1.9 m/s flat-out in calm water.
        p("mass", 25.0)
        p("iz", 3.4)
        p("length", 1.52)
        p("beam", 0.74)
        p("thruster_half_spacing", 0.30)
        p("t200_fwd_n", 44.0)        # ~4.5 kgf at 14.8 V
        p("t200_rev_n", 34.0)
        p("xu", 8.0)
        p("xuu", 22.0)
        p("yv", 40.0)
        p("yvv", 120.0)
        p("nr", 8.0)
        p("nrr", 10.0)
        p("wind_area_front", 0.20)   # m^2 above the waterline
        p("wind_area_side", 0.45)
        p("yaw_sign", 1)             # +effort z = turn left (CCW), as commissioned
        p("rate_hz", 50.0)
        p("track_gain", 2.0)         # pull Gazebo's pose onto the model state

        gv = lambda n: self.get_parameter(n).value
        preset = gv("sea_state")
        if preset not in PRESETS:
            raise ValueError(f"sea_state must be one of {sorted(PRESETS)}, not {preset!r}")
        hs, tp, wind, cur, hs_ws, tp_ws = PRESETS[preset]
        self.hs = gv("hs") if gv("hs") >= 0 else hs
        self.tp = gv("tp") if gv("tp") > 0 else tp
        self.wind = gv("wind_speed") if gv("wind_speed") >= 0 else wind
        cur = gv("current_speed") if gv("current_speed") >= 0 else cur
        self.depth = float(gv("depth"))
        for n in ("mass", "iz", "length", "beam", "thruster_half_spacing", "t200_fwd_n",
                  "t200_rev_n", "xu", "xuu", "yv", "yvv", "nr", "nrr",
                  "wind_area_front", "wind_area_side", "track_gain"):
            setattr(self, n, float(gv(n)))
        self.yaw_sign = int(gv("yaw_sign"))
        # Added mass: ~10 % in surge, ~80 % in sway, ~50 % of Iz in yaw.
        self.m_u = self.mass * 1.1
        self.m_v = self.mass * 1.8
        self.i_r = self.iz * 1.5

        # Wind blows FROM wind_from_deg (compass) -> velocity vector in ENU.
        wf = math.radians(gv("wind_from_deg"))
        self.wind_vel = np.array([-math.sin(wf), -math.cos(wf)]) * self.wind
        ct = math.radians(gv("current_to_deg"))
        self.current = np.array([math.sin(ct), math.cos(ct)]) * cur

        nc, seed = int(gv("n_components")), int(gv("seed"))
        swell = self._build_waves(self.hs, self.tp, gv("wave_from_deg"), gv("wave_spread_s"), nc, seed)
        wsea = self._build_waves(hs_ws, tp_ws, gv("wind_from_deg"), 2.0, nc // 2, seed + 1)
        self._set_waves([swell, wsea])
        self.hs_ws, self.tp_ws = hs_ws, tp_ws
        if self.hs > 0.78 * self.depth * 0.7:
            self.get_logger().warn(
                f"Hs {self.hs:.1f} m in {self.depth:.1f} m of water: the biggest waves would break "
                "(surf). Breaking is not modelled; real conditions would be worse.")

        # State: world x, y (ENU, m), yaw psi (rad, from +x), body u, v, r.
        self.x = self.y = self.psi = 0.0
        self.u = self.v = self.r = 0.0
        self.z = self.roll = self.pitch = 0.0
        self.effort = (0.0, 0.0)
        self.effort_t = None
        self.gz_pose = None   # (x, y, z, roll, pitch, yaw)
        self.t_prev = None
        self.prev_orb = None
        self.started = False

        self.create_subscription(Twist, "/control/effort", self._effort_cb, 10)
        self.create_subscription(TFMessage, "/sim/model_poses", self._pose_cb, 10)
        self.pub_twist = self.create_publisher(Twist, "/sim/hull_twist", 10)
        self.pub_vel = self.create_publisher(TwistStamped, "/mavros/local_position/velocity_body", 10)
        self.pub_metrics = self.create_publisher(Float64MultiArray, "/sim/sea/metrics", 10)
        self.create_timer(1.0 / float(gv("rate_hz")), self._step)
        self.get_logger().info(
            f"sea state '{preset}': Hs {self.hs:.2f} m, Tp {self.tp:.1f} s from {gv('wave_from_deg'):.0f} deg, "
            f"wind {self.wind:.1f} m/s from {gv('wind_from_deg'):.0f} deg, current {cur:.2f} m/s to "
            f"{gv('current_to_deg'):.0f} deg, wind sea Hs {self.hs_ws:.1f} m Tp {self.tp_ws:.1f} s, depth {self.depth:.1f} m, Stokes drift "
            f"{np.linalg.norm(self.stokes):.2f} m/s")

    # ------------------------------------------------------------------ waves
    def _build_waves(self, hs, tp, from_deg, spread_s, n, seed):
        """One JONSWAP sea (swell or wind sea) as n components."""
        rng = np.random.default_rng(seed)
        wp = 2.0 * math.pi / tp
        # Random frequencies (avoids the periodic repeat of a regular grid).
        w = np.sort(rng.uniform(0.6 * wp, 3.0 * wp, n))
        dw = np.gradient(w)
        amp = np.sqrt(2.0 * jonswap(w, max(hs, 1e-6), tp) * dw) if hs > 0 else np.zeros(n)
        # Direction: waves travel TOWARD from_deg + 180; sample cos^2s spread.
        mean_to = math.radians(from_deg + 180.0)
        cand = rng.uniform(-math.pi / 2, math.pi / 2, 20 * n)
        keep = rng.uniform(0, 1, cand.size) < np.cos(cand) ** (2 * spread_s)
        dth = cand[keep][:n]
        th = mean_to + dth                      # compass, travel direction
        k = np.array([wavenumber(wi, self.depth) for wi in w])
        return dict(w=w, a=amp, k=k, th=th, phase=rng.uniform(0, 2 * math.pi, n))

    def _set_waves(self, seas):
        cat = lambda key: np.concatenate([s[key] for s in seas])
        w, amp, k, th = cat("w"), cat("a"), cat("k"), cat("th")
        self.w, self.a, self.k, self.phase = w, amp, k, cat("phase")
        self.kx = k * np.sin(th)                # ENU: x east, y north
        self.ky = k * np.cos(th)
        kh = k * self.depth
        self.orb_gain = w / np.tanh(kh)         # horizontal orbital speed / amplitude at z=0
        us = w * k * amp ** 2 * np.cosh(2 * kh) / (2 * np.sinh(kh) ** 2)
        self.stokes = np.array([np.sum(us * np.sin(th)), np.sum(us * np.cos(th))])
        self.dir_x, self.dir_y = np.sin(th), np.cos(th)

    def _sea(self, x, y, t):
        """Surface elevation, slope (d/dx, d/dy) and orbital velocity at (x, y, t)."""
        ph = self.kx * x + self.ky * y - self.w * t + self.phase
        c, s = np.cos(ph), np.sin(ph)
        eta = np.sum(self.a * c)
        sx = -np.sum(self.a * self.kx * s)
        sy = -np.sum(self.a * self.ky * s)
        uo = self.a * self.orb_gain * c
        orb = np.array([np.sum(uo * self.dir_x), np.sum(uo * self.dir_y)])
        return eta, sx, sy, orb

    # -------------------------------------------------------------- callbacks
    def _effort_cb(self, msg):
        self.effort = (msg.linear.x, msg.angular.z)
        self.effort_t = self.get_clock().now()

    def _pose_cb(self, msg):
        # ros_gz_bridge's Pose_V -> TFMessage leaves child_frame_id empty
        # (Jazzy/Harmonic); the model comes first, before its links.
        named = [tf for tf in msg.transforms if tf.child_frame_id == "asket"]
        if not (named or msg.transforms):
            return
        tf = (named or msg.transforms)[0]
        q = tf.transform.rotation
        tr = tf.transform.translation
        roll = math.atan2(2 * (q.w * q.x + q.y * q.z), 1 - 2 * (q.x * q.x + q.y * q.y))
        pitch = math.asin(max(-1.0, min(1.0, 2 * (q.w * q.y - q.z * q.x))))
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        self.gz_pose = (tr.x, tr.y, tr.z, roll, pitch, yaw)
        if not self.started:
            # Start from wherever Gazebo spawned the hull.
            self.x, self.y, self.psi = tr.x, tr.y, yaw
            self.started = True

    # ---------------------------------------------------------------- physics
    def _thrust(self):
        x, z = self.effort
        if self.effort_t is None or (self.get_clock().now() - self.effort_t).nanoseconds > 0.6e9:
            x = z = 0.0   # the Pico goes neutral without a fresh command
        z *= self.yaw_sign
        # pico_bridge._mix (same clamp/normalise), +z = CCW.
        left, right = x - z, x + z   # ROS +z = CCW: starboard pushes more
        m = max(abs(left), abs(right), 1.0)
        left, right = max(-1.0, min(1.0, left / m)), max(-1.0, min(1.0, right / m))
        tl = t200_thrust(left, self.t200_fwd_n, self.t200_rev_n)
        tr = t200_thrust(right, self.t200_fwd_n, self.t200_rev_n)
        sat = 1.0 if max(abs(left), abs(right)) >= 0.999 else 0.0
        return tl, tr, sat

    def _step(self):
        now = self.get_clock().now().nanoseconds * 1e-9
        if now <= 0.0 or not self.started:
            return   # no /clock yet, or Gazebo has not reported the hull
        if self.t_prev is None:
            self.t_prev = now
            return
        dt = now - self.t_prev
        if dt <= 0.0:
            return
        self.t_prev = now
        sub = max(1, int(math.ceil(dt / 0.01)))
        h = dt / sub
        for i in range(sub):
            self._integrate(now - dt + (i + 1) * h, h)
        self._command(now, dt)

    def _integrate(self, t, h):
        cps, sps = math.cos(self.psi), math.sin(self.psi)
        eta, sx, sy, orb = self._sea(self.x, self.y, t)
        water = self.current + self.stokes + orb
        orb_acc = np.zeros(2) if self.prev_orb is None else (orb - self.prev_orb) / h
        self.prev_orb = orb
        # World -> body.
        wb = np.array([cps * water[0] + sps * water[1], -sps * water[0] + cps * water[1]])
        ab = np.array([cps * orb_acc[0] + sps * orb_acc[1], -sps * orb_acc[0] + cps * orb_acc[1]])
        ur, vr = self.u - wb[0], self.v - wb[1]

        tl, tr, _ = self._thrust()
        fx_thr = tl + tr
        n_thr = self.thruster_half_spacing * (tr - tl)

        # Wind on the topsides, relative to the boat's ground velocity.
        vg = np.array([cps * self.u - sps * self.v, sps * self.u + cps * self.v])
        rel = self.wind_vel - vg
        relb = np.array([cps * rel[0] + sps * rel[1], -sps * rel[0] + cps * rel[1]])
        fx_w = 0.5 * RHO_AIR * 0.9 * self.wind_area_front * relb[0] * abs(relb[0])
        fy_w = 0.5 * RHO_AIR * 1.1 * self.wind_area_side * relb[1] * abs(relb[1])
        n_w = 0.08 * self.length * fy_w            # centre of effort a little aft: weathervanes

        # Waves on a short hull: Froude-Krylov follows the orbital acceleration;
        # the cross-slope gives a small broaching moment in quartering seas.
        fx_fk, fy_fk = self.m_u * ab[0], self.m_v * ab[1]
        slope_long = cps * sx + sps * sy
        slope_lat = -sps * sx + cps * sy
        n_wave = -0.15 * self.mass * G * self.length * slope_long * slope_lat

        du = (fx_thr + fx_w + fx_fk - (self.xu + self.xuu * abs(ur)) * ur + self.m_v * self.v * self.r) / self.m_u
        dv = (fy_w + fy_fk - (self.yv + self.yvv * abs(vr)) * vr - self.m_u * self.u * self.r) / self.m_v
        dr = (n_thr + n_w + n_wave - (self.nr + self.nrr * abs(self.r)) * self.r) / self.i_r
        self.u += du * h
        self.v += dv * h
        self.r += dr * h
        self.x += (cps * self.u - sps * self.v) * h
        self.y += (sps * self.u + cps * self.v) * h
        self.psi = math.atan2(math.sin(self.psi + self.r * h), math.cos(self.psi + self.r * h))
        # Ride the surface (first-order lag ~0.25 s for heave, 0.35 s for angles).
        self.z += (eta - self.z) * min(1.0, h / 0.25)
        self.roll += (math.atan(slope_lat) - self.roll) * min(1.0, h / 0.35)
        self.pitch += (-math.atan(slope_long) - self.pitch) * min(1.0, h / 0.35)
        self._last = (eta, water, tl, tr)

    def _command(self, t, dt):
        """Send Gazebo the body twist that moves its hull onto the model state."""
        k = self.track_gain
        cps, sps = math.cos(self.psi), math.sin(self.psi)
        vx = cps * self.u - sps * self.v
        vy = sps * self.u + cps * self.v
        vz = 0.0
        dyaw = droll = dpitch = 0.0
        if self.gz_pose is not None:
            gx, gy, gz, groll, gpitch, gyaw = self.gz_pose
            vx += k * (self.x - gx)
            vy += k * (self.y - gy)
            vz = k * 2 * (self.z - gz)
            dyaw = k * math.atan2(math.sin(self.psi - gyaw), math.cos(self.psi - gyaw))
            droll = k * 2 * (self.roll - groll)
            dpitch = k * 2 * (self.pitch - gpitch)
            rot = (groll, gpitch, gyaw)
        else:
            rot = (self.roll, self.pitch, self.psi)
        # VelocityControl takes the twist in the hull's own frame.
        cr, sr = math.cos(rot[0]), math.sin(rot[0])
        cp, sp = math.cos(rot[1]), math.sin(rot[1])
        cy, sy = math.cos(rot[2]), math.sin(rot[2])
        R = np.array([
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ])
        vb = R.T @ np.array([vx, vy, vz])
        # Euler rates -> body rates (ZYX).
        yaw_rate = self.r + dyaw
        p_ = droll - sp * yaw_rate
        q_ = cr * dpitch + sr * cp * yaw_rate
        r_ = -sr * dpitch + cr * cp * yaw_rate
        tw = Twist()
        tw.linear.x, tw.linear.y, tw.linear.z = (float(c) for c in vb)
        tw.angular.x, tw.angular.y, tw.angular.z = float(p_), float(q_), float(r_)
        self.pub_twist.publish(tw)

        # What MAVROS gives the speed PID on the real boat: ground velocity, body frame.
        vel = TwistStamped()
        vel.header.stamp = self.get_clock().now().to_msg()
        vel.header.frame_id = "base_link"
        vel.twist.linear.x, vel.twist.linear.y = self.u, self.v
        vel.twist.angular.z = self.r
        self.pub_vel.publish(vel)

        eta, water, tl, tr = self._last
        _, _, sat = self._thrust()
        wb_u = cps * water[0] + sps * water[1]
        m = Float64MultiArray()
        m.data = [float(v) for v in (
            t, self.x, self.y, (90.0 - math.degrees(self.psi)) % 360.0,
            math.hypot(self.u, self.v), self.u - wb_u, eta,
            math.degrees(self.roll), math.degrees(self.pitch), tl, tr, sat,
            self.effort[0], self.effort[1], water[0], water[1])]
        self.pub_metrics.publish(m)


def main(args=None):
    rclpy.init(args=args)
    node = SeaStateNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
