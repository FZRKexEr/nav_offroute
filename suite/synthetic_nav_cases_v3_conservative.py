#!/usr/bin/env python3
from __future__ import annotations
"""
Synthetic navigation cases, conservative default labeling.

This suite intentionally labels many ambiguous/shortcut/bias cases as no-off-route,
matching a cautious product policy: only declare off-route when the route hypothesis is
no longer a plausible explanation under GPS bias/noise or a likely local shortcut/rejoin.

Requires synthetic_nav_cases_v2.py in PYTHONPATH or same directory.
"""
import argparse
import json
import math
import os
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import synthetic_nav_cases_v2 as v2

Point = Tuple[float, float]
EARTH_R = v2.EARTH_R
CENTER_LON = v2.CENTER_LON
CENTER_LAT = v2.CENTER_LAT


def ll_to_xy(lon: float, lat: float) -> Point:
    x = math.radians(lon - CENTER_LON) * EARTH_R * math.cos(math.radians(CENTER_LAT))
    y = math.radians(lat - CENTER_LAT) * EARTH_R
    return x, y


def update_point_geometry(feat: Dict[str, Any], x: float, y: float) -> None:
    lon, lat = v2.xy_to_ll(x, y)
    feat["geometry"]["coordinates"] = [lon, lat]
    feat["properties"]["point"] = f"{lat:.7f}, {lon:.7f}"


def add_global_bias_to_gps(rec: Dict[str, Any], rng: random.Random,
                           bias_mag: float, drift_final_mag: float = 0.0,
                           wobble_std: float = 1.5) -> Dict[str, Any]:
    """Apply a global XY offset to measured GPS positions, not to actualX/actualY."""
    ang = rng.uniform(0, 2 * math.pi)
    bx0, by0 = bias_mag * math.cos(ang), bias_mag * math.sin(ang)
    dang = ang + rng.uniform(-1.1, 1.1)
    bxf, byf = drift_final_mag * math.cos(dang), drift_final_mag * math.sin(dang)
    feats = rec["gps_geojson"]["features"]
    n = max(1, len(feats) - 1)
    # Low-frequency correlated wobble.
    wx = wy = 0.0
    for i, ft in enumerate(feats):
        lon, lat = ft["geometry"]["coordinates"][:2]
        x, y = ll_to_xy(float(lon), float(lat))
        u = i / n
        # Smooth transition so the displacement behaves like a bias vector, not iid noise.
        bx = bx0 + (bxf - bx0) * (3*u*u - 2*u*u*u)
        by = by0 + (byf - by0) * (3*u*u - 2*u*u*u)
        wx = 0.94 * wx + rng.gauss(0, wobble_std * 0.30)
        wy = 0.94 * wy + rng.gauss(0, wobble_std * 0.30)
        update_point_geometry(ft, x + bx + wx, y + by + wy)
        ft["properties"]["globalBiasM"] = round(math.hypot(bx, by), 2)
    rec["meta"]["global_bias_m"] = round(bias_mag, 2)
    rec["meta"]["global_bias_drift_final_m"] = round(drift_final_mag, 2)
    return rec


def add_bias_episode(rec: Dict[str, Any], rng: random.Random,
                     peak_mag: float, start_frac: float, end_frac: float,
                     wobble_std: float = 1.5) -> Dict[str, Any]:
    """Temporary multipath/building-canyon bias episode; label usually remains no-off."""
    ang = rng.uniform(0, 2 * math.pi)
    bx, by = peak_mag * math.cos(ang), peak_mag * math.sin(ang)
    feats = rec["gps_geojson"]["features"]
    n = max(1, len(feats) - 1)
    wx = wy = 0.0
    for i, ft in enumerate(feats):
        lon, lat = ft["geometry"]["coordinates"][:2]
        x, y = ll_to_xy(float(lon), float(lat))
        u = i / n
        if u < start_frac or u > end_frac:
            w = 0.0
        else:
            q = (u - start_frac) / max(1e-6, end_frac - start_frac)
            # smooth bump: 0 -> 1 -> 0
            w = math.sin(math.pi * q)
        wx = 0.93 * wx + rng.gauss(0, wobble_std * 0.35)
        wy = 0.93 * wy + rng.gauss(0, wobble_std * 0.35)
        update_point_geometry(ft, x + bx*w + wx, y + by*w)
        ft["properties"]["biasEpisodeWeight"] = round(w, 3)
    rec["meta"]["bias_episode_peak_m"] = round(peak_mag, 2)
    return rec


