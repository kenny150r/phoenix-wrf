#!/bin/bash
# Delete HRRR GRIB after success; keep wrfout 48h; keep local PNGs 14 days.
set -euo pipefail
ROOT="/home/kenny/phoenix-wrf"
CYCLE=""
KEEP_WRF=48
KEEP_PNG=14
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cycle) CYCLE="$2"; shift 2 ;;
    --keep-wrfout-hours) KEEP_WRF="$2"; shift 2 ;;
    --keep-png-days) KEEP_PNG="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ -n $CYCLE ]]; then
  DATE=${CYCLE:0:8}
  rm -rf "$ROOT/data/grib/$DATE"
  echo "deleted GRIB $DATE"
  rm -f "$ROOT/work/wps"/GRIBFILE.* "$ROOT/work/wps"/FILE:* "$ROOT/work/wps"/SFC:* \
        "$ROOT/work/wps"/PFILE:* "$ROOT/work/wps"/met_em.d01.* || true
fi

# wrfout older than 48 hours
if [[ -d $ROOT/data/wrfout ]]; then
  find "$ROOT/data/wrfout" -mindepth 1 -maxdepth 1 -type d -mmin +$((KEEP_WRF * 60)) -exec rm -rf {} +
fi
# local PNGs older than 14 days
if [[ -d $ROOT/plots ]]; then
  find "$ROOT/plots" -mindepth 1 -maxdepth 1 -type d -mtime +$KEEP_PNG -exec rm -rf {} +
fi
# logs older than 14 days
if [[ -d $ROOT/data/logs ]]; then
  find "$ROOT/data/logs" -type f -mtime +14 -delete
fi
echo "purge complete"
