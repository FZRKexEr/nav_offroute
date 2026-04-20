#!/usr/bin/env python3
"""
Streaming route projection + multi-hypothesis conservative off-route detector prototype.

Input assumptions:
- route GeoJSON: FeatureCollection of LineString features in route order, feature.properties.id optional.
- gps GeoJSON: FeatureCollection of Point features, each with timestamp(ms), speed(m/s), heading(deg), usable, trustedLevel.

The detector is online: update(point) uses only current and previous states.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

EARTH_R = 6371000.0


def angle_diff_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def heading_from_dxdy(dx: float, dy: float) -> float:
    # navigation convention: north=0, east=90
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def quantile(xs: List[float], q: float) -> float:
    if not xs:
        return float('nan')
    ys = sorted(xs)
    k = int(round((len(ys) - 1) * q))
    return ys[k]


@dataclass
class GpsPoint:
    lon: float
    lat: float
    timestamp_ms: int = 0
    speed: float = 0.0
    heading: float = 0.0
    usable: bool = True
    trusted_level: int = 1
    raw: Optional[Dict[str, Any]] = None


@dataclass
class Segment:
    idx: int
    x1: float
    y1: float
    x2: float
    y2: float
    dx: float
    dy: float
    length: float
    heading: float
    s0: float
    s1: float


@dataclass
class Projection:
    segment_idx: int
    s: float
    dist: float
    signed_dist: float
    heading: float
    t: float
    t_raw: float
    endpoint_gap: float
    x: float
    y: float
    px: float
    py: float
    score: float = 0.0
    global_nearest: bool = False


@dataclass
class DetectorOutput:
    off_route: bool
    state: str
    reason: str
    projection: Optional[Projection]
    metrics: Dict[str, Any]


class RouteIndex:
    def __init__(self, coords_lonlat: List[Tuple[float, float]]):
        if len(coords_lonlat) < 2:
            raise ValueError('route must contain at least two coordinates')
        self.coords_lonlat = coords_lonlat
        self.lon0 = sum(p[0] for p in coords_lonlat) / len(coords_lonlat)
        self.lat0 = sum(p[1] for p in coords_lonlat) / len(coords_lonlat)
        self.xy = [self.ll_to_xy(lon, lat) for lon, lat in coords_lonlat]
        self.segments: List[Segment] = []
        s = 0.0
        for i in range(len(self.xy) - 1):
            x1, y1 = self.xy[i]
            x2, y2 = self.xy[i + 1]
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy)
            if L < 0.05:
                continue
            h = heading_from_dxdy(dx, dy)
            self.segments.append(Segment(len(self.segments), x1, y1, x2, y2, dx, dy, L, h, s, s + L))
            s += L
        self.length = s
        self.sharp_turn_s: List[float] = []
        self._compute_sharp_turns()

    def ll_to_xy(self, lon: float, lat: float) -> Tuple[float, float]:
        x = math.radians(lon - self.lon0) * EARTH_R * math.cos(math.radians(self.lat0))
        y = math.radians(lat - self.lat0) * EARTH_R
        return x, y

    def _compute_sharp_turns(self) -> None:
        for a, b in zip(self.segments, self.segments[1:]):
            # Ignore tiny heading changes. If the route has a required maneuver, wrong heading matters more.
            turn = angle_diff_deg(a.heading, b.heading)
            if turn >= 35.0:
                self.sharp_turn_s.append(a.s1)

    def distance_to_nearest_turn(self, s: float) -> float:
        if not self.sharp_turn_s:
            return float('inf')
        return min(abs(s - ts) for ts in self.sharp_turn_s)


    def has_turn_between(self, s0: float, s1: float) -> bool:
        if s1 < s0:
            s0, s1 = s1, s0
        return any((s0 - 3.0) <= ts <= (s1 + 3.0) for ts in self.sharp_turn_s)

    def point_at_s(self, s: float) -> Tuple[float, float, float]:
        s = clamp(s, 0.0, self.length)
        if not self.segments:
            return 0.0, 0.0, 0.0
        lo, hi = 0, len(self.segments) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.segments[mid].s1 < s:
                lo = mid + 1
            else:
                hi = mid
        seg = self.segments[lo]
        t = 0.0 if seg.length <= 1e-9 else clamp((s - seg.s0) / seg.length, 0.0, 1.0)
        return seg.x1 + t * seg.dx, seg.y1 + t * seg.dy, seg.heading

    def project_xy_window_only(self, x: float, y: float, min_s: float, max_s: float,
                               heading: Optional[float] = None, heading_weight: float = 0.0) -> Optional[Projection]:
        candidates: List[Projection] = []
        for seg in self.segments:
            if seg.s1 < min_s or seg.s0 > max_s:
                continue
            c = self.project_on_segment(seg, x, y)
            cost = (min(c.dist, 160.0) / 18.0) ** 2
            if heading is not None and heading_weight > 0.0:
                cost += heading_weight * (angle_diff_deg(heading, c.heading) / 65.0) ** 2
            c.score = cost
            candidates.append(c)
        if not candidates:
            return None
        return min(candidates, key=lambda c: c.score)

    def project_window_only(self, lon: float, lat: float, min_s: float, max_s: float,
                            heading: Optional[float] = None, heading_weight: float = 0.0) -> Optional[Projection]:
        x, y = self.ll_to_xy(lon, lat)
        candidates: List[Projection] = []
        for seg in self.segments:
            if seg.s1 < min_s or seg.s0 > max_s:
                continue
            c = self.project_on_segment(seg, x, y)
            cost = (min(c.dist, 160.0) / 18.0) ** 2
            if heading is not None and heading_weight > 0.0:
                cost += heading_weight * (angle_diff_deg(heading, c.heading) / 65.0) ** 2
            c.score = cost
            candidates.append(c)
        if not candidates:
            return None
        return min(candidates, key=lambda c: c.score)

    def project_on_segment(self, seg: Segment, x: float, y: float) -> Projection:
        t_raw = ((x - seg.x1) * seg.dx + (y - seg.y1) * seg.dy) / (seg.length * seg.length)
        t = clamp(t_raw, 0.0, 1.0)
        px = seg.x1 + t * seg.dx
        py = seg.y1 + t * seg.dy
        dist = math.hypot(x - px, y - py)
        signed = (seg.dx * (y - seg.y1) - seg.dy * (x - seg.x1)) / seg.length
        # Longitudinal distance beyond a segment endpoint. This catches missed turns where
        # the point stays on the old road extension: signed lateral distance can be tiny,
        # but the snapped endpoint is getting farther away.
        endpoint_gap = 0.0
        if t_raw < 0.0:
            endpoint_gap = -t_raw * seg.length
        elif t_raw > 1.0:
            endpoint_gap = (t_raw - 1.0) * seg.length
        s = seg.s0 + t * seg.length
        return Projection(seg.idx, s, dist, signed, seg.heading, t, t_raw, endpoint_gap, x, y, px, py)

    def project(self, lon: float, lat: float, prev_s: Optional[float] = None,
                min_s: Optional[float] = None, max_s: Optional[float] = None,
                heading: Optional[float] = None, heading_weight: float = 0.0,
                speed: float = 0.0, dt: float = 1.0) -> Projection:
        x, y = self.ll_to_xy(lon, lat)
        candidates: List[Projection] = []
        # Include all segments whose s range intersects the allowed window. For small routes brute force is fine.
        for seg in self.segments:
            if min_s is not None and seg.s1 < min_s:
                continue
            if max_s is not None and seg.s0 > max_s:
                continue
            candidates.append(self.project_on_segment(seg, x, y))
        if not candidates:
            candidates = [self.project_on_segment(seg, x, y) for seg in self.segments]
        # A global candidate is added with a heavy progress-jump penalty; this prevents matching to a later crossing.
        global_best = min((self.project_on_segment(seg, x, y) for seg in self.segments), key=lambda p: p.dist)
        global_best.global_nearest = True
        candidates.append(global_best)

        # Keep projection progress physically plausible. A very loose forward window
        # can snap an early U-turn/shortcut onto a future route leg and hide the deviation.
        max_forward = 22.0 + 2.2 * max(speed, 0.0) * max(dt, 0.5)
        back_allow = 16.0 + 1.2 * max(speed, 0.0)
        for c in candidates:
            cost = (min(c.dist, 120.0) / 15.0) ** 2
            if heading is not None and speed >= 2.0 and heading_weight > 0.0:
                # Near a sharp turn the route tangent can change abruptly; reduce heading influence for matching.
                turn_dist = self.distance_to_nearest_turn(c.s)
                wh = heading_weight * (0.35 if turn_dist < 20.0 else 1.0)
                hd = angle_diff_deg(heading, c.heading)
                cost += wh * (hd / 55.0) ** 2
            if prev_s is not None:
                ds = c.s - prev_s
                if ds < -back_allow:
                    cost += ((-ds - back_allow) / 20.0) ** 2 * 8.0
                if ds > max_forward:
                    cost += ((ds - max_forward) / 20.0) ** 2 * 16.0
                # Small preference for smooth forward progress.
                if c.global_nearest:
                    cost += 1.2
            c.score = cost
        return min(candidates, key=lambda p: p.score)


class OffRouteDetector:
    def __init__(self, route: RouteIndex, config: Optional[Dict[str, float]] = None):
        self.route = route
        cfg = dict(
            start_grace_time_s=12.0,
            start_grace_dist_m=38.0,
            end_grace_dist_m=35.0,
            soft_residual_m=35.0,
            hard_residual_m=88.0,
            hard_raw_m=165.0,
            bias_cap_m=125.0,
            heading_bad_deg=65.0,
            heading_very_bad_deg=115.0,
            suspect_score_threshold=9.8,
            immediate_score_threshold=11.8,
            spoken_score_threshold=12.2,
            silent_suspect_threshold=4.8,
            max_window_back_m=120.0,
            max_window_ahead_m=260.0,
            vector_bias_cap_m=180.0,
            vector_bias_plausible_residual_m=42.0,
            chord_shortcut_max_lookahead_m=760.0,
            chord_shortcut_max_dist_m=62.0,
        )
        if config:
            cfg.update(config)
        self.cfg = cfg
        self.state = 'INIT'
        self.off_route = False
        self.score = 0.0
        self.prev_point: Optional[GpsPoint] = None
        self.prev_proj: Optional[Projection] = None
        self.start_ts_ms: Optional[int] = None
        self.last_good_s: Optional[float] = None
        self.last_good_ts_ms: Optional[int] = None
        self.bias = 0.0
        self.bias_conf = 0.0
        self.bias_x = 0.0
        self.bias_y = 0.0
        self.bias_vec_conf = 0.0
        self.last_obs_bias_vec: Optional[Tuple[float, float]] = None
        self.prev_raw_dist: Optional[float] = None
        self.last_timestamp_seen: Optional[int] = None

    @staticmethod
    def gps_sigma(p: GpsPoint) -> float:
        # trustedLevel semantics are not documented; the data suggests 1 is good, 4 is weak.
        return {1: 10.0, 2: 22.0, 3: 40.0, 4: 65.0}.get(int(p.trusted_level or 1), 32.0)

    def _heading_valid(self, p: GpsPoint) -> bool:
        return p.usable and p.speed >= 1.8 and abs((p.heading or 0.0)) > 1e-6

    def _dt(self, p: GpsPoint) -> float:
        if self.prev_point is None or not p.timestamp_ms or not self.prev_point.timestamp_ms:
            return 1.0
        return clamp((p.timestamp_ms - self.prev_point.timestamp_ms) / 1000.0, 0.0, 5.0)

    def update(self, p: GpsPoint) -> DetectorOutput:
        if self.start_ts_ms is None:
            self.start_ts_ms = p.timestamp_ms
        dt = self._dt(p)
        duplicate_time = self.last_timestamp_seen is not None and p.timestamp_ms == self.last_timestamp_seen
        self.last_timestamp_seen = p.timestamp_ms

        if not p.usable:
            # Do not let unusable points by themselves trigger off-route; keep state but decay slowly.
            self.score = max(0.0, self.score - 0.25 * max(dt, 0.5))

        # Search window around previous route progress. Large enough for normal movement, small enough to avoid jump to route crossings.
        prev_s = self.prev_proj.s if self.prev_proj else None
        if prev_s is None:
            # At navigation start prefer the beginning of the planned route. This avoids
            # snapping the first few points onto a close future/return leg in U-shaped routes.
            min_s = 0.0
            max_s = min(self.route.length, 240.0)
        else:
            min_s = max(0.0, prev_s - self.cfg['max_window_back_m'])
            max_s = min(self.route.length, prev_s + self.cfg['max_window_ahead_m'])
        heading_arg = p.heading if self._heading_valid(p) else None
        proj = self.route.project(p.lon, p.lat, prev_s=prev_s, min_s=min_s, max_s=max_s,
                                  heading=heading_arg, heading_weight=0.55,
                                  speed=p.speed, dt=max(dt, 1.0))

        route_heading = proj.heading
        hdiff = angle_diff_deg(p.heading, route_heading) if self._heading_valid(p) else None
        sigma = self.gps_sigma(p)
        soft = self.cfg['soft_residual_m'] + 0.45 * sigma + 0.45 * max(p.speed, 0.0)
        hard = self.cfg['hard_residual_m'] + 0.70 * sigma + 0.35 * max(p.speed, 0.0)
        raw_hard = self.cfg['hard_raw_m'] + 0.8 * sigma

        # H0b: correlated GPS vector bias. In urban canyons the error often behaves like
        # a slowly changing translation vector, not iid Gaussian noise. If subtracting the
        # learned vector makes the point fit route geometry/progress/heading, do not escalate.
        corrected_proj: Optional[Projection] = None
        corrected_hdiff = None
        vector_bias_plausible = False
        vector_bias_residual = None
        if self.bias_vec_conf >= 0.25 and p.usable:
            cx, cy = proj.x - self.bias_x, proj.y - self.bias_y
            if prev_s is None:
                cmin, cmax = 0.0, min(self.route.length, 260.0)
            else:
                cmin = max(0.0, prev_s - self.cfg['max_window_back_m'])
                cmax = min(self.route.length, prev_s + self.cfg['max_window_ahead_m'])
            corrected_proj = self.route.project_xy_window_only(cx, cy, cmin, cmax, heading=heading_arg, heading_weight=0.45)
            if corrected_proj is not None:
                corrected_hdiff = angle_diff_deg(p.heading, corrected_proj.heading) if self._heading_valid(p) else None
                cds = 0.0 if self.prev_proj is None else corrected_proj.s - self.prev_proj.s
                cprogress_upper = 36.0 + 2.5 * max(p.speed, 0.0) * max(dt, 0.5)
                vector_bias_residual = corrected_proj.dist
                vector_bias_plausible = (
                    (corrected_hdiff is None or corrected_hdiff <= 40.0)
                    and (self.prev_proj is None or cds >= -14.0)
                    and (self.prev_proj is None or cds <= cprogress_upper)
                    and corrected_proj.endpoint_gap <= 24.0
                    and corrected_proj.dist <= max(self.cfg['vector_bias_plausible_residual_m'], 0.70 * soft)
                )

        # Conservative shortcut / early-U-turn hypothesis: if the point can be explained
        # by a nearby future leg after a maneuver, allow projection progress to jump there.
        # This does not use future GPS samples, only the planned route ahead and current fix.
        plausible_shortcut = False
        shortcut_projection_used = False
        future_hdiff = None
        future_proj = None
        if prev_s is not None and p.usable:
            fmin = min(self.route.length, prev_s + max(24.0, 0.6 * (22.0 + 2.2 * max(p.speed, 0.0) * max(dt, 0.5))))
            fmax = min(self.route.length, prev_s + 520.0)
            if fmin < fmax - 1.0:
                future_proj = self.route.project_window_only(p.lon, p.lat, fmin, fmax, heading=heading_arg, heading_weight=0.35)
                if future_proj is not None:
                    future_hdiff = angle_diff_deg(p.heading, future_proj.heading) if self._heading_valid(p) else None
                    jump = future_proj.s - prev_s
                    has_turn_ahead = self.route.has_turn_between(prev_s, future_proj.s)
                    future_close = future_proj.dist <= max(88.0, soft + 58.0)
                    future_heading_ok = future_hdiff is None or future_hdiff <= 70.0
                    current_bad_for_progress = (hdiff is not None and hdiff >= 105.0) or proj.endpoint_gap > 18.0 or proj.dist > soft
                    plausible_shortcut = (
                        has_turn_ahead and 24.0 <= jump <= 520.0 and future_close
                        and future_heading_ok
                    )
                    if plausible_shortcut and current_bad_for_progress and future_proj.dist <= proj.dist + 42.0:
                        proj = future_proj
                        route_heading = proj.heading
                        hdiff = future_hdiff
                        shortcut_projection_used = True

        # H2b: chord shortcut/rejoin. A point can be far from the route polyline yet close
        # to the straight line between the current route position and a future route point.
        # With no road graph, this is exactly the ambiguity of local shortcuts, early U-turns,
        # parking-lot cut-throughs, or crossing to the opposite carriageway.
        chord_shortcut_plausible = False
        chord_shortcut_target_s = None
        chord_shortcut_dist = None
        chord_shortcut_heading_diff = None
        if prev_s is not None and p.usable and not shortcut_projection_used:
            sx0, sy0 = (self.prev_proj.px, self.prev_proj.py) if self.prev_proj is not None else self.route.point_at_s(prev_s)[:2]
            max_la = min(self.cfg['chord_shortcut_max_lookahead_m'], self.route.length - prev_s)
            if max_la >= 60.0:
                step = 28.0
                best = None
                k = 2
                while k * step <= max_la + 1e-6:
                    ts = prev_s + k * step
                    if not self.route.has_turn_between(prev_s, ts):
                        k += 1
                        continue
                    tx, ty, th = self.route.point_at_s(ts)
                    ux, uy = tx - sx0, ty - sy0
                    clen2 = ux * ux + uy * uy
                    if clen2 < 900.0:
                        k += 1
                        continue
                    tline = ((proj.x - sx0) * ux + (proj.y - sy0) * uy) / clen2
                    if -0.08 <= tline <= 1.10:
                        qx, qy = sx0 + tline * ux, sy0 + tline * uy
                        dline = math.hypot(proj.x - qx, proj.y - qy)
                        ch = heading_from_dxdy(ux, uy)
                        chd = angle_diff_deg(p.heading, ch) if self._heading_valid(p) else None
                        route_arc = ts - prev_s
                        chord_len = math.sqrt(clen2)
                        saves = route_arc - chord_len
                        max_dline = max(self.cfg['chord_shortcut_max_dist_m'], 0.85 * soft)
                        if saves >= 18.0 and dline <= max_dline and (chd is None or chd <= 72.0):
                            score = dline + (chd or 20.0) * 0.28 - saves * 0.08
                            if best is None or score < best[0]:
                                best = (score, ts, dline, chd)
                    k += 1
                if best is not None:
                    chord_shortcut_plausible = True
                    chord_shortcut_target_s = best[1]
                    chord_shortcut_dist = best[2]
                    chord_shortcut_heading_diff = best[3]
                    plausible_shortcut = True

        elapsed = 0.0 if self.start_ts_ms is None else max(0.0, (p.timestamp_ms - self.start_ts_ms) / 1000.0)
        # Start grace is expanded for approaching the route start from the same road extension,
        # but disabled when the user is moving away from the route start in the opposite direction.
        approaching_start_extension = (
            elapsed <= 35.0 and proj.s <= 8.0 and 20.0 <= proj.endpoint_gap <= 125.0
            and hdiff is not None and hdiff <= 38.0 and p.speed >= 1.0
        )
        moving_away_from_start = (
            proj.s <= 8.0 and proj.endpoint_gap > 8.0 and hdiff is not None
            and hdiff >= 105.0 and p.speed >= 2.0
        )
        opposite_to_route_near_start = hdiff is not None and hdiff >= 105.0 and p.speed >= 2.0 and proj.s <= self.cfg['start_grace_dist_m']
        start_grace = (
            (elapsed <= self.cfg['start_grace_time_s'] and proj.s <= self.cfg['start_grace_dist_m'] and proj.dist <= 70.0 and not opposite_to_route_near_start)
            or approaching_start_extension
        ) and not moving_away_from_start
        near_end = self.route.length - proj.s <= 35.0 and proj.dist <= self.cfg['end_grace_dist_m']
        near_turn = self.route.distance_to_nearest_turn(proj.s) <= 28.0

        gps_step = None
        if self.prev_point is not None and dt > 0.05:
            x0, y0 = self.route.ll_to_xy(self.prev_point.lon, self.prev_point.lat)
            x1, y1 = self.route.ll_to_xy(p.lon, p.lat)
            gps_step = math.hypot(x1 - x0, y1 - y0)
        prev_was_good_match = self.prev_proj is not None and self.prev_raw_dist is not None and self.prev_raw_dist <= 45.0
        implausible_gps_jump = (
            gps_step is not None and gps_step > max(75.0, (max(p.speed, self.prev_point.speed if self.prev_point else 0.0) + 8.0) * max(dt, 0.5) * 2.8)
        )
        sensor_spike = (
            p.usable and prev_was_good_match and proj.dist > 85.0
            and (int(p.trusted_level or 1) >= 4 or implausible_gps_jump)
        )
        sensor_skip = (not p.usable) or sensor_spike

        # Bias compensation: stable same-side offset + aligned heading + forward progress means likely GPS/map/lane offset.
        # Endpoint overrun is deliberately excluded: after a missed turn the old segment extension can keep
        # signed lateral error small while true route progress is stuck.
        ds = 0.0 if self.prev_proj is None else proj.s - self.prev_proj.s
        lateral_change = 0.0 if self.prev_proj is None else abs(proj.signed_dist - self.prev_proj.signed_dist)
        growing_lateral_departure = (
            self.prev_proj is not None and not near_turn and p.speed >= 2.0 and self.bias_conf > 0.35
            and proj.dist > 70.0 and abs(proj.signed_dist) > 70.0
            and (
                lateral_change > 14.0
                or (proj.dist > 88.0 and lateral_change > 6.0 and self.bias_conf < 0.95)
            )
        )
        progress_forward = ds >= -8.0
        progress_upper = 30.0 + 2.3 * max(p.speed, 0.0) * max(dt, 0.5)
        progress_plausible = self.prev_proj is None or ds <= progress_upper or shortcut_projection_used
        heading_aligned = hdiff is None or hdiff <= 30.0
        endpoint_ok = proj.endpoint_gap <= 18.0 or start_grace or near_end
        vector_route_compatible = bool(vector_bias_plausible and p.usable and not sensor_skip and not duplicate_time)
        shortcut_bad_sustained = bool(
            chord_shortcut_plausible and p.usable and not sensor_skip
            and hdiff is not None and hdiff >= 65.0
            and proj.dist >= max(68.0, soft + 18.0)
            and self.prev_raw_dist is not None and proj.dist > self.prev_raw_dist + 1.8
        )
        shortcut_route_compatible = bool(
            chord_shortcut_plausible and p.usable and not sensor_skip and not duplicate_time
            and not shortcut_bad_sustained
        )
        normal_route_compatible = (
            p.usable and not sensor_skip and not growing_lateral_departure
            and progress_forward and progress_plausible and heading_aligned and endpoint_ok
            and proj.dist <= max(150.0, soft + 105.0)
        )
        route_compatible = normal_route_compatible or vector_route_compatible or shortcut_route_compatible
        if route_compatible and not duplicate_time:
            pure_shortcut_compatible = shortcut_route_compatible and not normal_route_compatible and not vector_route_compatible
            if not pure_shortcut_compatible:
                alpha = clamp(max(dt, 0.5) / 8.0, 0.06, 0.25)
                if self.bias_conf < 0.1:
                    self.bias = proj.signed_dist
                else:
                    self.bias = (1.0 - alpha) * self.bias + alpha * proj.signed_dist
                self.bias = clamp(self.bias, -self.cfg['bias_cap_m'], self.cfg['bias_cap_m'])
                self.bias_conf = min(1.0, self.bias_conf + 0.08 * max(dt, 0.5))

                bp = corrected_proj if vector_route_compatible and corrected_proj is not None else proj
                obs_bx, obs_by = proj.x - bp.px, proj.y - bp.py
                obs_mag = math.hypot(obs_bx, obs_by)
                prev_obs_change = 0.0 if self.last_obs_bias_vec is None else math.hypot(obs_bx - self.last_obs_bias_vec[0], obs_by - self.last_obs_bias_vec[1])
                self.last_obs_bias_vec = (obs_bx, obs_by)
                if obs_mag <= self.cfg['vector_bias_cap_m'] and (self.bias_vec_conf < 0.2 or prev_obs_change <= max(36.0, 0.55 * obs_mag + 15.0) or vector_route_compatible):
                    alpha_v = clamp(max(dt, 0.5) / 12.0, 0.04, 0.18)
                    if self.bias_vec_conf < 0.12:
                        self.bias_x, self.bias_y = obs_bx, obs_by
                    else:
                        self.bias_x = (1.0 - alpha_v) * self.bias_x + alpha_v * obs_bx
                        self.bias_y = (1.0 - alpha_v) * self.bias_y + alpha_v * obs_by
                    mag = math.hypot(self.bias_x, self.bias_y)
                    if mag > self.cfg['vector_bias_cap_m']:
                        scale = self.cfg['vector_bias_cap_m'] / max(1e-6, mag)
                        self.bias_x *= scale; self.bias_y *= scale
                    self.bias_vec_conf = min(1.0, self.bias_vec_conf + 0.055 * max(dt, 0.5))
            else:
                self.bias_conf = max(0.0, self.bias_conf - 0.01 * max(dt, 0.5))
        else:
            self.bias_conf = max(0.0, self.bias_conf - 0.04 * max(dt, 0.5))
            self.bias_vec_conf = max(0.0, self.bias_vec_conf - 0.025 * max(dt, 0.5))

        bias_residual = abs(proj.signed_dist - self.bias) if self.bias_conf >= 0.35 else proj.dist
        if endpoint_ok:
            residual = bias_residual
        else:
            residual = max(bias_residual, proj.endpoint_gap)
        residual = min(residual, proj.dist + 5.0)  # never hide gross nearest-point distance entirely
        if vector_bias_plausible and vector_bias_residual is not None and (endpoint_ok or proj.endpoint_gap <= 24.0):
            residual = min(residual, vector_bias_residual + 6.0)

        dist_growth = None
        if self.prev_raw_dist is not None and dt > 0.1:
            dist_growth = (proj.dist - self.prev_raw_dist) / dt
        if not sensor_skip:
            self.prev_raw_dist = proj.dist

        instant = 0.0
        reasons: List[str] = []

        if vector_bias_plausible:
            reasons.append('vector_bias_plausible')
        if chord_shortcut_plausible:
            reasons.append('chord_shortcut_plausible')
        if not p.usable:
            reasons.append('unusable_gps')
        if sensor_spike:
            reasons.append('sensor_spike_guard')
        if start_grace:
            reasons.append('start_grace')
        if near_end:
            reasons.append('end_grace')

        if not start_grace and not near_end and p.usable and not sensor_skip:
            if residual > hard:
                instant += 3.2 + min(2.0, (residual - hard) / 25.0)
                reasons.append(f'residual>{hard:.0f}m')
            elif residual > soft:
                instant += 0.8 + 2.0 * (residual - soft) / max(1.0, hard - soft)
                reasons.append(f'residual>{soft:.0f}m')

            if proj.dist > raw_hard:
                instant += 3.0 + min(2.0, (proj.dist - raw_hard) / 35.0)
                reasons.append(f'raw_dist>{raw_hard:.0f}m')

            if hdiff is not None:
                heading_has_context = residual > soft * 0.65 or proj.endpoint_gap > 15.0 or (near_turn and proj.dist > 15.0)
                if heading_has_context and hdiff > self.cfg['heading_very_bad_deg']:
                    instant += 2.2
                    reasons.append(f'heading>{self.cfg["heading_very_bad_deg"]:.0f}deg')
                elif heading_has_context and hdiff > self.cfg['heading_bad_deg']:
                    instant += 1.2
                    reasons.append(f'heading>{self.cfg["heading_bad_deg"]:.0f}deg')
                elif near_turn and proj.dist > 15.0 and hdiff > 42.0:
                    instant += 0.8
                    reasons.append('bad_heading_near_turn')

                # Sustained heading disagreement while measurably away from the route corridor
                # is a strong shortcut/wrong-fork signal. It deliberately requires distance
                # context, so noisy heading alone on-route does not trigger reroute.
                if proj.dist > max(18.0, soft * 0.55) and hdiff > 65.0:
                    instant += 1.35
                    reasons.append('heading_diverge_off_corridor')
                elif proj.dist > max(22.0, soft * 0.70) and hdiff > 48.0:
                    instant += 0.75
                    reasons.append('heading_mismatch_off_corridor')

            if growing_lateral_departure:
                instant += 1.35 + min(1.4, (abs(proj.signed_dist) - 36.0) / 28.0)
                reasons.append('growing_lateral_departure')

            if proj.dist > max(95.0, soft + 55.0) and abs(proj.signed_dist) > 85.0 and p.speed >= 2.0:
                instant += 1.05 + min(1.4, (proj.dist - max(56.0, soft + 20.0)) / 42.0)
                reasons.append('large_lateral_offset')

            if self.prev_proj is not None and ds > progress_upper and not plausible_shortcut and p.speed >= 1.5:
                instant += 1.2 + min(1.5, (ds - progress_upper) / 35.0)
                reasons.append('implausible_progress_jump')

            if self.prev_proj is not None:
                # Moving but route progress is stuck/backward is a strong missed-turn signal.
                backward_has_context = (
                    proj.dist > 18.0 or residual > soft * 0.55 or proj.endpoint_gap > 14.0
                    or (hdiff is not None and hdiff > 85.0)
                )
                if p.speed >= 2.5 and ds < -11.0 and backward_has_context:
                    instant += 2.1
                    reasons.append('backward_progress')
                if p.speed >= 2.5 and hdiff is not None and hdiff > 140.0 and ds < -1.0 and (proj.dist > 12.0 or proj.endpoint_gap > 10.0 or ds < -8.0):
                    instant += 1.8
                    reasons.append('wrong_way_on_route')
                if p.speed >= 2.5 and ds < 1.0 and (residual > soft * 0.95 or proj.endpoint_gap > 24.0 or (near_turn and hdiff is not None and hdiff > 90.0)):
                    instant += 1.3
                    reasons.append('stalled_projection_while_moving')

            if dist_growth is not None and dist_growth > 4.5 and (proj.endpoint_gap > 25.0 or residual > soft * 0.95 or (hdiff is not None and hdiff > 70.0)):
                instant += 1.0
                reasons.append('distance_increasing_away')

        # Decay/accumulate. Aligned progress is strong negative evidence, including parallel-offset cases.
        if duplicate_time:
            # Duplicated locations should not double-count evidence.
            instant *= 0.0
            reasons.append('duplicate_time')
        if start_grace or near_end or sensor_skip or not p.usable:
            decay = 1.2 * max(dt, 0.5) if sensor_skip else 0.7 * max(dt, 0.5)
        elif route_compatible and residual <= soft:
            decay = 1.4 * max(dt, 0.5)
            reasons.append('route_compatible')
        elif route_compatible:
            decay = 0.6 * max(dt, 0.5)
            reasons.append('parallel_or_bias_compatible')
        else:
            decay = 0.25 * max(dt, 0.5)

        if plausible_shortcut:
            if 'shortcut_bad_sustained' in locals() and shortcut_bad_sustained:
                instant *= 0.55
                reasons.append('shortcut_bad_sustained')
            else:
                instant *= 0.12 if chord_shortcut_plausible else 0.16
            decay += 1.15 * max(dt, 0.5)
            reasons.append('plausible_shortcut_or_early_uturn')
        if vector_bias_plausible:
            instant *= 0.18
            decay += 0.95 * max(dt, 0.5)
            reasons.append('gps_vector_bias_explains')

        # Conservative product policy: low-trust / poor-GNSS fixes should contribute much less
        # to a reroute decision unless several independent signals remain bad.
        if int(p.trusted_level or 1) >= 3:
            instant *= 0.32
            decay += 0.35 * max(dt, 0.5)
            reasons.append('low_trust_tempered')
        elif int(p.trusted_level or 1) == 2:
            instant *= 0.55
            decay += 0.15 * max(dt, 0.5)
            reasons.append('mid_trust_tempered')

        self.score = max(0.0, self.score + instant - decay)

        # Score capping for strong no-off hypotheses.  This is intentionally asymmetric:
        # obvious off-route evidence can still accumulate, but GPS-bias/shortcut explanations
        # prevent a few bad fixes from becoming a spoken reroute.
        if route_compatible and residual <= max(soft + 12.0, 62.0):
            self.score = min(self.score, self.cfg['suspect_score_threshold'] * 0.38)
        elif route_compatible and hdiff is not None and hdiff <= 28.0 and ds >= -5.0 and proj.dist <= 145.0:
            self.score = min(self.score, self.cfg['suspect_score_threshold'] * 0.58)
        if plausible_shortcut:
            if 'shortcut_bad_sustained' in locals() and shortcut_bad_sustained:
                self.score = min(self.score, self.cfg['spoken_score_threshold'] * 0.92)
            else:
                self.score = min(self.score, self.cfg['suspect_score_threshold'] * (0.34 if chord_shortcut_plausible else 0.38))
        if vector_bias_plausible:
            self.score = min(self.score, self.cfg['suspect_score_threshold'] * 0.40)
        if shortcut_route_compatible:
            self.score = min(self.score, self.cfg['suspect_score_threshold'] * 0.34)
        if int(p.trusted_level or 1) >= 3:
            self.score = min(self.score, self.cfg['suspect_score_threshold'] * 0.62)
        elif int(p.trusted_level or 1) == 2 and route_compatible:
            self.score = min(self.score, self.cfg['suspect_score_threshold'] * 0.60)

        # Good matched points refresh last_good_s. This is useful to debug and can be used for reroute recovery.
        if route_compatible and residual <= soft + 8.0:
            self.last_good_s = proj.s
            self.last_good_ts_ms = p.timestamp_ms

        immediate = False
        if not start_grace and not near_end and p.usable and not sensor_skip and not plausible_shortcut and not vector_bias_plausible and not route_compatible:
            if int(p.trusted_level or 1) <= 2 and residual > hard + 45.0 and (hdiff is None or hdiff > 75.0 or proj.endpoint_gap > 55.0):
                immediate = True
                reasons.append('immediate_far_or_endpoint_overrun')
            elif int(p.trusted_level or 1) <= 2 and proj.endpoint_gap > 58.0 and p.speed >= 2.5 and ds < 1.0:
                immediate = True
                reasons.append('immediate_endpoint_overrun')
            elif int(p.trusted_level or 1) <= 2 and proj.dist > raw_hard + 65.0:
                immediate = True
                reasons.append('immediate_raw_far')
            elif instant >= self.cfg['immediate_score_threshold']:
                immediate = True
                reasons.append('immediate_score')

        spoken_physical_evidence = bool(
            residual >= soft + 8.0
            or proj.endpoint_gap >= 45.0
            or proj.dist >= soft + 20.0
            or (hdiff is not None and hdiff >= 95.0 and proj.dist >= 52.0 and abs(ds) <= 2.5 and p.speed >= 2.0)
            or (self.prev_proj is not None and ds < -35.0 and proj.dist >= 38.0)
        )
        if self.off_route:
            self.state = 'OFF_ROUTE'
        else:
            if immediate or (self.score >= self.cfg['spoken_score_threshold'] and spoken_physical_evidence):
                self.off_route = True
                self.state = 'OFF_ROUTE'
            elif self.score >= self.cfg['silent_suspect_threshold']:
                self.state = 'SUSPECT'
            else:
                self.state = 'ON_ROUTE'

        if not sensor_skip:
            self.prev_point = p
            self.prev_proj = proj
        elif self.prev_point is None:
            # Keep initial bad fixes from poisoning projection state.
            self.prev_point = p

        metrics = dict(
            timestamp_ms=p.timestamp_ms,
            speed=p.speed,
            heading=p.heading,
            trusted_level=p.trusted_level,
            dt=dt,
            s=proj.s,
            ds=ds,
            route_heading=route_heading,
            heading_diff=hdiff,
            raw_dist=proj.dist,
            signed_dist=proj.signed_dist,
            endpoint_gap=proj.endpoint_gap,
            bias=self.bias,
            bias_conf=self.bias_conf,
            residual=residual,
            soft=soft,
            hard=hard,
            raw_hard=raw_hard,
            score=self.score,
            instant=instant,
            start_grace=start_grace,
            near_end=near_end,
            near_turn=near_turn,
            route_compatible=route_compatible,
            progress_forward=progress_forward,
            duplicate_time=duplicate_time,
            gps_step=gps_step,
            sensor_spike=sensor_spike,
            sensor_skip=sensor_skip,
            progress_upper=progress_upper,
            progress_plausible=progress_plausible,
            lateral_change=lateral_change,
            growing_lateral_departure=growing_lateral_departure,
            moving_away_from_start=moving_away_from_start,
            opposite_to_route_near_start=opposite_to_route_near_start,
            approaching_start_extension=approaching_start_extension,
            plausible_shortcut=plausible_shortcut,
            shortcut_projection_used=shortcut_projection_used,
            future_hdiff=future_hdiff,
            vector_bias_plausible=vector_bias_plausible,
            vector_bias_residual=vector_bias_residual,
            corrected_hdiff=corrected_hdiff,
            bias_x=self.bias_x,
            bias_y=self.bias_y,
            bias_vec_conf=self.bias_vec_conf,
            vector_route_compatible=vector_route_compatible,
            shortcut_route_compatible=shortcut_route_compatible,
            chord_shortcut_plausible=chord_shortcut_plausible,
            chord_shortcut_target_s=chord_shortcut_target_s,
            chord_shortcut_dist=chord_shortcut_dist,
            chord_shortcut_heading_diff=chord_shortcut_heading_diff,
            shortcut_bad_sustained=locals().get('shortcut_bad_sustained', False),
            normal_route_compatible=locals().get('normal_route_compatible', False),
            spoken_physical_evidence=locals().get('spoken_physical_evidence', False),
        )
        return DetectorOutput(self.off_route, self.state, ','.join(reasons) if reasons else 'ok', proj, metrics)


def load_route_geojson(path: str) -> RouteIndex:
    data = json.load(open(path, encoding='utf-8'))
    if data.get('type') == 'FeatureCollection':
        feats = data['features']
    elif data.get('type') == 'Feature':
        feats = [data]
    else:
        feats = [{'geometry': data, 'properties': {}}]
    feats = sorted(feats, key=lambda f: f.get('properties', {}).get('id', 0))
    coords: List[Tuple[float, float]] = []
    for ft in feats:
        if ft['geometry']['type'] != 'LineString':
            continue
        cs = [tuple(map(float, c[:2])) for c in ft['geometry']['coordinates']]
        if coords and cs and abs(cs[0][0] - coords[-1][0]) < 1e-10 and abs(cs[0][1] - coords[-1][1]) < 1e-10:
            cs = cs[1:]
        coords.extend(cs)
    return RouteIndex(coords)


def load_gps_geojson(path: str, dedup: bool = True) -> List[GpsPoint]:
    data = json.load(open(path, encoding='utf-8'))
    feats = data['features'] if data.get('type') == 'FeatureCollection' else [data]
    feats = sorted(feats, key=lambda f: (f.get('properties', {}).get('timestamp', 0), f.get('properties', {}).get('id', 0)))
    out: List[GpsPoint] = []
    seen = set()
    for ft in feats:
        if ft['geometry']['type'] != 'Point':
            continue
        c = ft['geometry']['coordinates']
        prop = ft.get('properties', {})
        key = (prop.get('timestamp', 0), round(float(c[0]), 8), round(float(c[1]), 8))
        if dedup and key in seen:
            continue
        seen.add(key)
        out.append(GpsPoint(
            lon=float(c[0]), lat=float(c[1]),
            timestamp_ms=int(prop.get('timestamp', 0) or 0),
            speed=float(prop.get('speed', 0.0) or 0.0),
            heading=float(prop.get('heading', 0.0) or 0.0),
            usable=bool(prop.get('usable', True)),
            trusted_level=int(prop.get('trustedLevel', 1) or 1),
            raw=prop,
        ))
    return out


def run_detector(route_path: str, gps_path: str) -> List[DetectorOutput]:
    route = load_route_geojson(route_path)
    gps = load_gps_geojson(gps_path)
    det = OffRouteDetector(route)
    return [det.update(p) for p in gps]


if __name__ == '__main__':
    import argparse, os, csv
    ap = argparse.ArgumentParser()
    ap.add_argument('route_geojson')
    ap.add_argument('gps_geojson')
    ap.add_argument('--csv', help='write per-point debug CSV')
    args = ap.parse_args()
    route = load_route_geojson(args.route_geojson)
    gps = load_gps_geojson(args.gps_geojson)
    det = OffRouteDetector(route)
    first_off = None
    rows = []
    for i, p in enumerate(gps):
        o = det.update(p)
        if o.off_route and first_off is None:
            first_off = i
        m = o.metrics
        rows.append(dict(
            i=i, gps_id=(p.raw or {}).get('id'), off_route=o.off_route, state=o.state, reason=o.reason,
            lon=p.lon, lat=p.lat, timestamp_ms=p.timestamp_ms,
            s=round(m['s'], 2), ds=round(m['ds'], 2), raw_dist=round(m['raw_dist'], 2), endpoint_gap=round(m['endpoint_gap'], 2),
            residual=round(m['residual'], 2), signed_dist=round(m['signed_dist'], 2), bias=round(m['bias'], 2),
            bias_conf=round(m['bias_conf'], 2), speed=round(p.speed, 2), heading=round(p.heading, 2),
            route_heading=round(m['route_heading'], 2), heading_diff='' if m['heading_diff'] is None else round(m['heading_diff'], 2),
            score=round(m['score'], 2), instant=round(m['instant'], 2), soft=round(m['soft'], 2), hard=round(m['hard'], 2),
            start_grace=m['start_grace'], route_compatible=m['route_compatible']))
    print(f'route_len_m={route.length:.1f}, gps_n={len(gps)}, first_off_index={first_off}')
    if first_off is not None:
        print(rows[first_off])
    if args.csv:
        with open(args.csv, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print(args.csv)
