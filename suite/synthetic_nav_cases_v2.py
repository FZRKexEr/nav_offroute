#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, os, random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

EARTH_R = 6371000.0
CENTER_LON = 113.93
CENTER_LAT = 22.53
Point = Tuple[float, float]

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def heading_from_vec(dx: float, dy: float) -> float:
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0

def angle_to_vec(h: float) -> Point:
    r = math.radians(h)
    return math.sin(r), math.cos(r)

def rotate(points: Sequence[Point], deg: float) -> List[Point]:
    r = math.radians(deg); c = math.cos(r); s = math.sin(r)
    return [(x*c - y*s, x*s + y*c) for x, y in points]

def transform(points: Sequence[Point], deg: float, tx: float, ty: float) -> List[Point]:
    return [(x+tx, y+ty) for x, y in rotate(points, deg)]

def xy_to_ll(x: float, y: float) -> Tuple[float, float]:
    lon = CENTER_LON + math.degrees(x / (EARTH_R * math.cos(math.radians(CENTER_LAT))))
    lat = CENTER_LAT + math.degrees(y / EARTH_R)
    return round(lon, 7), round(lat, 7)

def polyline_lengths(path: Sequence[Point]) -> Tuple[List[float], float]:
    cum = [0.0]; total = 0.0
    for a, b in zip(path, path[1:]):
        total += math.hypot(b[0]-a[0], b[1]-a[1])
        cum.append(total)
    return cum, total

def densify(path: Sequence[Point], max_step: float = 12.0) -> List[Point]:
    out = [path[0]]
    for a, b in zip(path, path[1:]):
        L = math.hypot(b[0]-a[0], b[1]-a[1])
        n = max(1, int(math.ceil(L / max_step)))
        for k in range(1, n+1):
            t = k / n
            out.append((a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t))
    return out

def interp_cached(path: Sequence[Point], cum: Sequence[float], total: float, s: float, hint: int = 0):
    s = clamp(s, 0.0, total)
    i = max(0, min(hint, len(path)-2))
    while i < len(path)-2 and s > cum[i+1]:
        i += 1
    while i > 0 and s < cum[i]:
        i -= 1
    a, b = path[i], path[i+1]
    L = max(1e-9, cum[i+1]-cum[i])
    t = clamp((s - cum[i]) / L, 0.0, 1.0)
    x = a[0] + (b[0]-a[0])*t
    y = a[1] + (b[1]-a[1])*t
    return (x, y), heading_from_vec(b[0]-a[0], b[1]-a[1]), i

def arc_points(cx: float, cy: float, r: float, a0: float, a1: float, n: int) -> List[Point]:
    return [(cx + r*math.cos(math.radians(a0+(a1-a0)*i/max(1,n))),
             cy + r*math.sin(math.radians(a0+(a1-a0)*i/max(1,n)))) for i in range(n+1)]

@dataclass
class CaseSpec:
    category: str
    route_xy: List[Point]
    actual_xy: List[Point]
    should_off: bool
    off_start_s: Optional[float] = None
    off_end_s: Optional[float] = None
    speed: float = 10.0
    sample_dt: float = 1.0
    noise_std: float = 4.0
    lateral_bias: float = 0.0
    heading_noise: float = 4.0
    max_delay_s: float = 10.0
    spike_count: int = 0
    spike_usable: bool = False
    spike_distance_min: float = 90.0
    spike_distance_max: float = 220.0
    trusted_level_base: int = 1
    bad_heading_rate: float = 0.0
    stop_at_s: Optional[float] = None
    stop_duration_s: float = 0.0
    duplicate_rate: float = 0.0
    note: str = ""

def route_geojson(route_xy: Sequence[Point], cid: str, cat: str) -> Dict[str, Any]:
    return {"type":"FeatureCollection","features":[{
        "type":"Feature","properties":{"id":0,"caseId":cid,"category":cat},
        "geometry":{"type":"LineString","coordinates":[list(xy_to_ll(x,y)) for x,y in route_xy]}}]}

