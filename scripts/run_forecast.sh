#!/bin/bash
# Daily (or smoke-test) Phoenix 1 km WRF forecast driver.
# Usage: run_forecast.sh [--hours N] [--date YYYYMMDD] [--cycle-hour HH]
set -euo pipefail
ROOT="/home/kenny/phoenix-wrf"
# Survive a closed terminal (00Z 13 Aug died mid-ungrib after the session went away).
trap '' HUP
# shellcheck disable=SC1091
source "$ROOT/env.sh"
export AWS_EC2_METADATA_DISABLED=true

HOURS=18
DATE=""
CYCLE_HOUR=12
while [[ $# -gt 0 ]]; do
  case "$1" in
    --hours) HOURS="$2"; shift 2 ;;
    --date) DATE="$2"; shift 2 ;;
    --cycle-hour) CYCLE_HOUR="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

CYCLE_HOUR=$(printf '%02d' "$((10#$CYCLE_HOUR))")

if [[ -z $DATE ]]; then
  # Timer fires ~14:20 UTC for the 12Z cycle.
  DATE=$(date -u +%Y%m%d)
  hour=$(date -u +%H)
  if [[ 10#$hour -lt 14 ]]; then
    DATE=$(date -u -d 'yesterday' +%Y%m%d)
  fi
fi

CYCLE="${DATE}T${CYCLE_HOUR}z"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOGDIR="$ROOT/data/logs"
PLOTDIR="$ROOT/plots/$CYCLE"
POST_PY="/home/kenny/anaconda3/envs/wrf-post/bin/python"
STAGE_LABEL="startup"
FINAL=0
WATCH_PID=""
WATCH_STOP="$LOGDIR/watch_${CYCLE}.stop"

# Full-CONUS ungrib FILE: intermediates were ~111 GB (00Z 13 Aug). Abort at 40 GB free.
MIN_FREE_KB=$((40 * 1024 * 1024))
DISK_MON_PID=""
DISK_ABORT="$LOGDIR/disk_abort_${CYCLE}"

need_free_kb() {
  local kb="${1:-$MIN_FREE_KB}"
  local msg="${2:-Abort: /home below 40 GB free.}"
  local avail
  avail=$(df -Pk /home | awk 'NR==2 {print $4}')
  if [[ ${avail:-0} -lt $kb ]]; then
    echo "DISK: need ${kb} KB free on /home (have ${avail} KB). $msg" >&2
    return 1
  fi
}

start_disk_monitor() {
  (
    while sleep 15; do
      avail=$(df -Pk /home | awk 'NR==2 {print $4}')
      if [[ ${avail:-0} -lt $MIN_FREE_KB ]]; then
        echo "DISK: /home free ${avail} KB < 40 GB; aborting forecast pid $PPID" >&2
        echo "${avail}" > "$DISK_ABORT"
        python3 "$ROOT/scripts/publish_status.py" \
          --cycle "$CYCLE" --hours "$HOURS" --run-dir "$PLOTDIR" \
          --status failed --stage failed \
          --stage-label "Aborted: /home below 40 GB free (${avail} KB)" \
          || true
        kill -TERM "$PPID" 2>/dev/null || true
        exit 1
      fi
    done
  ) &
  DISK_MON_PID=$!
}

stop_disk_monitor() {
  if [[ -n ${DISK_MON_PID:-} ]] && kill -0 "$DISK_MON_PID" 2>/dev/null; then
    kill "$DISK_MON_PID" 2>/dev/null || true
    wait "$DISK_MON_PID" 2>/dev/null || true
  fi
  DISK_MON_PID=""
}

mkdir -p "$LOGDIR" "$ROOT/work/wps" "$ROOT/work/wrf" "$ROOT/data/wrfout" "$PLOTDIR"
echo "run_forecast starting $(date -u +%Y-%m-%dT%H:%M:%SZ) cycle=$CYCLE pid=$$" >> "$LOGDIR/systemd.log"

LOCK="$LOGDIR/forecast.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "forecast already running (lock $LOCK); not starting a second copy" >&2
  exit 0
fi

phx_status() {
  python3 "$ROOT/scripts/publish_status.py" \
    --cycle "$CYCLE" --hours "$HOURS" --run-dir "$PLOTDIR" "$@" \
    || echo "publish_status failed (non-fatal)"
}

stop_watcher() {
  if [[ -n ${WATCH_PID:-} ]] && kill -0 "$WATCH_PID" 2>/dev/null; then
    touch "$WATCH_STOP"
    local i
    for i in $(seq 1 120); do
      kill -0 "$WATCH_PID" 2>/dev/null || { WATCH_PID=""; return 0; }
      sleep 5
    done
    kill -TERM "$WATCH_PID" 2>/dev/null || true
    wait "$WATCH_PID" 2>/dev/null || true
    WATCH_PID=""
  fi
}

on_exit() {
  local rc=$?
  stop_disk_monitor
  stop_watcher
  if [[ $rc -ne 0 && $FINAL -eq 0 ]]; then
    if [[ -f $DISK_ABORT ]]; then
      phx_status --status failed --stage failed \
        --stage-label "Aborted: /home below 40 GB free during ${STAGE_LABEL}" || true
    else
      phx_status --status failed --stage failed \
        --stage-label "Failed during ${STAGE_LABEL} (exit ${rc})" || true
    fi
    rm -f "$ROOT/work/wps"/FILE:* "$ROOT/work/wps"/SFC:* \
          "$ROOT/work/wps"/PFILE:* "$ROOT/work/wps"/GRIBFILE.* 2>/dev/null || true
  fi
}
trap on_exit EXIT

LOG="$LOGDIR/forecast_${CYCLE}_${STAMP}.log"
exec > >(tee -a "$LOG") 2>&1
start_disk_monitor
need_free_kb "$MIN_FREE_KB" "Free space before starting (WPS + wrfout)." || exit 1

echo "=== Phoenix WRF $CYCLE hours=$HOURS pid=$$ ==="
echo "PATH=$PATH"
which wrf.exe >/dev/null 2>&1 || true

WPS_SRC="$ROOT/src/WPS"
WRF_SRC="$ROOT/src/WRF"
if [[ ! -x $WRF_SRC/main/wrf.exe || ! -x $WPS_SRC/ungrib.exe ]]; then
  echo "WRF/WPS binaries missing. Run scripts/compile_wrf.sh first." >&2
  exit 1
fi

# --- HRRR (regional subset only; never ungrib full CONUS) ---
STAGE_LABEL="HRRR download"
export PHX_CYCLE="$CYCLE" PHX_HOURS="$HOURS" PHX_PLOTDIR="$PLOTDIR"
phx_status --status running --stage download --stage-label "Downloading HRRR ${DATE} t${CYCLE_HOUR}z (regional subset)"
"$ROOT/scripts/download_hrrr.sh" "$DATE" "$HOURS" "$CYCLE_HOUR"

GRIB="$ROOT/data/grib/$DATE"
if find "$GRIB" -name '*.grib2' -size +80M | grep -q .; then
  echo "FATAL: full-CONUS HRRR still present after download; refusing ungrib" >&2
  find "$GRIB" -name '*.grib2' -size +80M -ls >&2
  exit 1
fi
need_free_kb "$MIN_FREE_KB" "Free space after HRRR download." || exit 1

# --- WPS workspace ---
WPS="$ROOT/work/wps"
cd "$WPS"
rm -f GRIBFILE.* FILE:* SFC:* PFILE:* met_em.d01.* ungrib.log metgrid.log
ln -sfn "$WPS_SRC/geogrid.exe" .
ln -sfn "$WPS_SRC/ungrib.exe" .
ln -sfn "$WPS_SRC/metgrid.exe" .
ln -sfn "$WPS_SRC/geogrid" .
ln -sfn "$WPS_SRC/metgrid" .
ln -sfn "$WPS_SRC/ungrib" .
cp -f "$ROOT/config/namelist.wps" namelist.wps
python3 "$ROOT/scripts/update_namelist.py" --wps namelist.wps --date "$DATE" --hours "$HOURS" --cycle-hour "$CYCLE_HOUR"

# Patch link_grib shebang if needed
LINK="$WPS_SRC/link_grib.csh"
if head -1 "$LINK" | grep -q '/bin/csh'; then
  sed -i "1s|^#!.*csh.*|#!$ROOT/opt/bin/csh -f|" "$LINK"
fi

if [[ ! -f $ROOT/data/wps/geo_em.d01.nc ]]; then
  echo "=== geogrid ==="
  STAGE_LABEL="WPS geogrid"
  phx_status --status running --stage wps --stage-label "WPS geogrid"
  rm -f geo_em.d01.nc
  ./geogrid.exe
  mkdir -p "$ROOT/data/wps"
  cp -f geo_em.d01.nc "$ROOT/data/wps/geo_em.d01.nc"
else
  ln -sfn "$ROOT/data/wps/geo_em.d01.nc" geo_em.d01.nc
  echo "reusing geo_em.d01.nc"
fi

echo "=== ungrib FILE (prs) ==="
STAGE_LABEL="WPS ungrib (pressure)"
need_free_kb "$MIN_FREE_KB" "Free space before ungrib." || exit 1
phx_status --status running --stage wps --stage-label "WPS ungrib (pressure GRIB)"
"$LINK" "$GRIB"/hrrr.t${CYCLE_HOUR}z.wrfprsf*.grib2
ln -sfn "$ROOT/config/Vtable.HRRR" Vtable
sed -i "s/^ prefix.*/ prefix = 'FILE',/" namelist.wps
./ungrib.exe

echo "=== ungrib SFC ==="
STAGE_LABEL="WPS ungrib (surface)"
phx_status --status running --stage wps --stage-label "WPS ungrib (surface GRIB)"
rm -f GRIBFILE.*
"$LINK" "$GRIB"/hrrr.t${CYCLE_HOUR}z.wrfsfcf*.grib2
sed -i "s/^ prefix.*/ prefix = 'SFC',/" namelist.wps
./ungrib.exe

echo "=== metgrid ==="
STAGE_LABEL="WPS metgrid"
phx_status --status running --stage wps --stage-label "WPS metgrid"
sed -i "s/^ prefix.*/ prefix = 'FILE',/" namelist.wps
./metgrid.exe

# Drop ungrib intermediates and the GRIBs themselves — FILE: was 111 GB for full CONUS.
rm -f GRIBFILE.* FILE:* SFC:* PFILE:*
rm -f "$GRIB"/*.grib2 "$GRIB"/*.full "$GRIB"/*.sub "$GRIB"/*.tmp 2>/dev/null || true
need_free_kb "$MIN_FREE_KB" "Free space after metgrid." || exit 1

MET=$(ls -1 met_em.d01.* | head -1)
NCDUMP_OUT=$("$ROOT/opt/bin/ncdump" -h "$MET" 2>/dev/null || true)
NMET=$(echo "$NCDUMP_OUT" | grep -oE 'num_metgrid_levels *= *[0-9]+' | grep -oE '[0-9]+' | head -1)
NSOIL=$(echo "$NCDUMP_OUT" | grep -oE 'num_st_layers *= *[0-9]+|num_sm_layers *= *[0-9]+|num_metgrid_soil_levels *= *[0-9]+' | grep -oE '[0-9]+' | head -1)
echo "num_metgrid_levels=$NMET num_soil=$NSOIL"

# --- WRF workspace ---
WRF="$ROOT/work/wrf"
mkdir -p "$WRF"
cd "$WRF"
# tables / data from WRF/run
shopt -s nullglob
for f in "$WRF_SRC/run/"*; do
  base=$(basename "$f")
  case "$base" in
    namelist.input|wrf.exe|real.exe) ;;
    *) ln -sfn "$f" . ;;
  esac
done
ln -sfn "$WRF_SRC/main/real.exe" .
ln -sfn "$WRF_SRC/main/wrf.exe" .
rm -f met_em.d01.* wrfout_d01_* wrfinput_d01 wrfbdy_d01 rsl.*
ln -sfn "$WPS"/met_em.d01.* .
cp -f "$ROOT/config/namelist.input" namelist.input
cp -f "$ROOT/config/iofields.txt" iofields.txt
python3 "$ROOT/scripts/update_namelist.py" --input namelist.input --date "$DATE" --hours "$HOURS" \
  --cycle-hour "$CYCLE_HOUR" ${NMET:+--nmet "$NMET"} ${NSOIL:+--nsoil "$NSOIL"}

echo "=== real.exe ==="
STAGE_LABEL="real.exe"
need_free_kb "$MIN_FREE_KB" "Free space before real.exe." || exit 1
phx_status --status running --stage real --stage-label "Running real.exe"
./real.exe

echo "=== wrf.exe np=4 hours=$HOURS ==="
STAGE_LABEL="wrf.exe"
need_free_kb "$MIN_FREE_KB" "Free space before wrf.exe." || exit 1
phx_status --status running --stage wrf --stage-label "wrf.exe · starting F00 / ${HOURS}"
if [[ -x $POST_PY && -f $ROOT/scripts/watch_wrfout.py ]]; then
  rm -f "$WATCH_STOP"
  "$POST_PY" "$ROOT/scripts/watch_wrfout.py" \
    --wrfout-dir "$WRF" --out-dir "$PLOTDIR" --cycle "$CYCLE" --hours "$HOURS" \
    --rsl "$WRF/rsl.out.0000" --stop-file "$WATCH_STOP" --parent-pid $$ &
  WATCH_PID=$!
  echo "wrfout watcher pid=$WATCH_PID"
fi
START=$(date +%s)
MPI_WRAP=(nice -n 5)
if command -v ionice >/dev/null 2>&1; then
  MPI_WRAP+=(ionice -c2 -n5)
fi
"${MPI_WRAP[@]}" mpirun --bind-to core --map-by core -np 4 ./wrf.exe
END=$(date +%s)
ELAPSED=$((END - START))
echo "wrf.exe wall_clock_seconds=$ELAPSED"
python3 - <<PY
elapsed=$ELAPSED
hours=$HOURS
print(f"smoke_or_run_hours={hours}")
print(f"wrf_seconds={elapsed}")
if hours > 0:
    per=elapsed/hours
    print(f"seconds_per_forecast_hour={per:.1f}")
    print(f"extrapolated_18h_seconds={per*18:.0f}")
    print(f"extrapolated_18h_hours={per*18/3600:.2f}")
PY

stop_watcher

mkdir -p "$ROOT/data/wrfout/$CYCLE"
cp -f wrfout_d01_* "$ROOT/data/wrfout/$CYCLE/" || true

# --- plots + S3 ---
STAGE_LABEL="plot"
phx_status --status running --stage plot --stage-label "Plotting remaining frames"
mkdir -p "$PLOTDIR"
if [[ -x $POST_PY && -f $ROOT/scripts/plot_products.py ]]; then
  "$POST_PY" "$ROOT/scripts/plot_products.py" \
    --wrfout-dir "$ROOT/data/wrfout/$CYCLE" --out-dir "$PLOTDIR" --cycle "$CYCLE" \
    --skip-existing \
    || echo "plot_products.py failed (non-fatal)"
  if [[ -f $ROOT/scripts/upload_s3.py ]]; then
    STAGE_LABEL="upload"
    phx_status --status running --stage upload --stage-label "Uploading frames to S3"
    "$POST_PY" "$ROOT/scripts/upload_s3.py" \
      --run-dir "$PLOTDIR" --cycle "$CYCLE" --hours "$HOURS" --status complete \
      --stage complete --stage-label "Complete · ${CYCLE}" \
      || echo "upload_s3.py failed (non-fatal)"
  fi
else
  echo "skipping plots/upload (wrf-post python missing)"
fi

"$ROOT/scripts/purge.sh" --cycle "$CYCLE" --keep-wrfout-hours 48 --keep-png-days 14 || echo "purge failed (non-fatal)"

FINAL=1
phx_status --status complete --stage complete --stage-label "Complete · ${CYCLE} (${HOURS} h)"
echo "=== done $CYCLE elapsed_wrf=${ELAPSED}s ==="
echo "$ELAPSED" > "$LOGDIR/last_wrf_seconds.txt"
echo "$HOURS" > "$LOGDIR/last_wrf_hours.txt"