def route_len(path: Sequence[Point]) -> float:
    return v2.polyline_lengths(path)[1]


def base_default_specs(seed: int, scale: float) -> List[v2.CaseSpec]:
    """Base cases whose labels are still valid under cautious semantics."""
    rng = random.Random(seed)
    def n(x: int) -> int:
        return max(1, int(round(x * scale)))
    specs: List[v2.CaseSpec] = []
    # Correct turns and obvious missed turns remain essential.
    specs += v2.right_angle(rng, n(520), n(520), n(180), n(180))
    specs += v2.uturn(rng, n(260), n(260), 1)  # we replace early uturn labels below.
    specs[-1].category = "uturn_early_allowed_nooff"
    specs[-1].should_off = False
    specs[-1].off_start_s = None
    # Existing shortcuts from v2 were labeled off. Under conservative policy they become mostly allowed.
    specs += v2.shortcut(rng, 1, 1, n(180))
    for sp in specs[-(2 + n(180)):]:
        if sp.category in ("shortcut_block_straight", "shortcut_diagonal"):
            sp.category += "_allowed_nooff"
            sp.should_off = False
            sp.off_start_s = None
            sp.off_end_s = None
            sp.max_delay_s = 0
            sp.note += "; relabeled as tolerated shortcut/rejoin"
    specs += v2.parallel(rng, n(320), n(220), n(220))
    # In gradual drift, a lot of large drifts are GPS bias/building canyon until shape/progress contradicts route.
    for sp in specs:
        if sp.category == "gradual_lateral_drift_probe" and sp.should_off and abs(sp.actual_xy[-1][1]) <= 78:
            sp.category = "gradual_lateral_drift_ambiguous_nooff"
            sp.should_off = False
            sp.off_start_s = None
            sp.note += "; conservative ambiguous nooff"
    # Extra complex cases from v2 but do not over-weight old off labels.
    extra = v2.loop_roundabout_fork_noise_wrong(rng)
    rng.shuffle(extra)
    specs += extra[:n(850)]
    return specs