def point_feature(i: int, x: float, y: float, ts: int, speed: float, heading: float, usable: bool, trusted: int, gt: bool, actual: Point, cid: str) -> Dict[str, Any]:
    lon, lat = xy_to_ll(x, y)
    return {"type":"Feature","properties":{
        "id":i,"usable":bool(usable),"point":f"{lat:.7f}, {lon:.7f}","locationType":1,
        "trustedLevel":int(trusted),"timestamp":int(ts),"speed":round(max(0.0,speed),2),
        "heading":round(heading%360.0,2),"gtOffRoute":bool(gt),
        "actualX":round(actual[0],2),"actualY":round(actual[1],2),"caseId":cid},
        "geometry":{"type":"Point","coordinates":[lon,lat]}}

def sample_case(rng: random.Random, cid: str, spec: CaseSpec, base_ts: int) -> Dict[str, Any]:
    rot = rng.uniform(0, 360)
    tx, ty = rng.uniform(-2500, 2500), rng.uniform(-2500, 2500)
    route = transform(densify(spec.route_xy, 18.0), rot, tx, ty)
    actual = transform(densify(spec.actual_xy, 10.0), rot, tx, ty)
    cum, total = polyline_lengths(actual)
    samples: List[Tuple[float, float]] = []
    s = 0.0; t = 0.0; stop_used = False
    while s <= total + 0.1 and len(samples) < 650:
        samples.append((min(s, total), t))
        dt = max(0.35, rng.gauss(spec.sample_dt, spec.sample_dt*0.12))
        v = max(0.8, rng.gauss(spec.speed, max(0.4, spec.speed*0.08)))
        if (not stop_used) and spec.stop_at_s is not None and abs(s-spec.stop_at_s) < max(8.0, spec.speed*spec.sample_dt):
            for _ in range(max(1, int(spec.stop_duration_s / max(0.5, spec.sample_dt)))):
                t += dt
                samples.append((min(s, total), t))
            stop_used = True
        s += v * dt
        t += dt
    if samples[-1][0] < total - 1.0:
        samples.append((total, t + spec.sample_dt))
    feats: List[Dict[str, Any]] = []
    drift = 0.0; hint = 0
    off_end = total + 1.0 if spec.off_end_s is None else spec.off_end_s
    for i, (ss, tt) in enumerate(samples):
        (x, y), h, hint = interp_cached(actual, cum, total, ss, hint)
        vx, vy = angle_to_vec(h); nx, ny = -vy, vx
        drift = 0.96*drift + rng.gauss(0, spec.noise_std*0.08)
        lat_err = spec.lateral_bias + drift + rng.gauss(0, spec.noise_std)
        lon_err = rng.gauss(0, spec.noise_std*0.45)
        mx, my = x + nx*lat_err + vx*lon_err, y + ny*lat_err + vy*lon_err
        if i == 0:
            spd = 0.0 if spec.speed < 2 else max(0.0, rng.gauss(spec.speed, spec.speed*0.08))
        else:
            ds = max(0.0, ss - samples[i-1][0])
            dt = max(0.2, tt - samples[i-1][1])
            spd = ds / dt
        head = h + rng.gauss(0, spec.heading_noise)
        if rng.random() < spec.bad_heading_rate:
            head = rng.uniform(0, 360)
        if spd < 0.6:
            head = 0.0
        gt = bool(spec.should_off and spec.off_start_s is not None and ss >= spec.off_start_s and ss <= off_end)
        feats.append(point_feature(len(feats), mx, my, base_ts+int(round(tt*1000)), spd, head, True, spec.trusted_level_base, gt, (x,y), cid))
        if rng.random() < spec.duplicate_rate and i > 2:
            dup = json.loads(json.dumps(feats[-1]))
            dup["properties"]["id"] = len(feats)
            feats.append(dup)
    # Inject sensor spikes; ground truth remains unchanged.
    idxs = list(range(3, max(3, len(feats)-3)))
    rng.shuffle(idxs)
    for idx in idxs[:spec.spike_count]:
        f = feats[idx]
        ax, ay = f["properties"]["actualX"], f["properties"]["actualY"]
        d = rng.uniform(spec.spike_distance_min, spec.spike_distance_max)
        a = rng.uniform(0, 2*math.pi)
        sx, sy = ax + d*math.cos(a), ay + d*math.sin(a)
        lon, lat = xy_to_ll(sx, sy)
        f["geometry"]["coordinates"] = [lon, lat]
        f["properties"]["point"] = f"{lat:.7f}, {lon:.7f}"
        f["properties"]["usable"] = bool(spec.spike_usable)
        f["properties"]["trustedLevel"] = 4
        f["properties"]["isSyntheticSpike"] = True
    gt_idxs = [i for i, f in enumerate(feats) if f["properties"].get("gtOffRoute")]
    true_off_idx = gt_idxs[0] if gt_idxs else None
    latest = None
    if spec.should_off and true_off_idx is not None:
        t0 = feats[true_off_idx]["properties"]["timestamp"]
        target = t0 + int(spec.max_delay_s*1000)
        latest = gt_idxs[-1]
        for j in gt_idxs:
            if feats[j]["properties"]["timestamp"] >= target:
                latest = j; break
    meta = {
        "case_id": cid, "category": spec.category, "should_off_route": bool(spec.should_off),
        "true_off_idx": true_off_idx, "latest_detect_idx": latest, "point_count": len(feats),
        "route_length_m": round(polyline_lengths(route)[1], 2), "actual_length_m": round(total, 2),
        "speed_mps_nominal": round(spec.speed, 2), "sample_dt_s_nominal": round(spec.sample_dt, 2),
        "noise_std_m": round(spec.noise_std, 2), "lateral_bias_m": round(spec.lateral_bias, 2),
        "max_delay_s": round(spec.max_delay_s, 2), "note": spec.note
    }
    return {"meta": meta, "route_geojson": route_geojson(route, cid, spec.category), "gps_geojson": {"type":"FeatureCollection","features":feats}}

