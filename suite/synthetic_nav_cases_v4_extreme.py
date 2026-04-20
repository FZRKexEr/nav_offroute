#!/usr/bin/env python3
from __future__ import annotations
"""
Synthetic navigation cases v4: commercial-conservative semantics.

This generator intentionally separates spoken off-route labels from internal suspect labels.
Ambiguous shortcut/rejoin, early cross-over U-turn, correlated urban-canyon GPS bias, and
low-trust tunnel fixes are labeled no-off-route. Only sustained, hard-to-explain departures
are labeled off-route.
"""
import argparse, json, math, os, random, sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(__file__) or ".")
import synthetic_nav_cases_v2 as v2
import synthetic_nav_cases_v3_conservative as v3

Point = Tuple[float, float]


def route_len(path: Sequence[Point]) -> float:
    return v2.polyline_lengths(path)[1]


def path_final_offset(route: Sequence[Point], actual: Sequence[Point]) -> float:
    # crude minimum distance from last actual point to route
    x, y = actual[-1]
    best = 1e9
    for a, b in zip(route, route[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        L2 = max(1e-9, dx*dx+dy*dy)
        t = max(0.0, min(1.0, ((x-a[0])*dx+(y-a[1])*dy)/L2))
        px, py = a[0]+t*dx, a[1]+t*dy
        best = min(best, math.hypot(x-px, y-py))
    return best


def relabel_for_spoken_policy(sp: v2.CaseSpec, rng: random.Random) -> v2.CaseSpec:
    # A lot of v2/v3 stress labels are useful for internal reroute preparation, but not for
    # spoken off-route when we only have one planned polyline and no road graph.
    cat = sp.category
    if cat == "loop_wrong_future_leg_off":
        sp.category = "loop_skip_future_leg_ambiguous_nooff"
        sp.should_off = False
        sp.off_start_s = None
        sp.off_end_s = None
        sp.max_delay_s = 0
        sp.note += "; v4: skipping to a close future leg is ambiguous without topology; no spoken off-route"
    elif cat == "uturn_too_early_far_off" and rng.random() < 0.55:
        sp.category = "uturn_early_far_ambiguous_nooff"
        sp.should_off = False
        sp.off_start_s = None
        sp.off_end_s = None
        sp.max_delay_s = 0
        sp.note += "; v4: early U-turn/cross-over tolerated unless it clearly diverges"
    elif cat in ("right_angle_early_turn_parallel", "right_angle_late_turn_parallel") and sp.should_off:
        # Parallel late/early turns in the 20~60m band are often adjacent lanes/service roads.
        if rng.random() < 0.45:
            sp.category = cat + "_ambiguous_nooff"
            sp.should_off = False
            sp.off_start_s = None
            sp.off_end_s = None
            sp.max_delay_s = 0
            sp.note += "; v4: early/late parallel turn treated as ambiguous unless sustained far"
    elif cat == "start_far_parallel_off" and rng.random() < 0.60:
        sp.category = "start_far_parallel_ambiguous_nooff"
        sp.should_off = False
        sp.off_start_s = None
        sp.off_end_s = None
        sp.max_delay_s = 0
        sp.note += "; v4: start GPS can be far/parallel before route lock"
    return sp


def extreme_extra_specs(seed: int, scale: float) -> List[v2.CaseSpec]:
    rng = random.Random(seed + 7077)
    def n(x: int) -> int:
        return max(1, int(round(x * scale)))
    out: List[v2.CaseSpec] = []

    # Many variations of right-angle behavior: follow, missed, cut, parallel, stop-before-turn.
    for _ in range(n(900)):
        L1, L2, d = rng.uniform(50, 420), rng.uniform(50, 360), rng.choice([-1, 1])
        typ = rng.random()
        route = [(0,0),(L1,0),(L1,d*L2)]
        if typ < 0.22:
            # completely missed turn: high confidence off
            extra = rng.uniform(70, 360)
            out.append(v2.CaseSpec("right_angle_missed_turn", route, [(0,0),(L1+extra,0)], True,
                off_start_s=L1+rng.uniform(5,16), speed=rng.uniform(3,20), sample_dt=rng.uniform(.55,2.5),
                noise_std=rng.uniform(1,12), lateral_bias=rng.uniform(-10,10), heading_noise=rng.uniform(2,18),
                max_delay_s=rng.uniform(7,16), note="v4 missed right-angle turn; continues away from maneuver"))
        elif typ < 0.48:
            # correct but corner-cut/parking-lot cut: no spoken off
            cut = rng.uniform(4, min(55, L1*0.45, L2*0.45))
            actual = [(0,0),(L1-cut,0),(L1,d*cut),(L1,d*L2)]
            out.append(v2.CaseSpec("right_angle_corner_cut_allowed_nooff", route, actual, False,
                speed=rng.uniform(2.5,18), sample_dt=rng.uniform(.6,2.4), noise_std=rng.uniform(1.5,10),
                lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,16),
                note="cuts corner but rejoins; no spoken reroute"))
        elif typ < 0.68:
            # late turn to near parallel road. If near and same shape nooff; far off.
            late = rng.uniform(15, 110)
            should = late > rng.uniform(70, 95)
            actual = [(0,0),(L1+late,0),(L1+late,d*L2)]
            out.append(v2.CaseSpec("right_angle_late_parallel_far_off" if should else "right_angle_late_parallel_ambiguous_nooff",
                route, actual, should, off_start_s=(L1+18 if should else None),
                speed=rng.uniform(3.5,19), sample_dt=rng.uniform(.6,2.4), noise_std=rng.uniform(1,9),
                lateral_bias=rng.uniform(-7,7), heading_noise=rng.uniform(2,14), max_delay_s=rng.uniform(12,24),
                note="late turn; far sustained parallel is off, near parallel is ambiguous"))
        elif typ < 0.86:
            early = rng.uniform(12, 100)
            should = early > rng.uniform(72, 98)
            actual = [(0,0),(max(3,L1-early),0),(max(3,L1-early),d*L2)]
            out.append(v2.CaseSpec("right_angle_early_parallel_far_off" if should else "right_angle_early_parallel_ambiguous_nooff",
                route, actual, should, off_start_s=(max(3,L1-early)+18 if should else None),
                speed=rng.uniform(3.5,18), sample_dt=rng.uniform(.6,2.4), noise_std=rng.uniform(1,9),
                lateral_bias=rng.uniform(-7,7), heading_noise=rng.uniform(2,14), max_delay_s=rng.uniform(12,24),
                note="early turn; far sustained parallel is off, near parallel is ambiguous"))
        else:
            # stop before/at turn; heading often bad while stopped. nooff.
            out.append(v2.CaseSpec("stopped_at_turn_nooff", route, route, False,
                speed=rng.uniform(1.5,9), sample_dt=rng.uniform(.8,2.8), noise_std=rng.uniform(2,16),
                lateral_bias=rng.uniform(-12,12), heading_noise=rng.uniform(8,55), stop_at_s=L1+rng.uniform(-18,14),
                stop_duration_s=rng.uniform(3,35), bad_heading_rate=rng.uniform(.05,.35),
                note="stopped/creeping at turn; heading and GPS unstable"))

    # U-turn/cross-over families.
    for _ in range(n(760)):
        L, sep, d = rng.uniform(90, 560), rng.uniform(10, 90), rng.choice([-1,1])
        route = [(0,0),(L,0),(L,d*sep),(0,d*sep)]
        typ = rng.random()
        if typ < 0.38:
            e = rng.uniform(10, min(180, L*0.65))
            mid = rng.uniform(.25,.78)
            actual=[(0,0),(L-e,0),(L-e*mid,d*sep*rng.uniform(.35,.85)),(L-e,d*sep),(0,d*sep)]
            out.append(v2.CaseSpec("uturn_early_crossover_allowed_nooff", route, actual, False,
                speed=rng.uniform(2,16), sample_dt=rng.uniform(.6,2.6), noise_std=rng.uniform(1.5,12),
                lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,18),
                note="early U-turn/cross-over to intended opposite corridor; tolerated"))
        elif typ < 0.58:
            extra=rng.uniform(70,330)
            out.append(v2.CaseSpec("uturn_missed_continue_straight", route, [(0,0),(L+extra,0)], True,
                off_start_s=L+rng.uniform(7,18), speed=rng.uniform(3,20), sample_dt=rng.uniform(.6,2.4),
                noise_std=rng.uniform(1,10), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,16),
                max_delay_s=rng.uniform(7,15), note="requires U-turn but continues straight"))
        elif typ < 0.76:
            e = rng.uniform(min(140,L*.32), min(300,L*.78))
            far = sep > 55 or e > 190
            actual=[(0,0),(L-e,0),(L-e,d*sep),(max(0,L-e-rng.uniform(80,260)),d*sep)]
            out.append(v2.CaseSpec("uturn_too_early_far_off" if far else "uturn_early_far_ambiguous_nooff", route, actual, far,
                off_start_s=(L-e+rng.uniform(18,35) if far else None), speed=rng.uniform(3,18), sample_dt=rng.uniform(.7,2.5),
                noise_std=rng.uniform(1,10), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,16), max_delay_s=rng.uniform(14,28),
                note="far early U-turn only off when separation/distance makes rejoin unlikely"))
        else:
            out.append(v2.CaseSpec("uturn_follow", route, route, False,
                speed=rng.uniform(2,14), sample_dt=rng.uniform(.7,2.6), noise_std=rng.uniform(1.5,12),
                lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,18), stop_at_s=(L+rng.uniform(-14,14) if rng.random()<.22 else None),
                stop_duration_s=rng.uniform(2,18), note="correct U-turn / hairpin"))

    # Shortcut/rejoin vs wrong corridor.
    for _ in range(n(840)):
        A, B, C = rng.uniform(100,620), rng.uniform(45,260), rng.uniform(20,220)
        d = rng.choice([-1,1])
        typ = rng.random()
        if typ < 0.46:
            route = [(0,0),(0,d*B),(A,d*B),(A+C,0)]
            actual = [(0,0),(A+C,0)]
            out.append(v2.CaseSpec("shortcut_rejoin_allowed_nooff", route, actual, False,
                speed=rng.uniform(2.5,20), sample_dt=rng.uniform(.55,2.5), noise_std=rng.uniform(1,12),
                lateral_bias=rng.uniform(-10,10), heading_noise=rng.uniform(2,16),
                note="block/dogleg shortcut that rejoins planned corridor; no spoken off-route"))
        elif typ < 0.68:
            route = [(0,0),(A,0),(A,d*B),(A+C,d*B)]
            actual = [(0,0),(A+C,d*B)]
            out.append(v2.CaseSpec("shortcut_diagonal_allowed_nooff", route, actual, False,
                speed=rng.uniform(2.5,20), sample_dt=rng.uniform(.55,2.5), noise_std=rng.uniform(1,12),
                lateral_bias=rng.uniform(-10,10), heading_noise=rng.uniform(2,16),
                note="diagonal cut-through across dogleg; may be legal local road"))
        else:
            route = [(0,0),(A,0),(A,d*B),(A+C,d*B)]
            actual = [(0,0),(A*rng.uniform(.38,.72),0),(A*rng.uniform(.65,.95),-d*rng.uniform(70,190)),(A+C,-d*rng.uniform(90,260))]
            out.append(v2.CaseSpec("shortcut_wrong_corridor_off", route, actual, True,
                off_start_s=A*rng.uniform(.45,.75), speed=rng.uniform(3,20), sample_dt=rng.uniform(.6,2.4),
                noise_std=rng.uniform(1,10), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,14), max_delay_s=rng.uniform(14,28),
                note="leaves into opposite/wrong corridor with no near-term rejoin"))

    # Correlated GPS bias families.
    for _ in range(n(900)):
        L1, L2, L3, d = rng.uniform(80,480), rng.uniform(70,380), rng.uniform(50,340), rng.choice([-1,1])
        typ = rng.random()
        if typ < .34:
            route=[(0,0),(L1,0),(L1,d*L2),(L1+L3,d*L2)]
        elif typ < .67:
            route=[(0,0),(L1*.45,rng.uniform(-20,20)),(L1,d*L2*.25),(L1+L3,d*L2*.38)]
        else:
            route=[(0,0),(L1,0),(L1+L2*.45,d*L2*.20),(L1+L2*.85,d*L2*.15),(L1+L3,d*L2*.15)]
        out.append(v2.CaseSpec("building_canyon_vector_bias_nooff", route, route, False,
            speed=rng.uniform(1.5,20), sample_dt=rng.uniform(.6,2.8), noise_std=rng.uniform(1,8),
            lateral_bias=rng.uniform(-4,4), heading_noise=rng.uniform(2,24), trusted_level_base=rng.choice([1,1,1,2]),
            bad_heading_rate=(rng.uniform(.02,.16) if rng.random()<.2 else 0),
            note="true path follows route but measured GPS is coherently shifted"))

    for _ in range(n(540)):
        L=rng.uniform(240,1000); wig=rng.uniform(10,60)
        route=[(0,0),(L*.22,rng.uniform(-wig,wig)),(L*.48,rng.uniform(-wig,wig)),(L*.73,rng.uniform(-wig,wig)),(L,rng.uniform(-wig,wig))]
        out.append(v2.CaseSpec("temporary_bias_episode_nooff", route, route, False,
            speed=rng.uniform(2,20), sample_dt=rng.uniform(.7,3.0), noise_std=rng.uniform(1,8),
            lateral_bias=rng.uniform(-6,6), heading_noise=rng.uniform(2,26), trusted_level_base=rng.choice([1,1,2,3]),
            bad_heading_rate=(rng.uniform(.04,.25) if rng.random()<.35 else 0),
            note="temporary multipath/tunnel bias; should not trigger spoken off-route"))

    # Parallel ambiguity and sustained branch.
    for _ in range(n(620)):
        L=rng.uniform(180,1200); sep=rng.choice([-1,1])*rng.uniform(18,92); wig=rng.uniform(0,45)
        route=[(0,0),(L*.35,rng.uniform(-wig,wig)),(L*.7,rng.uniform(-wig,wig)),(L,rng.uniform(-wig,wig))]
        actual=[(x,y+sep) for x,y in route]
        out.append(v2.CaseSpec("parallel_same_shape_ambiguous_nooff", route, actual, False,
            speed=rng.uniform(2,22), sample_dt=rng.uniform(.6,2.8), noise_std=rng.uniform(1,12),
            lateral_bias=rng.uniform(-7,7), heading_noise=rng.uniform(2,22),
            note="same-shape parallel trace; could be GPS bias/service road"))
    for _ in range(n(360)):
        L=rng.uniform(300,1400); dep=rng.uniform(40,240); ramp=rng.uniform(45,180); sep=rng.choice([-1,1])*rng.uniform(110,320)
        route=[(0,0),(L,0)]
        actual=[(0,0),(dep,0),(dep+ramp,sep),(L,sep+rng.uniform(-45,45))]
        out.append(v2.CaseSpec("parallel_branch_sustained_far_off", route, actual, True,
            off_start_s=dep+ramp*.58, speed=rng.uniform(3,22), sample_dt=rng.uniform(.6,2.6),
            noise_std=rng.uniform(1,12), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,18), max_delay_s=rng.uniform(16,34),
            note="sustained far branch after fork; likely wrong corridor"))

    # Loops/close future legs. Most close future-leg skips are ambiguous; wrong exits that diverge are off.
    for _ in range(n(420)):
        H=rng.uniform(120,420); W=rng.uniform(35,120); tail=rng.uniform(40,220); d=rng.choice([-1,1])
        route=[(0,0),(0,H),(d*W,H),(d*W,0),(d*(W+tail),0)]
        typ=rng.random()
        if typ < .42:
            actual=route
            out.append(v2.CaseSpec("close_parallel_loop_follow", route, actual, False,
                speed=rng.uniform(2,17), sample_dt=rng.uniform(.7,2.8), noise_std=rng.uniform(1,12), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,22), note="follows close U/loop route"))
        elif typ < .78:
            actual=[(0,0),(0,H*.92),(d*W,H*.4),(d*W,0),(d*(W+tail),0)]
            out.append(v2.CaseSpec("loop_skip_future_leg_ambiguous_nooff", route, actual, False,
                speed=rng.uniform(2,17), sample_dt=rng.uniform(.7,2.8), noise_std=rng.uniform(1,12), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,22), note="skips loop to close future leg; no road topology so no spoken off"))
        else:
            actual=[(0,0),(0,H*.75),(-d*rng.uniform(70,180),H*.35),(-d*rng.uniform(120,260),-rng.uniform(30,160))]
            out.append(v2.CaseSpec("loop_wrong_corridor_off", route, actual, True,
                off_start_s=H*.65, speed=rng.uniform(3,18), sample_dt=rng.uniform(.7,2.6), noise_std=rng.uniform(1,10), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,18), max_delay_s=rng.uniform(16,30), note="leaves loop into opposite/wrong corridor"))

    # Low speed, poor GNSS, tunnels.
    for _ in range(n(520)):
        L1,L2,d=rng.uniform(120,650),rng.uniform(80,340),rng.choice([-1,1])
        route=[(0,0),(L1,0),(L1,d*L2)]
        out.append(v2.CaseSpec("poor_gnss_large_error_nooff", route, route, False,
            speed=rng.uniform(.5,16), sample_dt=rng.uniform(.8,4.0), noise_std=rng.uniform(10,42),
            lateral_bias=rng.choice([-1,1])*rng.uniform(20,140), heading_noise=rng.uniform(12,90),
            trusted_level_base=rng.choice([2,3,4]), bad_heading_rate=rng.uniform(.08,.45),
            stop_at_s=(rng.uniform(20, route_len(route)-20) if rng.random()<.22 else None), stop_duration_s=rng.uniform(3,45),
            note="poor GNSS / tunnel / urban canyon large error; no spoken off without strong independent evidence"))

    # Start, wrong-way, and destination edge cases.
    for _ in range(n(360)):
        L=rng.uniform(160,700); route=[(0,0),(L,0)]
        typ=rng.random()
        if typ<.35:
            actual=[(-rng.uniform(20,150),rng.uniform(-25,25)),(0,0),(L,0)]
            out.append(v2.CaseSpec("start_approach_extension_nooff", route, actual, False,
                speed=rng.uniform(1,16), sample_dt=rng.uniform(.7,2.8), noise_std=rng.uniform(2,18), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,30), note="approaches route start from extension"))
        elif typ<.62:
            actual=[(0,0),(-rng.uniform(60,260),rng.uniform(-20,20))]
            out.append(v2.CaseSpec("wrongway_from_start_off", route, actual, True,
                off_start_s=rng.uniform(6,18), speed=rng.uniform(2,16), sample_dt=rng.uniform(.7,2.8), noise_std=rng.uniform(1,12), lateral_bias=rng.uniform(-6,6), heading_noise=rng.uniform(2,20), max_delay_s=rng.uniform(8,18), note="moves away from route start in wrong direction"))
        else:
            sep=rng.choice([-1,1])*rng.uniform(35,130)
            actual=[(0,sep),(L,sep)]
            out.append(v2.CaseSpec("start_far_parallel_ambiguous_nooff", route, actual, False,
                speed=rng.uniform(1,16), sample_dt=rng.uniform(.7,2.8), noise_std=rng.uniform(2,18), lateral_bias=rng.uniform(-8,8), heading_noise=rng.uniform(2,30), note="start lock ambiguous on parallel/service road"))

    return out


