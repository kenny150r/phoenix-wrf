#!/usr/bin/env python3
"""Watch wrfout_d01_* appear during wrf.exe, plot + upload each hour.

frames_per_outfile=1: a new wrfout file is the history dump. We wait until
rsl.out reports "Timing for Writing <file>" (or the file size is stable and
NetCDF opens) then plot that forecast hour and push PNGs + latest.json.

If a single multi-frame wrfout is used, this watcher still updates stage_label
from rsl "Timing for main" but does not try to slice an in-progress NetCDF;
the driver plots after wrf.exe exits.
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from plot_products import list_wrfout_by_hour, plot_wrfout  # noqa: E402
from publish_status import publish, scan_hours_available  # noqa: E402
from upload_s3 import upload_run  # noqa: E402

WRITE_RE = re.compile(r"Timing for Writing (wrfout_d01_\d{4}-\d{2}-\d{2}_\d{2}:\d{2}:\d{2})")
MAIN_RE = re.compile(r"Timing for main: time (\d{4}-\d{2}-\d{2})_(\d{2}:\d{2}:\d{2})")
SUCCESS_RE = re.compile(r"SUCCESS COMPLETE WRF")


def cycle_start(cycle: str) -> datetime:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{1,2})z$", cycle, re.I)
    if not m:
        raise ValueError(f"bad cycle {cycle}")
    return datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]))


def hour_from_stamp(stamp: str, start: datetime) -> int:
    t = datetime.strptime(stamp, "%Y-%m-%d_%H:%M:%S")
    return int(round((t - start).total_seconds() / 3600.0))


def parse_rsl(rsl: Path, start: datetime) -> dict:
    written: set[str] = set()
    model_hour = None
    success = False
    if not rsl.exists():
        return {"written": written, "model_hour": model_hour, "success": success}
    try:
        text = rsl.read_text(errors="replace")
    except OSError:
        return {"written": written, "model_hour": model_hour, "success": success}
    for line in text.splitlines():
        m = WRITE_RE.search(line)
        if m:
            written.add(m.group(1))
        m = MAIN_RE.search(line)
        if m:
            try:
                model_hour = hour_from_stamp(f"{m.group(1)}_{m.group(2)}", start)
            except ValueError:
                pass
        if SUCCESS_RE.search(line):
            success = True
    return {"written": written, "model_hour": model_hour, "success": success}


def netcdf_readable(path: Path) -> bool:
    try:
        from netCDF4 import Dataset

        nc = Dataset(str(path), "r")
        try:
            if "Times" not in nc.variables:
                return False
            _ = nc.variables["Times"][0]
        finally:
            nc.close()
        return True
    except Exception:
        return False


def parent_alive(pid: int | None) -> bool:
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def file_ready(path: Path, written_names: set[str], sizes: dict[str, list[int]], force: bool) -> bool:
    name = path.name
    if name in written_names:
        return netcdf_readable(path) or force
    try:
        sz = path.stat().st_size
    except OSError:
        return False
    hist = sizes.setdefault(name, [])
    hist.append(sz)
    if len(hist) > 4:
        del hist[:-4]
    stable = len(hist) >= 3 and len(set(hist[-3:])) == 1 and sz > 1_000_000
    if (stable or force) and netcdf_readable(path):
        return True
    return False


def publish_wrf(*, cycle: str, hours: int, run_dir: Path, stage_label: str, note: str | None = None):
    avail = scan_hours_available(run_dir)
    extra = {"note": note} if note else None
    try:
        publish(
            cycle=cycle,
            status="running",
            stage="wrf",
            stage_label=stage_label,
            hours=hours,
            run_dir=run_dir,
            hours_available=avail,
            extra=extra,
        )
    except Exception as exc:
        print(f"publish_status failed: {exc}", file=sys.stderr)


def process_new_hours(
    *,
    wrfout_dir: Path,
    out_dir: Path,
    cycle: str,
    hours: int,
    plotted: set[int],
    written_names: set[str],
    sizes: dict[str, list[int]],
    force: bool,
) -> list[int]:
    by_hour = list_wrfout_by_hour(wrfout_dir, cycle)
    done = []
    for h, path in sorted(by_hour.items()):
        if h in plotted or h < 0 or h > hours:
            continue
        if not file_ready(path, written_names, sizes, force=force):
            continue
        print(f"watcher: plotting F{h:02d} from {path.name}")
        try:
            plot_wrfout(wrfout_dir, out_dir, cycle, only_hours={h}, skip_existing=True)
            upload_run(out_dir, cycle, hours, only_hours=[h], status="running", stage="wrf",
                       stage_label=f"wrf.exe · F{h:02d} / {hours} written")
            plotted.add(h)
            done.append(h)
        except Exception as exc:
            print(f"watcher: plot/upload F{h:02d} failed: {exc}", file=sys.stderr)
    return done


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--wrfout-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--cycle", required=True)
    p.add_argument("--hours", type=int, default=18)
    p.add_argument("--rsl", type=Path, default=None)
    p.add_argument("--stop-file", type=Path, default=None)
    p.add_argument("--parent-pid", type=int, default=None)
    p.add_argument("--interval", type=float, default=8.0)
    p.add_argument("--once", action="store_true", help="One sweep then exit (catch-up)")
    args = p.parse_args()

    stop = {"flag": False}

    def _stop(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rsl = args.rsl or (args.wrfout_dir / "rsl.out.0000")
    start = cycle_start(args.cycle)
    plotted: set[int] = set(scan_hours_available(args.out_dir))
    sizes: dict[str, list[int]] = {}
    last_pub = 0.0
    last_label = ""

    print(f"watcher start cycle={args.cycle} wrfout={args.wrfout_dir} already={sorted(plotted)}")

    while True:
        want_stop = stop["flag"]
        if args.stop_file and args.stop_file.exists():
            want_stop = True
        if not parent_alive(args.parent_pid):
            want_stop = True

        info = parse_rsl(rsl, start)
        force = want_stop or info["success"]
        new = process_new_hours(
            wrfout_dir=args.wrfout_dir,
            out_dir=args.out_dir,
            cycle=args.cycle,
            hours=args.hours,
            plotted=plotted,
            written_names=info["written"],
            sizes=sizes,
            force=force,
        )
        avail = scan_hours_available(args.out_dir)
        done = max(avail) if avail else 0
        integrating = info["model_hour"]
        if info["success"]:
            label = f"wrf.exe complete · F{done:02d} / {args.hours}"
        elif integrating is not None:
            label = f"wrf.exe · integrating F{integrating:02d} / {args.hours} · {len(avail)} frame(s) on S3"
        elif avail:
            label = f"wrf.exe · F{done:02d} / {args.hours} written"
        else:
            label = f"wrf.exe · starting (0 / {args.hours})"

        now = time.time()
        if new or label != last_label and now - last_pub >= 45:
            publish_wrf(cycle=args.cycle, hours=args.hours, run_dir=args.out_dir, stage_label=label)
            last_pub = now
            last_label = label

        if args.once or want_stop:
            if not new:
                process_new_hours(
                    wrfout_dir=args.wrfout_dir,
                    out_dir=args.out_dir,
                    cycle=args.cycle,
                    hours=args.hours,
                    plotted=plotted,
                    written_names=info["written"],
                    sizes=sizes,
                    force=True,
                )
            print(f"watcher exit plotted={sorted(plotted)}")
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