# ---- categories ----

def right_angle(rng: random.Random, follow: int, miss: int, late: int, early: int) -> List[CaseSpec]:
    out = []
    for _ in range(follow):
        L1, L2, d = rng.uniform(45,280), rng.uniform(40,260), rng.choice([-1,1])
        route = [(0,0),(L1,0),(L1,d*L2)]
        cut = rng.uniform(0,14)
        actual = [(0,0),(L1-cut,0),(L1,d*cut),(L1,d*L2)] if cut > 2 else route
        out.append(CaseSpec("right_angle_follow", route, actual, False, speed=rng.uniform(3,18), sample_dt=rng.uniform(.7,2.2), noise_std=rng.uniform(1.5,10), lateral_bias=rng.uniform(-10,10), heading_noise=rng.uniform(2,13), stop_at_s=(L1+rng.uniform(-12,12) if rng.random()<.12 else None), stop_duration_s=rng.uniform(3,15), duplicate_rate=(.02 if rng.random()<.1 else 0), note="correct 90-degree turn"))
    for _ in range(miss):
        L1, L2, extra, d = rng.uniform(45,300), rng.uniform(35,240), rng.uniform(70,280), rng.choice([-1,1])
        out.append(CaseSpec("right_angle_missed_turn", [(0,0),(L1,0),(L1,d*L2)], [(0,0),(L1+extra,0)], True, off_start_s=L1+rng.uniform(4,12), speed=rng.uniform(4,20), sample_dt=rng.uniform(.6,2), noise_std=rng.uniform(1,8), lateral_bias=rng.uniform(-6,6), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(5,9), note="route turns 90 degrees; driver continues straight"))
    for _ in range(late):
        L1,L2,late_d,d = rng.uniform(60,260),rng.uniform(60,260),rng.uniform(18,95),rng.choice([-1,1])
        should = late_d >= 26
        out.append(CaseSpec("right_angle_late_turn_parallel", [(0,0),(L1,0),(L1,d*L2)], [(0,0),(L1+late_d,0),(L1+late_d,d*L2)], should, off_start_s=(L1+8 if should else None), speed=rng.uniform(4,18), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(7,12), note="late turn then parallel"))
    for _ in range(early):
        L1,L2,e,d = rng.uniform(70,280),rng.uniform(60,260),rng.uniform(15,85),rng.choice([-1,1])
        should = e >= 28
        out.append(CaseSpec("right_angle_early_turn_parallel", [(0,0),(L1,0),(L1,d*L2)], [(0,0),(L1-e,0),(L1-e,d*L2)], should, off_start_s=(L1-e+8 if should else None), speed=rng.uniform(3.5,17), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(7,12), note="early turn then parallel"))
    return out

