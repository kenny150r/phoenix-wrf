#!/usr/bin/env python3
"""Upload PNG frames to s3://phx-wrf-forecast and write latest.json.

Layout:
  s3://phx-wrf-forecast/runs/YYYYMMDDTHHz/{refl,precip,t2,wind,cape,meteogram}/fXX.png
  s3://phx-wrf-forecast/latest.json
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import boto3

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from publish_status import BUCKET, PRODUCTS, build_latest, upload_latest

PNG_HOUR = re.compile(r"^f(\d+)\.png$", re.I)


def parse_hours_arg(text: str | None) -> list[int] | None:
    if text is None:
        return None
    text = text.strip()
    if not text:
        return []
    return sorted({int(p.strip()) for p in text.split(",") if p.strip()})


def should_upload(rel: Path, only_hours: list[int] | None) -> bool:
    if only_hours is None:
        return True
    if rel.name == "meta.json":
        return True
    if rel.parts and rel.parts[0] == "meteogram":
        return True
    m = PNG_HOUR.match(rel.name)
    if m and int(m.group(1)) in only_hours:
        return True
    return False


def upload_run(
    run_dir: Path,
    cycle: str,
    hours: int,
    *,
    only_hours: list[int] | None = None,
    status: str = "complete",
    stage: str | None = None,
    stage_label: str | None = None,
    bucket: str = BUCKET,
) -> int:
    run_dir = Path(run_dir)
    s3 = boto3.client("s3", region_name="us-east-1")
    uploaded = 0
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "latest.json":
            continue
        if path.suffix.lower() not in {".png", ".json"}:
            continue
        rel = path.relative_to(run_dir)
        if not should_upload(rel, only_hours):
            continue
        key = f"runs/{cycle}/{rel.as_posix()}"
        ctype = "image/png" if path.suffix.lower() == ".png" else "application/json"
        cache = "public, max-age=60" if rel.parts and rel.parts[0] == "meteogram" else "public, max-age=300"
        s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": ctype, "CacheControl": cache})
        uploaded += 1
        print(f"put s3://{bucket}/{key}")

    if stage is None:
        stage = "complete" if status in {"complete", "success", "placeholder"} else status
    if stage_label is None:
        stage_label = {
            "complete": f"Complete · {cycle}",
            "placeholder": f"Placeholder overlays · {cycle}",
            "failed": f"Failed · {cycle}",
            "running": f"Running · {cycle}",
        }.get(status if status != "success" else "complete", stage)

    latest = build_latest(
        cycle=cycle,
        status=status,
        stage=stage,
        stage_label=stage_label,
        hours=hours,
        run_dir=run_dir,
        hours_available=only_hours if only_hours is not None and status == "running" else None,
        bucket=bucket,
    )
    # When uploading a subset while running, merge with PNGs already on disk.
    if status == "running":
        from publish_status import scan_hours_available

        latest["hours_available"] = scan_hours_available(run_dir)
        latest["wrf_hour_done"] = max(latest["hours_available"]) if latest["hours_available"] else 0
    upload_latest(latest, bucket=bucket, run_dir=run_dir)
    print(f"Uploaded {uploaded} objects; latest.json written")
    return uploaded


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True, help="Local plots/<cycle> directory")
    p.add_argument("--cycle", required=True)
    p.add_argument("--hours", type=int, default=18)
    p.add_argument("--status", default="complete")
    p.add_argument("--stage", default=None)
    p.add_argument("--stage-label", default=None)
    p.add_argument("--only-hours", default=None, help="Comma-separated fXX hours to upload")
    p.add_argument("--bucket", default=BUCKET)
    args = p.parse_args()
    upload_run(
        Path(args.run_dir),
        args.cycle,
        args.hours,
        only_hours=parse_hours_arg(args.only_hours),
        status=args.status,
        stage=args.stage,
        stage_label=args.stage_label,
        bucket=args.bucket,
    )


if __name__ == "__main__":
    main()
