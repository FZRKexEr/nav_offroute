#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys


def load_module(path: str):
    spec = importlib.util.spec_from_file_location("algmod", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["algmod"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_route_direct(mod, gj):
    feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
    feats = sorted(feats, key=lambda f: f.get("properties", {}).get("id", 0))
    coords = []
    for ft in feats:
        if ft["geometry"]["type"] != "LineString":
            continue
        segment = [tuple(map(float, c[:2])) for c in ft["geometry"]["coordinates"]]
        if (
            coords
            and segment
            and abs(segment[0][0] - coords[-1][0]) < 1e-10
            and abs(segment[0][1] - coords[-1][1]) < 1e-10
        ):
            segment = segment[1:]
        coords.extend(segment)
    return mod.RouteIndex(coords)


def load_gps_direct(mod, gj):
    feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
    feats = sorted(
        feats,
        key=lambda f: (
            f.get("properties", {}).get("timestamp", 0),
            f.get("properties", {}).get("id", 0),
        ),
    )
    points = []
    seen = set()
    for ft in feats:
        if ft["geometry"]["type"] != "Point":
            continue
        coords = ft["geometry"]["coordinates"]
        props = ft.get("properties", {})
        key = (
            props.get("timestamp", 0),
            round(float(coords[0]), 8),
            round(float(coords[1]), 8),
        )
        if key in seen:
            continue
        seen.add(key)
        points.append(
            mod.GpsPoint(
                float(coords[0]),
                float(coords[1]),
                int(props.get("timestamp", 0) or 0),
                float(props.get("speed", 0.0) or 0.0),
                float(props.get("heading", 0.0) or 0.0),
                bool(props.get("usable", True)),
                int(props.get("trustedLevel", 1) or 1),
                props,
            )
        )
    return points


def eval_record(mod, rec):
    route = load_route_direct(mod, rec["route_geojson"])
    gps = load_gps_direct(mod, rec["gps_geojson"])
    det = mod.OffRouteDetector(route)
    first_off = None
    first_sus = None
    max_score = 0.0
    last_state = ""
    last_reason = ""

    for idx, point in enumerate(gps):
        out = det.update(point)
        last_state = out.state
        last_reason = out.reason
        max_score = max(max_score, float(out.metrics.get("score", 0.0)))
        if first_sus is None and out.state in ("SUSPECT", "OFF_ROUTE"):
            first_sus = idx
        if first_off is None and out.off_route:
            first_off = idx

    meta = rec["meta"]
    should = bool(meta.get("should_off_route"))
    true_idx = meta.get("true_off_idx")
    latest = meta.get("latest_detect_idx")
    if should:
        if first_off is None:
            verdict = "MISS"
        elif true_idx is not None and first_off < true_idx:
            verdict = "EARLY"
        elif latest is not None and first_off > latest:
            verdict = "LATE"
        else:
            verdict = "PASS"
    else:
        verdict = "FP" if first_off is not None else "PASS"

    return {
        "case_id": meta.get("case_id"),
        "category": meta.get("category"),
        "should_off_route": should,
        "true_off_idx": true_idx,
        "latest_detect_idx": latest,
        "first_suspect_idx": first_sus,
        "first_off_idx": first_off,
        "verdict": verdict,
        "point_count": len(gps),
        "max_score": round(max_score, 3),
        "last_state": last_state,
        "last_reason": last_reason,
        "noise_std_m": meta.get("noise_std_m"),
        "lateral_bias_m": meta.get("lateral_bias_m"),
        "global_bias_m": meta.get("global_bias_m"),
        "note": meta.get("note", "")[:160],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True)
    ap.add_argument("--algorithm", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    mod = load_module(args.algorithm)
    rows = []
    with open(args.suite, encoding="utf-8") as f:
        for line in f:
            rows.append(eval_record(mod, json.loads(line)))

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        fields = list(rows[0].keys()) if rows else ["case_id"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(
        json.dumps(
            {"rows": len(rows), "pass": sum(r["verdict"] == "PASS" for r in rows)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