def uturn(rng: random.Random, follow: int, miss: int, early: int) -> List[CaseSpec]:
    out=[]
    for _ in range(follow):
        L,sep,d = rng.uniform(90,320), rng.uniform(10,45), rng.choice([-1,1])
        route=[(0,0),(L,0),(L,d*sep),(0,d*sep)]
        cut=min(sep*.35,rng.uniform(0,12))
        actual=[(0,0),(L-cut,0),(L,d*sep*.5),(L-cut,d*sep),(0,d*sep)] if cut>2 else route
        out.append(CaseSpec("uturn_follow", route, actual, False, speed=rng.uniform(3,14), sample_dt=rng.uniform(.7,2.2), noise_std=rng.uniform(1.5,8), lateral_bias=rng.uniform(-7,7), heading_noise=rng.uniform(2,12), stop_at_s=(L+rng.uniform(-8,8) if rng.random()<.15 else None), stop_duration_s=rng.uniform(2,12), note="correct U-turn/hairpin"))
    for _ in range(miss):
        L,sep,extra,d = rng.uniform(80,300), rng.uniform(10,42), rng.uniform(70,260), rng.choice([-1,1])
        out.append(CaseSpec("uturn_missed_continue_straight", [(0,0),(L,0),(L,d*sep),(0,d*sep)], [(0,0),(L+extra,0)], True, off_start_s=L+rng.uniform(5,12), speed=rng.uniform(4,18), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(5,9), note="route requires U-turn; driver continues"))
    for _ in range(early):
        L,sep,e,d = rng.uniform(110,300), rng.uniform(12,45), rng.uniform(25,110), rng.choice([-1,1])
        out.append(CaseSpec("uturn_early", [(0,0),(L,0),(L,d*sep),(0,d*sep)], [(0,0),(L-e,0),(L-e,d*sep),(0,d*sep)], True, off_start_s=L-e+7, speed=rng.uniform(3,14), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,11), max_delay_s=rng.uniform(7,12), note="U-turn too early"))
    return out

def shortcut(rng: random.Random, block: int, diag: int, small: int) -> List[CaseSpec]:
    out=[]
    for _ in range(block):
        A,B = rng.uniform(120,420), rng.uniform(55,180)
        out.append(CaseSpec("shortcut_block_straight", [(0,0),(0,B),(A,B),(A,0)], [(0,0),(A,0)], True, off_start_s=rng.uniform(8,20), off_end_s=A-rng.uniform(8,20), speed=rng.uniform(4,17), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(6,11), note="same-block shortcut and rejoin"))
    for _ in range(diag):
        A,B,C = rng.uniform(110,360), rng.uniform(60,220), rng.uniform(70,280)
        alen=math.hypot(A+C,B)
        out.append(CaseSpec("shortcut_diagonal", [(0,0),(A,0),(A,B),(A+C,B)], [(0,0),(A+C,B)], True, off_start_s=rng.uniform(12,28), off_end_s=alen-rng.uniform(10,25), speed=rng.uniform(4,17), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(7,13), note="diagonal shortcut across dogleg"))
    for _ in range(small):
        L1,L2,d,cut = rng.uniform(60,240),rng.uniform(60,240),rng.choice([-1,1]),rng.uniform(5,18)
        out.append(CaseSpec("small_corner_cut_nooff", [(0,0),(L1,0),(L1,d*L2)], [(0,0),(L1-cut,0),(L1,d*cut),(L1,d*L2)], False, speed=rng.uniform(3,16), sample_dt=rng.uniform(.7,2.2), noise_std=rng.uniform(1,6), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,9), note="small corner cut tolerated"))
    return out

