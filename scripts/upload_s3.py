#!/usr/bin/env python3
"""Upload PNG frames to s3://phx-wrf-forecast and write latest.json."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import boto3

BUCKET = "phx-wrf-forecast"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True, help="Local plots/<cycle> directory")
    p.add_argument("--cycle", required=True)
    p.add_argument("--hours", type=int, default=18)
    p.add_argument("--status", default="success")
    args = p.parse_args()

    run_dir = Path(args.run_dir)
    s3 = boto3.client("s3", region_name="us-east-1")
    uploaded = 0
    for path in run_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".png", ".json"}:
            key = f"runs/{args.cycle}/{path.relative_to(run_dir).as_posix()}"
            ctype = "image/png" if path.suffix.lower() == ".png" else "application/json"
            extra = {"ContentType": ctype, "CacheControl": "public, max-age=300"}
            s3.upload_file(str(path), BUCKET, key, ExtraArgs=extra)
            uploaded += 1
            print(f"put s3://{BUCKET}/{key}")

    latest = {
        "cycle": args.cycle,
        "status": args.status,
        "hours": args.hours,
        "products": ["refl", "precip", "t2", "wind", "cape", "meteogram"],
        "bucket": BUCKET,
        "base_url": f"https://{BUCKET}.s3.amazonaws.com/runs/{args.cycle}",
        "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frames": args.hours + 1,
    }
    latest_path = run_dir / "latest.json"
    latest_path.write_text(json.dumps(latest, indent=2))
    s3.upload_file(
        str(latest_path),
        BUCKET,
        "latest.json",
        ExtraArgs={"ContentType": "application/json", "CacheControl": "public, max-age=60"},
    )
    s3.upload_file(
        str(latest_path),
        BUCKET,
        f"runs/{args.cycle}/latest.json",
        ExtraArgs={"ContentType": "application/json", "CacheControl": "public, max-age=300"},
    )
    print(f"Uploaded {uploaded} objects; latest.json written")


if __name__ == "__main__":
    main()
