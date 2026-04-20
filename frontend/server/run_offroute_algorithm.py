#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def load_module(module_path: str):
    spec = importlib.util.spec_from_file_location("selected_offroute_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载算法脚本: {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def build_result_key(gps_id: Any, timestamp_ms: Any, lon: float, lat: float) -> str:
    safe_id = "" if gps_id is None else str(gps_id)
    safe_ts = 0 if timestamp_ms is None else int(timestamp_ms)
    return f"{safe_id}|{safe_ts}|{float(lon):.8f}|{float(lat):.8f}"


def xy_to_lonlat(route: Any, x: float, y: float, earth_r: float) -> list[float]:
    lat = float(route.lat0) + math.degrees(float(y) / earth_r)
    cos_lat0 = math.cos(math.radians(float(route.lat0)))
    if abs(cos_lat0) < 1e-12:
        lon = float(route.lon0)
    else:
        lon = float(route.lon0) + math.degrees(float(x) / (earth_r * cos_lat0))
    return [lon, lat]


def serialize_projection(projection: Any, route: Any, earth_r: float) -> Optional[Dict[str, Any]]:
    if projection is None:
        return None

    return sanitize_json(
        {
            "segmentIdx": getattr(projection, "segment_idx", None),
            "s": getattr(projection, "s", None),
            "dist": getattr(projection, "dist", None),
            "signedDist": getattr(projection, "signed_dist", None),
            "heading": getattr(projection, "heading", None),
            "t": getattr(projection, "t", None),
            "tRaw": getattr(projection, "t_raw", None),
            "endpointGap": getattr(projection, "endpoint_gap", None),
            "score": getattr(projection, "score", None),
            "globalNearest": getattr(projection, "global_nearest", None),
            "gpsPoint": xy_to_lonlat(route, getattr(projection, "x", 0.0), getattr(projection, "y", 0.0), earth_r),
            "point": xy_to_lonlat(route, getattr(projection, "px", 0.0), getattr(projection, "py", 0.0), earth_r),
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--algorithm", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--gps", required=True)
    args = parser.parse_args()

    module = load_module(args.algorithm)
    required_symbols = ["load_route_geojson", "load_gps_geojson", "OffRouteDetector"]
    missing = [name for name in required_symbols if not hasattr(module, name)]
    if missing:
        raise RuntimeError(f"算法脚本缺少必要符号: {', '.join(missing)}")

    route = module.load_route_geojson(args.route)
    gps_points = module.load_gps_geojson(args.gps)
    detector = module.OffRouteDetector(route)
    earth_r = float(getattr(module, "EARTH_R", 6371000.0))

    outputs = []
    first_off_index = None
    first_suspect_index = None

    for index, point in enumerate(gps_points):
        output = detector.update(point)
        raw = point.raw or {}
        gps_id = raw.get("id")
        timestamp_ms = raw.get("timestamp", getattr(point, "timestamp_ms", 0))
        result_key = build_result_key(gps_id, timestamp_ms, point.lon, point.lat)

        if output.off_route and first_off_index is None:
            first_off_index = index
        if output.state == "SUSPECT" and first_suspect_index is None:
            first_suspect_index = index

        outputs.append(
            sanitize_json(
                {
                    "index": index,
                    "resultKey": result_key,
                    "gpsId": gps_id,
                    "timestampMs": getattr(point, "timestamp_ms", 0),
                    "gpsPoint": [float(point.lon), float(point.lat)],
                    "offRoute": bool(output.off_route),
                    "state": getattr(output, "state", ""),
                    "reason": getattr(output, "reason", ""),
                    "projection": serialize_projection(getattr(output, "projection", None), route, earth_r),
                    "metrics": getattr(output, "metrics", {}),
                }
            )
        )

    payload = sanitize_json(
        {
            "algorithmName": Path(args.algorithm).name,
            "summary": {
                "pointCount": len(outputs),
                "routeLengthM": getattr(route, "length", None),
                "firstOffIndex": first_off_index,
                "firstSuspectIndex": first_suspect_index,
            },
            "outputs": outputs,
        }
    )

    json.dump(payload, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