def parallel(rng: random.Random, bias: int, off: int, drift: int) -> List[CaseSpec]:
    out=[]
    for _ in range(bias):
        L=rng.uniform(180,650)
        route=[(0,0),(L*.4,0),(L*.75,rng.uniform(-35,35)),(L,rng.uniform(-35,35))] if rng.random()<.35 else [(0,0),(L,0)]
        out.append(CaseSpec("parallel_measurement_bias_nooff", route, route, False, speed=rng.uniform(4,19), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,6), lateral_bias=rng.choice([-1,1])*rng.uniform(8,42), heading_noise=rng.uniform(2,9), note="stable same-direction GPS/map lateral bias"))
    for _ in range(off):
        L,dep,ramp,sep = rng.uniform(250,800), rng.uniform(35,160), rng.uniform(20,90), rng.choice([-1,1])*rng.uniform(52,125)
        out.append(CaseSpec("parallel_road_after_branch_off", [(0,0),(L,0)], [(0,0),(dep,0),(dep+ramp,sep),(L,sep)], True, off_start_s=dep+min(ramp*.45,25), speed=rng.uniform(4,20), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-4,4), heading_noise=rng.uniform(2,9), max_delay_s=rng.uniform(8,14), note="branch to parallel road"))
    for _ in range(drift):
        L,final = rng.uniform(220,650), rng.choice([-1,1])*rng.uniform(35,100)
        should=abs(final)>=62
        out.append(CaseSpec("gradual_lateral_drift_probe", [(0,0),(L,0)], [(0,0),(L*.4,0),(L,final)], should, off_start_s=(L*.55 if should else None), speed=rng.uniform(4,18), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,6), lateral_bias=0, heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(10,16), note="ambiguous gradual lateral divergence"))
    return out