def conservative_extra_specs(seed: int, scale: float) -> List[v2.CaseSpec]:
    rng = random.Random(seed + 404)
    def n(x: int) -> int:
        return max(1, int(round(x * scale)))
    out: List[v2.CaseSpec] = []

    # Early U-turn / crossing to opposite side: tolerated if it reaches the intended return corridor.
    for _ in range(n(520)):
        L = rng.uniform(95, 380)
        sep = rng.uniform(8, 58)
        e = rng.uniform(6, min(130, L * 0.55))
        d = rng.choice([-1, 1])
        route = [(0, 0), (L, 0), (L, d * sep), (0, d * sep)]
        # Many drivers cross early to the opposite carriageway. Route still becomes plausible after the turn.
        mid = rng.uniform(0.25, 0.75)
        actual = [(0, 0), (L - e, 0), (L - e * mid, d * sep * rng.uniform(0.45, 0.8)), (L - e, d * sep), (0, d * sep)]
        out.append(v2.CaseSpec(
            "uturn_early_allowed_nooff", route, actual, False,
            speed=rng.uniform(2.5, 15), sample_dt=rng.uniform(.6, 2.3), noise_std=rng.uniform(1.5, 9),
            lateral_bias=rng.uniform(-8, 8), heading_noise=rng.uniform(2, 14),
            stop_at_s=(L - e + rng.uniform(-6, 8) if rng.random() < .12 else None), stop_duration_s=rng.uniform(1.5, 10),
            note="early U-turn/cross-over to intended opposite corridor; conservative tolerated"))

    # Extreme too-early U-turn that does not plausibly serve the planned route and stays away.
    for _ in range(n(120)):
        L = rng.uniform(220, 520)
        sep = rng.uniform(45, 110)
        e = rng.uniform(min(135, L*0.35), min(260, L*0.75))
        d = rng.choice([-1, 1])
        route = [(0, 0), (L, 0), (L, d * sep), (0, d * sep)]
        actual = [(0, 0), (L - e, 0), (L - e, d * sep), (max(0, L - e - rng.uniform(80, 220)), d * sep)]
        out.append(v2.CaseSpec(
            "uturn_too_early_far_off", route, actual, True, off_start_s=L-e+rng.uniform(8, 18),
            speed=rng.uniform(3, 15), sample_dt=rng.uniform(.7, 2.2), noise_std=rng.uniform(1, 8),
            lateral_bias=rng.uniform(-6, 6), heading_noise=rng.uniform(2, 12), max_delay_s=rng.uniform(10, 20),
            note="very early U-turn, far from intended maneuver and no plausible immediate rejoin"))

    # Route around a block, driver takes a local shortcut and rejoins. Tolerated.
    for _ in range(n(480)):
        A = rng.uniform(120, 520); B = rng.uniform(45, 220); C = rng.uniform(20, 160)
        # Several dogleg/block shapes.
        typ = rng.random()
        if typ < 0.35:
            route = [(0,0),(0,B),(A,B),(A+C,0)]
            actual = [(0,0),(A+C,0)]
        elif typ < 0.70:
            route = [(0,0),(A,0),(A,B),(A+C,B)]
            actual = [(0,0),(A+C,B)]
        else:
            route = [(0,0),(A*0.45,0),(A*0.45,B),(A,B),(A+C,0)]
            actual = [(0,0),(A+C,0)]
        out.append(v2.CaseSpec(
            "shortcut_rejoin_allowed_nooff", route, actual, False,
            speed=rng.uniform(3.5, 18), sample_dt=rng.uniform(.6, 2.2), noise_std=rng.uniform(1.5, 9),
            lateral_bias=rng.uniform(-8, 8), heading_noise=rng.uniform(2, 12),
            note="driver cuts through shorter local path and rejoins planned route; tolerated"))

    # Long diversion without near-term rejoin: still off, but allow longer delay.
    for _ in range(n(220)):
        A = rng.uniform(140, 450); B = rng.uniform(85, 260); extra = rng.uniform(110, 340)
        d = rng.choice([-1, 1])
        route = [(0,0),(A,0),(A,d*B),(A+extra,d*B)]
        actual = [(0,0),(A*0.65,0),(A*0.9,-d*rng.uniform(45,130)),(A+extra,-d*rng.uniform(65,180))]
        out.append(v2.CaseSpec(
            "shortcut_wrong_corridor_off", route, actual, True, off_start_s=A*0.65+rng.uniform(12,25),
            speed=rng.uniform(4,18), sample_dt=rng.uniform(.7,2.1), noise_std=rng.uniform(1,8),
            lateral_bias=rng.uniform(-6,6), heading_noise=rng.uniform(2,11), max_delay_s=rng.uniform(12,22),
            note="leaves into different corridor and does not plausibly rejoin soon"))

    # GPS vector bias: route shape is correct but entire measured trajectory is shifted, including turns.
    for _ in range(n(560)):
        L1 = rng.uniform(80, 320); L2 = rng.uniform(70, 280); L3 = rng.uniform(50, 220); d = rng.choice([-1,1])
        route = [(0,0),(L1,0),(L1,d*L2),(L1+L3,d*L2)] if rng.random()<0.55 else [(0,0),(L1,0),(L1+L2*0.7,d*L2*0.25),(L1+L3,d*L2*0.25)]
        out.append(v2.CaseSpec(
            "building_canyon_vector_bias_nooff", route, route, False,
            speed=rng.uniform(2.5, 18), sample_dt=rng.uniform(.6,2.4), noise_std=rng.uniform(1,6),
            lateral_bias=rng.uniform(-2,2), heading_noise=rng.uniform(2,14),
            duplicate_rate=(0.015 if rng.random()<.08 else 0),
            note="same route shape but GPS globally shifted by urban canyon/multipath"))

    # Temporary bias episode: not off; should decay/ignore if route shape recovers.
    for _ in range(n(320)):
        L = rng.uniform(220, 800)
        route = [(0,0),(L*.35,0),(L*.55,rng.uniform(-30,30)),(L*.78,rng.uniform(-30,30)),(L,rng.uniform(-25,25))]
        out.append(v2.CaseSpec(
            "temporary_bias_episode_nooff", route, route, False,
            speed=rng.uniform(3,18), sample_dt=rng.uniform(.7,2.2), noise_std=rng.uniform(1,5),
            lateral_bias=rng.uniform(-4,4), heading_noise=rng.uniform(2,11),
            note="temporary coherent GPS offset due to building canyon; route shape later recovers"))

    # Parallel road ambiguity: modest separation and same shape should not be decisive without road graph.
    for _ in range(n(320)):
        L = rng.uniform(180, 760); sep = rng.choice([-1,1])*rng.uniform(28, 75)
        route = [(0,0),(L*.35,0),(L*.65,rng.uniform(-25,25)),(L,rng.uniform(-25,25))]
        actual = [(x, y+sep) for x,y in route]
        out.append(v2.CaseSpec(
            "parallel_same_shape_ambiguous_nooff", route, actual, False,
            speed=rng.uniform(3,19), sample_dt=rng.uniform(.7,2.2), noise_std=rng.uniform(1,7),
            lateral_bias=rng.uniform(-4,4), heading_noise=rng.uniform(2,11),
            note="same-shape parallel offset could be GPS bias / service road; conservative nooff"))

    # Obvious parallel branch: larger separation after a real fork and sustained no rejoin.
    for _ in range(n(220)):
        L = rng.uniform(300, 950); dep = rng.uniform(35, 150); ramp = rng.uniform(45, 130); sep = rng.choice([-1,1])*rng.uniform(95, 210)
        route = [(0,0),(L,0)]
        actual = [(0,0),(dep,0),(dep+ramp,sep),(L,sep+rng.uniform(-20,20))]
        out.append(v2.CaseSpec(
            "parallel_branch_sustained_far_off", route, actual, True, off_start_s=dep+ramp*0.55,
            speed=rng.uniform(4,20), sample_dt=rng.uniform(.7,2.1), noise_std=rng.uniform(1,8),
            lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(12,24),
            note="sustained far branch after fork; less likely pure GPS bias"))

    # Urban tunnel/poor GNSS: distance can be large but points unreliable.
    for _ in range(n(220)):
        L1 = rng.uniform(150, 550); L2 = rng.uniform(80, 260); d = rng.choice([-1,1])
        route = [(0,0),(L1,0),(L1,d*L2)]
        out.append(v2.CaseSpec(
            "poor_gnss_large_error_nooff", route, route, False,
            speed=rng.uniform(2,16), sample_dt=rng.uniform(.8,2.8), noise_std=rng.uniform(8,28),
            lateral_bias=rng.choice([-1,1])*rng.uniform(20,90), heading_noise=rng.uniform(10,45),
            trusted_level_base=rng.choice([2,3,4]), bad_heading_rate=rng.uniform(.08,.35),
            note="low-trust building/tunnel GPS can be far from route; conservative nooff"))

    return out


