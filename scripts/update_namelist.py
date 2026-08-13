#!/usr/bin/env python3
"""Patch namelist.wps / namelist.input dates and metgrid dimensions (stdlib only)."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


def set_wps(path: Path, start: datetime, end: datetime):
    text = path.read_text()
    s = start.strftime("%Y-%m-%d_%H:%M:%S")
    e = end.strftime("%Y-%m-%d_%H:%M:%S")
    text = re.sub(
        r"start_date\s*=\s*'[^']*'(,'[^']*')?",
        f" start_date = '{s}','{s}',",
        text,
        count=1,
    )
    text = re.sub(
        r"end_date\s*=\s*'[^']*'(,'[^']*')?",
        f" end_date   = '{e}','{e}',",
        text,
        count=1,
    )
    path.write_text(text)


def set_input(path: Path, start: datetime, end: datetime, hours: int, nmet: int | None, nsoil: int | None):
    text = path.read_text()
    text = re.sub(r"run_hours\s*=\s*\d+", f" run_hours                           = {hours}", text, count=1)

    def block(prefix: str, dt: datetime):
        nonlocal text
        text = re.sub(rf"{prefix}_year\s*=.*", f" {prefix}_year                          = {dt.year:04d}, {dt.year:04d},", text, count=1)
        text = re.sub(rf"{prefix}_month\s*=.*", f" {prefix}_month                         = {dt.month:02d},   {dt.month:02d},", text, count=1)
        text = re.sub(rf"{prefix}_day\s*=.*", f" {prefix}_day                           = {dt.day:02d},   {dt.day:02d},", text, count=1)
        text = re.sub(rf"{prefix}_hour\s*=.*", f" {prefix}_hour                          = {dt.hour:02d},   {dt.hour:02d},", text, count=1)

    block("start", start)
    block("end", end)
    if nmet is not None:
        text = re.sub(
            r"num_metgrid_levels\s*=\s*\d+",
            f" num_metgrid_levels                  = {nmet}",
            text,
            count=1,
        )
    if nsoil is not None:
        text = re.sub(
            r"num_metgrid_soil_levels\s*=\s*\d+",
            f" num_metgrid_soil_levels             = {nsoil}",
            text,
            count=1,
        )
    path.write_text(text)


def dim_from_ncdump(ncdump_out: str, name: str) -> int | None:
    m = re.search(rf"{name}\s*=\s*(\d+)", ncdump_out)
    return int(m.group(1)) if m else None


def soil_levels_from_ncdump(ncdump_out: str) -> int | None:
    # WPS met_em uses num_soilt_levels / num_soilm_levels; WRF namelist
    # wants num_metgrid_soil_levels. Older dumps used num_st_layers.
    for name in (
        "num_soilt_levels",
        "num_soilm_levels",
        "num_metgrid_soil_levels",
        "num_st_layers",
        "num_sm_layers",
    ):
        val = dim_from_ncdump(ncdump_out, name)
        if val is not None:
            return val
    return None


def emit_dims(ncdump_out: str) -> None:
    nmet = dim_from_ncdump(ncdump_out, "num_metgrid_levels")
    nsoil = soil_levels_from_ncdump(ncdump_out)
    print(f"NMET={nmet or ''}")
    print(f"NSOIL={nsoil or ''}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wps", type=Path)
    p.add_argument("--input", type=Path)
    p.add_argument("--date", help="YYYYMMDD cycle date")
    p.add_argument("--hours", type=int, default=18)
    p.add_argument("--cycle-hour", type=int, default=12, help="UTC cycle hour (0 or 12)")
    p.add_argument("--nmet", type=int)
    p.add_argument("--nsoil", type=int)
    p.add_argument(
        "--emit-dims",
        action="store_true",
        help="Read ncdump -h on stdin; print NMET=/NSOIL= for the driver to eval",
    )
    args = p.parse_args()
    if args.emit_dims:
        emit_dims(sys.stdin.read())
        return
    if not args.date:
        p.error("--date is required")
    start = datetime.strptime(f"{args.date}{args.cycle_hour:02d}", "%Y%m%d%H")
    end = start + timedelta(hours=args.hours)
    if args.wps:
        set_wps(args.wps, start, end)
    if args.input:
        set_input(args.input, start, end, args.hours, args.nmet, args.nsoil)


if __name__ == "__main__":
    main()