def loop_roundabout_fork_noise_wrong(rng: random.Random) -> List[CaseSpec]:
    out=[]
    for _ in range(90):
        L,sep,v = rng.uniform(130,360), rng.uniform(12,55), rng.uniform(30,110)
        route=[(0,0),(L,0),(L,sep),(0,sep),(0,sep+v),(L,sep+v)]
        out.append(CaseSpec("close_parallel_loop_follow", route, route, False, speed=rng.uniform(3,15), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,9), lateral_bias=rng.uniform(-6,6), heading_noise=rng.uniform(2,12), note="close parallel/future legs"))
    for _ in range(70):
        L,sep=rng.uniform(130,360),rng.uniform(18,70)
        route=[(0,0),(L,0),(L,sep),(0,sep),(0,2*sep)]
        out.append(CaseSpec("loop_wrong_future_leg_off", route, [(0,0),(L,0),(0,sep)], True, off_start_s=L+10, speed=rng.uniform(4,16), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-4,4), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(7,12), note="wrong future close leg"))
    for _ in range(70):
        r,approach,exit_len = rng.uniform(18,45), rng.uniform(60,160), rng.uniform(70,180)
        sweep=rng.choice([90,135,180,225])
        circle=arc_points(0,0,r,180,180-sweep,max(8,int(sweep/12)))
        end=circle[-1]; a=math.radians(180-sweep); h=heading_from_vec(math.sin(a),-math.cos(a)); vx,vy=angle_to_vec(h)
        route=[(-approach,0),(-r,0)]+circle[1:]+[(end[0]+vx*exit_len,end[1]+vy*exit_len)]
        out.append(CaseSpec("roundabout_follow", route, route, False, speed=rng.uniform(3,13), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,12), note="correct roundabout"))
    for _ in range(80):
        r,approach=rng.uniform(20,48),rng.uniform(70,170)
        rs=rng.choice([135,180,225,270])
        ws=max(55,rs-rng.choice([45,90])) if rng.random()<.65 else min(315,rs+rng.choice([45,90]))
        def mk(sweep):
            circle=arc_points(0,0,r,180,180-sweep,max(8,int(sweep/12)))
            end=circle[-1]; a=math.radians(180-sweep); h=heading_from_vec(math.sin(a),-math.cos(a)); vx,vy=angle_to_vec(h)
            return [(-approach,0),(-r,0)]+circle[1:]+[(end[0]+vx*rng.uniform(85,200),end[1]+vy*rng.uniform(85,200))]
        route,actual=mk(rs),mk(ws); _,alen=polyline_lengths(actual)
        out.append(CaseSpec("roundabout_wrong_exit_off", route, actual, True, off_start_s=min(approach+r*math.radians(min(rs,ws)+10),alen*.75), speed=rng.uniform(3,14), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,12), max_delay_s=rng.uniform(7,13), note="wrong roundabout exit"))
    for _ in range(80):
        L=rng.uniform(180,520); bend=rng.uniform(-70,70); route=[(0,0),(L*.4,0),(L*.7,bend),(L,bend)]
        out.append(CaseSpec("gps_unusable_spikes_nooff", route, route, False, speed=rng.uniform(4,18), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,6), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,9), spike_count=rng.randint(1,4), spike_usable=False, spike_distance_min=80, spike_distance_max=260, note="far unusable spikes"))
    for _ in range(70):
        L=rng.uniform(180,620); route=[(0,0),(L,0)]
        out.append(CaseSpec("gps_usable_lowtrust_spike_nooff", route, route, False, speed=rng.uniform(4,19), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,5), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,9), spike_count=1, spike_usable=True, spike_distance_min=100, spike_distance_max=240, note="single usable low-trust spike"))
    for _ in range(80):
        L1,L2,d=rng.uniform(100,330),rng.uniform(70,280),rng.choice([-1,1])
        route=[(0,0),(L1,0),(L1,d*L2)]
        out.append(CaseSpec("bad_heading_onroute_nooff", route, route, False, speed=rng.uniform(2.5,13), sample_dt=rng.uniform(.8,2.5), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(5,20), bad_heading_rate=rng.uniform(.05,.28), note="heading sensor intermittently wrong"))
    for _ in range(100):
        L,back=rng.uniform(160,600),rng.uniform(80,240); route=[(0,0),(L,0)]
        if rng.random()<.5:
            actual=[(0,0),(-back,0)]; off_s=8; cat="wrongway_from_start_off"
        else:
            st=rng.uniform(50,min(L-30,180)); actual=[(st,0),(0,0),(-back,0)]; off_s=5; cat="reverse_along_route_off"
        out.append(CaseSpec(cat, route, actual, True, off_start_s=off_s, speed=rng.uniform(3,15), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,6), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(4,8), note="moving opposite route direction"))
    for _ in range(70):
        L,pre=rng.uniform(180,620),rng.uniform(25,95); route=[(0,0),(L,0)]
        out.append(CaseSpec("start_approach_extension_nooff", route, [(-pre,0),(0,0),(L,0)], False, speed=rng.uniform(3,16), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-6,6), heading_noise=rng.uniform(2,10), note="approaching start from extension"))
    for _ in range(70):
        L,off=rng.uniform(180,620),rng.choice([-1,1])*rng.uniform(60,160); route=[(0,0),(L,0)]
        out.append(CaseSpec("start_far_parallel_off", route, [(0,off),(L*.45,off)], True, off_start_s=5, speed=rng.uniform(3,16), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,6), lateral_bias=rng.uniform(-4,4), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(8,14), note="starts far on another parallel road"))
    for _ in range(70):
        L0,L1,ang=rng.uniform(80,220),rng.uniform(120,380),rng.choice([-1,1])*rng.uniform(15,45)
        vx,vy=math.cos(math.radians(ang)),math.sin(math.radians(ang)); route=[(0,0),(L0,0),(L0+vx*L1,vy*L1)]
        out.append(CaseSpec("fork_correct_branch_follow", route, route, False, speed=rng.uniform(4,18), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-6,6), heading_noise=rng.uniform(2,10), note="correct fork"))
    for _ in range(110):
        L0,L1=rng.uniform(70,220),rng.uniform(140,440); ar=rng.choice([-1,1])*rng.uniform(12,35); aw=-ar+rng.choice([-1,1])*rng.uniform(0,18)
        vxr,vyr=math.cos(math.radians(ar)),math.sin(math.radians(ar)); vxw,vyw=math.cos(math.radians(aw)),math.sin(math.radians(aw))
        out.append(CaseSpec("fork_wrong_branch_off", [(0,0),(L0,0),(L0+vxr*L1,vyr*L1)], [(0,0),(L0,0),(L0+vxw*L1,vyw*L1)], True, off_start_s=L0+rng.uniform(8,18), speed=rng.uniform(4,19), sample_dt=rng.uniform(.7,2), noise_std=rng.uniform(1,7), lateral_bias=rng.uniform(-5,5), heading_noise=rng.uniform(2,10), max_delay_s=rng.uniform(7,13), note="wrong fork branch"))
    return out