def mutate_record_by_category(rec: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    cat = rec["meta"]["category"]
    if cat == "building_canyon_vector_bias_nooff":
        return add_global_bias_to_gps(rec, rng, rng.uniform(25, 95), rng.uniform(20, 100), rng.uniform(1.0, 5.0))
    if cat == "temporary_bias_episode_nooff":
        return add_bias_episode(rec, rng, rng.uniform(35, 125), rng.uniform(.18, .38), rng.uniform(.55, .85), rng.uniform(1.0, 5.0))
    if cat == "poor_gnss_large_error_nooff" and rng.random() < .45:
        return add_bias_episode(rec, rng, rng.uniform(40, 140), rng.uniform(.05, .25), rng.uniform(.65, .95), rng.uniform(2.0, 8.0))
    return rec


def generate_jsonl(out: str, seed: int = 20260421, scale: float = 1.0, limit: Optional[int] = None) -> Dict[str, Any]:
    rng = random.Random(seed + 909)
    specs = base_default_specs(seed, scale) + conservative_extra_specs(seed, scale)
    rng.shuffle(specs)
    if limit is not None:
        specs = specs[:limit]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    summary: Dict[str, Any] = {"seed": seed, "scale": scale, "case_count": 0, "offroute_expected_count": 0, "nooff_expected_count": 0, "categories": {}}
    with open(out, "w", encoding="utf-8") as f:
        for i, sp in enumerate(specs):
            cid = f"case_{i:05d}_{sp.category}"
            rec = v2.sample_case(rng, cid, sp, 1774934662669 + i * 260000)
            rec = mutate_record_by_category(rec, rng)
            meta = rec["meta"]; cat = meta["category"]
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            summary["case_count"] += 1
            summary["offroute_expected_count"] += int(meta["should_off_route"])
            summary["nooff_expected_count"] += int(not meta["should_off_route"])
            summary["categories"].setdefault(cat, {"count": 0, "off_expected": 0})
            summary["categories"][cat]["count"] += 1
            summary["categories"][cat]["off_expected"] += int(meta["should_off_route"])
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/mnt/data/synthetic_nav_suite_v3_conservative.jsonl")
    ap.add_argument("--summary", default="/mnt/data/synthetic_nav_suite_v3_conservative_summary.json")
    ap.add_argument("--seed", type=int, default=20260421)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    summary = generate_jsonl(args.out, args.seed, args.scale, args.limit)
    with open(args.summary, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