def mutate_record_by_category(rec: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    cat = rec['meta']['category']
    if cat in ('building_canyon_vector_bias_nooff','parallel_same_shape_ambiguous_nooff'):
        return v3.add_global_bias_to_gps(rec, rng, rng.uniform(18, 125), rng.uniform(15, 130), rng.uniform(1.0, 7.0))
    if cat in ('temporary_bias_episode_nooff','poor_gnss_large_error_nooff') and rng.random() < .78:
        return v3.add_bias_episode(rec, rng, rng.uniform(35, 170), rng.uniform(.08, .36), rng.uniform(.55, .96), rng.uniform(2.0, 10.0))
    return rec


def generate_jsonl(out: str, seed: int = 20260421, scale: float = 1.0, limit: Optional[int] = None) -> Dict[str, Any]:
    rng = random.Random(seed + 9131)
    specs = []
    base = v3.base_commercial_specs(seed, scale * 1.05) + v3.conservative_extra_specs(seed, scale * 1.05)
    specs.extend(relabel_for_spoken_policy(sp, rng) for sp in base)
    specs.extend(extreme_extra_specs(seed, scale))
    rng.shuffle(specs)
    if limit is not None:
        specs = specs[:limit]
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    summary: Dict[str, Any] = {"seed": seed, "scale": scale, "case_count": 0, "offroute_expected_count": 0, "nooff_expected_count": 0, "categories": {}}
    with open(out, 'w', encoding='utf-8') as f:
        for i, sp in enumerate(specs):
            cid = f"case_{i:05d}_{sp.category}"
            rec = v2.sample_case(rng, cid, sp, 1775034662669 + i * 260000)
            rec = mutate_record_by_category(rec, rng)
            meta = rec['meta']; cat = meta['category']
            f.write(json.dumps(rec, ensure_ascii=False, separators=(',', ':')) + '\n')
            summary['case_count'] += 1
            summary['offroute_expected_count'] += int(meta['should_off_route'])
            summary['nooff_expected_count'] += int(not meta['should_off_route'])
            summary['categories'].setdefault(cat, {'count':0, 'off_expected':0})
            summary['categories'][cat]['count'] += 1
            summary['categories'][cat]['off_expected'] += int(meta['should_off_route'])
    return summary


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--out', default='/mnt/data/synthetic_nav_suite_v4_extreme.jsonl')
    ap.add_argument('--summary', default='/mnt/data/synthetic_nav_suite_v4_extreme_summary.json')
    ap.add_argument('--seed', type=int, default=20260421)
    ap.add_argument('--scale', type=float, default=1.0)
    ap.add_argument('--limit', type=int, default=6000)
    args=ap.parse_args()
    summary=generate_jsonl(args.out, args.seed, args.scale, args.limit)
    with open(args.summary,'w',encoding='utf-8') as f: json.dump(summary,f,ensure_ascii=False,indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__=='__main__': main()
