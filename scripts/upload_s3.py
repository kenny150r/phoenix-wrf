#!/usr/bin/env python3
"""Upload PNG frames to s3://phx-wrf-forecast and write latest.json.

Layout:
  s3://phx-wrf-forecast/runs/YYYYMMDDTHHz/{refl,precip,t2,wind,cape,meteogram}/fXX.png
  s3://phx-wrf-forecast/latest.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# This desktop is not EC2; IMDS probes stall boto3/aws for ~20s otherwise.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import boto3

BUCKET = "phx-wrf-forecast"
PRODUCTS = ["refl", "precip", "t2", "wind", "cape", "meteogram"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True, help="Local plots/<cycle> directory")
    p.add_argument("--cycle", required=True)
    p.add_argument("--hours", type=int, default=18)
    p.add_argument("--status", default="success")
    p.add_argument("--bucket", default=BUCKET)
    args = p.parse_args()

    run_dir = Path(args.run_dir)
    s3 = boto3.client("s3", region_name="us-east-1")
    uploaded = 0
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "latest.json":
            continue
        if path.suffix.lower() not in {".png", ".json"}:
            continue
        key = f"runs/{args.cycle}/{path.relative_to(run_dir).as_posix()}"
        ctype = "image/png" if path.suffix.lower() == ".png" else "application/json"
        extra = {"ContentType": ctype, "CacheControl": "public, max-age=300"}
        s3.upload_file(str(path), args.bucket, key, ExtraArgs=extra)
        uploaded += 1
        print(f"put s3://{args.bucket}/{key}")

    meta = {}
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            meta = {}

    # Leaflet L.imageOverlay bounds: [[south, west], [north, east]]
    default_bounds = [[32.10253, -113.68496], [34.79747, -110.45504]]
    bounds = meta.get("bounds") or default_bounds

    latest = {
        "cycle": args.cycle,
        "status": args.status,
        "hours": args.hours,
        "products": PRODUCTS,
        "bucket": args.bucket,
        "base_url": f"https://{args.bucket}.s3.amazonaws.com/runs/{args.cycle}",
        "meteogram_url": f"https://{args.bucket}.s3.amazonaws.com/runs/{args.cycle}/meteogram/f00.png",
        "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frames": args.hours + 1,
        "bounds": bounds,
        "center": meta.get("center") or [33.45, -112.07],
        "ref_lat": meta.get("ref_lat", 33.45),
        "ref_lon": meta.get("ref_lon", -112.07),
        "domain_km": meta.get("domain_km", 300),
    }
    latest_path = run_dir / "latest.json"
    latest_path.write_text(json.dumps(latest, indent=2) + "\n")
    for key, cache in (
        ("latest.json", "public, max-age=60"),
        (f"runs/{args.cycle}/latest.json", "public, max-age=300"),
    ):
        s3.upload_file(
            str(latest_path),
            args.bucket,
            key,
            ExtraArgs={"ContentType": "application/json", "CacheControl": cache},
        )
    print(f"Uploaded {uploaded} objects; latest.json written")


if __name__ == "__main__":
    main()
