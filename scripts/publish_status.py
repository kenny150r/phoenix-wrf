#!/usr/bin/env python3
"""Write latest.json (run status + available frames) and upload it to S3.

GitHub Pages is static; the map polls this object. Safe to call from the
forecast driver at every stage (stdlib + boto3 only — no wrf-python).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

BUCKET = "phx-wrf-forecast"
PRODUCTS = ["refl", "precip", "t2", "wind", "cape", "meteogram"]
DEFAULT_BOUNDS = [[32.55050, -113.15377], [34.34493, -110.98623]]
DEFAULT_CENTER = [33.45, -112.07]

STATUS_VALUES = ("running", "placeholder", "complete", "failed")
STAGE_VALUES = ("download", "wps", "real", "wrf", "plot", "upload", "complete", "failed")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scan_hours_available(run_dir: Path | None) -> list[int]:
    if run_dir is None:
        return []
    refl = run_dir / "refl"
    hours: list[int] = []
    if refl.is_dir():
        for p in refl.glob("f*.png"):
            m = re.match(r"f(\d+)$", p.stem)
            if m:
                hours.append(int(m.group(1)))
    return sorted(set(hours))


def load_meta(run_dir: Path | None) -> dict:
    if run_dir is None:
        return {}
    path = run_dir / "meta.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def parse_hours_csv(text: str | None) -> list[int] | None:
    if text is None:
        return None
    text = text.strip()
    if text == "":
        return []
    hours = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        hours.append(int(part))
    return sorted(set(hours))


def normalize_status(status: str) -> str:
    if status == "success":
        return "complete"
    if status not in STATUS_VALUES:
        raise SystemExit(f"invalid status {status!r}; expected {STATUS_VALUES}")
    return status


def build_latest(
    *,
    cycle: str,
    status: str,
    stage: str,
    stage_label: str,
    hours: int,
    run_dir: Path | None = None,
    hours_available: list[int] | None = None,
    wrf_hour_done: int | None = None,
    bucket: str = BUCKET,
    extra: dict | None = None,
) -> dict:
    status = normalize_status(status)
    if stage not in STAGE_VALUES:
        raise SystemExit(f"invalid stage {stage!r}; expected {STAGE_VALUES}")

    meta = load_meta(run_dir)
    if hours_available is None:
        hours_available = scan_hours_available(run_dir)
        if not hours_available and status in {"placeholder", "complete"}:
            hours_available = list(range(hours + 1))

    if wrf_hour_done is None:
        wrf_hour_done = max(hours_available) if hours_available else 0

    bounds = meta.get("bounds") or DEFAULT_BOUNDS
    center = meta.get("center") or DEFAULT_CENTER
    latest = {
        "cycle": cycle,
        "status": status,
        "stage": stage,
        "stage_label": stage_label,
        "hours": hours,
        "products": PRODUCTS,
        "bucket": bucket,
        "base_url": f"https://{bucket}.s3.amazonaws.com/runs/{cycle}",
        "meteogram_url": f"https://{bucket}.s3.amazonaws.com/runs/{cycle}/meteogram/f00.png",
        "frames": hours + 1,
        "bounds": bounds,
        "center": center,
        "ref_lat": meta.get("ref_lat", 33.45),
        "ref_lon": meta.get("ref_lon", -112.07),
        "domain_km": meta.get("domain_km", 200),
        "wrf_hour_done": int(wrf_hour_done),
        "hours_available": hours_available,
        "updated_at": utc_now(),
    }
    if status == "complete":
        latest["completed_at"] = latest["updated_at"]
    elif status == "placeholder":
        latest["completed_at"] = latest["updated_at"]
    if extra:
        latest.update(extra)
    return latest


def s3_client(region: str = "us-east-1"):
    import boto3

    return boto3.client("s3", region_name=region)


def upload_latest(latest: dict, *, bucket: str = BUCKET, run_dir: Path | None = None) -> Path | None:
    body = json.dumps(latest, indent=2) + "\n"
    local = None
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        local = run_dir / "latest.json"
        local.write_text(body)
    s3 = s3_client()
    extra = {"ContentType": "application/json", "CacheControl": "public, max-age=10, must-revalidate"}
    s3.put_object(Bucket=bucket, Key="latest.json", Body=body.encode("utf-8"), **extra)
    cycle = latest.get("cycle")
    if cycle:
        s3.put_object(
            Bucket=bucket,
            Key=f"runs/{cycle}/latest.json",
            Body=body.encode("utf-8"),
            ContentType="application/json",
            CacheControl="public, max-age=60",
        )
    print(f"published latest.json status={latest.get('status')} stage={latest.get('stage')} "
          f"hours_available={latest.get('hours_available')} updated_at={latest.get('updated_at')}")
    return local


def publish(**kwargs) -> dict:
    latest = build_latest(**kwargs)
    upload_latest(latest, bucket=kwargs.get("bucket", BUCKET), run_dir=kwargs.get("run_dir"))
    return latest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Upload forecast status to s3://phx-wrf-forecast/latest.json")
    p.add_argument("--cycle", required=True)
    p.add_argument("--status", required=True, help="running | placeholder | complete | failed")
    p.add_argument("--stage", required=True)
    p.add_argument("--stage-label", required=True)
    p.add_argument("--hours", type=int, default=18)
    p.add_argument("--wrf-hour-done", type=int, default=None)
    p.add_argument(
        "--hours-available",
        default=None,
        help="Comma-separated forecast hours that have PNGs. Omit to scan --run-dir/refl.",
    )
    p.add_argument("--run-dir", type=Path, default=None, help="plots/<cycle> (meta.json + PNG scan)")
    p.add_argument("--bucket", default=BUCKET)
    p.add_argument("--note", default=None, help="Optional extra string stored as latest.note")
    args = p.parse_args(argv)

    extra = {}
    if args.note:
        extra["note"] = args.note
    publish(
        cycle=args.cycle,
        status=args.status,
        stage=args.stage,
        stage_label=args.stage_label,
        hours=args.hours,
        run_dir=args.run_dir,
        hours_available=parse_hours_csv(args.hours_available),
        wrf_hour_done=args.wrf_hour_done,
        bucket=args.bucket,
        extra=extra or None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
