#!/bin/bash
# Daily (or smoke-test) Phoenix 1 km WRF forecast driver.
# Usage: run_forecast.sh [--hours N] [--date YYYYMMDD]
set -euo pipefail
ROOT="/home/kenny/phoenix-wrf"
# shellcheck disable=SC1091
source "$ROOT/env.sh"

HOURS=18
DATE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --hours) HOURS="$2"; shift 2 ;;
    --date) DATE="$2"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

if [[ -z $DATE ]]; then
  # Timer fires ~14:20 UTC for the 12Z cycle.
  DATE=$(date -u +%Y%m%d)
  hour=$(date -u +%H)
  if [[ 10#$hour -lt 14 ]]; then
    DATE=$(date -u -d 'yesterday' +%Y%m%d)
  fi
fi

CYCLE="${DATE}T12z"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOGDIR="$ROOT/data/logs"
mkdir -p "$LOGDIR" "$ROOT/work/wps" "$ROOT/work/wrf" "$ROOT/data/wrfout" "$ROOT/plots/$CYCLE"
LOG="$LOGDIR/forecast_${CYCLE}_${STAMP}.log"
exec > >(tee -a "$LOG") 2>&1

echo "=== Phoenix WRF $CYCLE hours=$HOURS pid=$$ ==="
echo "PATH=$PATH"
which wrf.exe >/dev/null 2>&1 || true

WPS_SRC="$ROOT/src/WPS"
WRF_SRC="$ROOT/src/WRF"
if [[ ! -x $WRF_SRC/main/wrf.exe || ! -x $WPS_SRC/ungrib.exe ]]; then
  echo "WRF/WPS binaries missing. Run scripts/compile_wrf.sh first." >&2
  exit 1
fi

# --- HRRR ---
"$ROOT/scripts/download_hrrr.sh" "$DATE" "$HOURS"

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
python3 "$ROOT/scripts/update_namelist.py" --wps namelist.wps --date "$DATE" --hours "$HOURS"

# Patch link_grib shebang if needed
LINK="$WPS_SRC/link_grib.csh"
if head -1 "$LINK" | grep -q '/bin/csh'; then
  sed -i "1s|^#!.*csh.*|#!$ROOT/opt/bin/csh -f|" "$LINK"
fi

if [[ ! -f $ROOT/data/wps/geo_em_d01.nc ]]; then
  echo "=== geogrid ==="
  ./geogrid.exe
  mkdir -p "$ROOT/data/wps"
  cp -f geo_em_d01.nc "$ROOT/data/wps/geo_em_d01.nc"
else
  ln -sfn "$ROOT/data/wps/geo_em_d01.nc" geo_em_d01.nc
  echo "reusing geo_em_d01.nc"
fi

GRIB="$ROOT/data/grib/$DATE"
echo "=== ungrib FILE (prs) ==="
"$LINK" "$GRIB"/hrrr.t12z.wrfprsf*.grib2
ln -sfn "$ROOT/config/Vtable.HRRR" Vtable
sed -i "s/^ prefix.*/ prefix = 'FILE',/" namelist.wps
./ungrib.exe

echo "=== ungrib SFC ==="
rm -f GRIBFILE.*
"$LINK" "$GRIB"/hrrr.t12z.wrfsfcf*.grib2
sed -i "s/^ prefix.*/ prefix = 'SFC',/" namelist.wps
./ungrib.exe

echo "=== metgrid ==="
sed -i "s/^ prefix.*/ prefix = 'FILE',/" namelist.wps
./metgrid.exe

MET=$(ls -1 met_em.d01.* | head -1)
NCDUMP_OUT=$("$ROOT/opt/bin/ncdump" -h "$MET")
NMET=$(echo "$NCDUMP_OUT" | sed -n 's/.*num_metgrid_levels *= *\([0-9]*\).*/\1/p' | head -1)
NSOIL=$(echo "$NCDUMP_OUT" | sed -n 's/.*num_st_layers *= *\([0-9]*\).*/\1/p' | head -1)
if [[ -z ${NSOIL:-} ]]; then
  NSOIL=$(echo "$NCDUMP_OUT" | sed -n 's/.*num_sm_layers *= *\([0-9]*\).*/\1/p' | head -1)
fi
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
  ${NMET:+--nmet "$NMET"} ${NSOIL:+--nsoil "$NSOIL"}

echo "=== real.exe ==="
./real.exe

echo "=== wrf.exe np=4 hours=$HOURS ==="
START=$(date +%s)
mpirun --bind-to core --map-by core -np 4 ./wrf.exe
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

mkdir -p "$ROOT/data/wrfout/$CYCLE"
cp -f wrfout_d01_* "$ROOT/data/wrfout/$CYCLE/" || true

# --- plots + S3 ---
PLOTDIR="$ROOT/plots/$CYCLE"
mkdir -p "$PLOTDIR"
if conda run -n wrf-post python --version >/dev/null 2>&1; then
  CONDA_PYTHON=(conda run -n wrf-post python)
else
  CONDA_PYTHON=(/home/kenny/anaconda3/envs/wrf-post/bin/python)
fi
# conda is not on WRF PATH; call it explicitly
if [[ -x /home/kenny/anaconda3/bin/conda ]]; then
  /home/kenny/anaconda3/bin/conda run -n wrf-post python "$ROOT/scripts/plot_products.py" \
    --wrfout-dir "$ROOT/data/wrfout/$CYCLE" --out-dir "$PLOTDIR" --cycle "$CYCLE"
  /home/kenny/anaconda3/bin/conda run -n wrf-post python "$ROOT/scripts/upload_s3.py" \
    --run-dir "$PLOTDIR" --cycle "$CYCLE" --hours "$HOURS"
else
  echo "conda wrf-post missing; skipping plots" >&2
fi

"$ROOT/scripts/purge.sh" --cycle "$CYCLE" --keep-wrfout-hours 48 --keep-png-days 14

echo "=== done $CYCLE elapsed_wrf=${ELAPSED}s ==="
echo "$ELAPSED" > "$LOGDIR/last_wrf_seconds.txt"
echo "$HOURS" > "$LOGDIR/last_wrf_hours.txt"