def build_specs(seed: int, scale: float = 1.0) -> List[CaseSpec]:
    rng=random.Random(seed)
    def n(x): return max(1, int(round(x*scale)))
    specs=[]
    specs += right_angle(rng, n(260), n(320), n(170), n(150))
    specs += uturn(rng, n(120), n(130), n(90))
    specs += shortcut(rng, n(110), n(100), n(90))
    specs += parallel(rng, n(150), n(150), n(100))
    # Keep these unscaled for broad coverage, then scale by sub-sampling if scale < 1.
    extra = loop_roundabout_fork_noise_wrong(rng)
    if scale != 1.0:
        rng.shuffle(extra)
        extra = extra[:max(1, int(round(len(extra)*scale)))]
    specs += extra
    rng.shuffle(specs)
    return specs

def generate_jsonl(out: str, seed: int = 20260420, scale: float = 1.0, limit: Optional[int] = None) -> Dict[str, Any]:
    rng=random.Random(seed+1337)
    specs=build_specs(seed, scale)
    if limit is not None:
        specs=specs[:limit]
    summary={"seed":seed,"scale":scale,"case_count":0,"offroute_expected_count":0,"nooff_expected_count":0,"categories":{}}
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out,"w",encoding="utf-8") as f:
        for i,sp in enumerate(specs):
            cid=f"case_{i:05d}_{sp.category}"
            rec=sample_case(rng,cid,sp,1774834662669+i*300000)
            meta=rec["meta"]; cat=meta["category"]
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",",":"))+"\n")
            summary["case_count"]+=1
            summary["offroute_expected_count"]+=int(meta["should_off_route"])
            summary["nooff_expected_count"]+=int(not meta["should_off_route"])
            summary["categories"].setdefault(cat,{"count":0,"off_expected":0})
            summary["categories"][cat]["count"]+=1
            summary["categories"][cat]["off_expected"]+=int(meta["should_off_route"])
    return summary

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out", default="/mnt/data/synthetic_nav_suite_v2.jsonl")
    ap.add_argument("--summary", default="/mnt/data/synthetic_nav_suite_v2_summary.json")
    ap.add_argument("--seed", type=int, default=20260420)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--limit", type=int)
    args=ap.parse_args()
    summary=generate_jsonl(args.out,args.seed,args.scale,args.limit)
    with open(args.summary,"w",encoding="utf-8") as f:
        json.dump(summary,f,ensure_ascii=False,indent=2)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
