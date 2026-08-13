#!/bin/bash
# Regional HRRR wrfprs + wrfsfc for the Phoenix 200 km domain.
# NOMADS grib filter for 2D/surface when it works; AWS + wgrib2 -small_grib
# for pressure (NOMADS has no wrfprs filter). Never keep a full-CONUS GRIB.
# Usage: download_hrrr.sh YYYYMMDD [FMAX] [CYCLE_HOUR]
set -euo pipefail
ROOT="${PHX_ROOT:-/home/kenny/phoenix-wrf}"

# Snapshot before running: a live rewrite of this file mid-loop killed the
# 12Z 13 Aug 2026 download after f18 (`break` outside a loop / unexpected `fi`).
if [[ ${PHX_HRRR_FROZEN:-0} -ne 1 ]]; then
  export PHX_HRRR_FROZEN=1
  PHX_HRRR_SNAP=$(mktemp)
  export PHX_HRRR_SNAP
  cp -f "$0" "$PHX_HRRR_SNAP"
  exec /bin/bash "$PHX_HRRR_SNAP" "$@"
fi

DATE="${1:?usage: $0 YYYYMMDD [FMAX] [CYCLE_HOUR]}"
FMAX="${2:-18}"
CYCLE=$(printf '%02d' "$((10#${3:-12}))")
OUT="$ROOT/data/grib/$DATE"
mkdir -p "$OUT"

# 200 km Lambert around 33.45N, -112.07 (~31.5–35.5N, -114.5–-109.5)
# plus extra for LBC / WPS interpolation.
LEFTLON=-115.2
RIGHTLON=-108.9
TOPLAT=36.2
BOTTOMLAT=30.7
LONBOX="${LEFTLON}:${RIGHTLON}"
LATBOX="${BOTTOMLAT}:${TOPLAT}"

# Full CONUS wrfprs is ~400 MB and expands to ~6 GB/hour in ungrib FILE:.
MAX_REGIONAL_BYTES=80000000
MIN_FREE_KB=$((40 * 1024 * 1024))  # 40 GiB on /home

assert_disk() {
  local avail
  avail=$(df -Pk /home | awk 'NR==2 {print $4}')
  if [[ ${avail:-0} -lt $MIN_FREE_KB ]]; then
    echo "DISK: /home has ${avail:-0} KB free (need ${MIN_FREE_KB} KB / 40 GB). Aborting download." >&2
    return 1
  fi
}

phx_dl_status() {
  local label="$1"
  if [[ -z ${PHX_CYCLE:-} ]]; then
    return 0
  fi
  python3 "$ROOT/scripts/publish_status.py" \
    --cycle "$PHX_CYCLE" --hours "${PHX_HOURS:-$FMAX}" \
    ${PHX_PLOTDIR:+--run-dir "$PHX_PLOTDIR"} \
    --status running --stage download --stage-label "$label" \
    || echo "publish_status failed (non-fatal)"
}

WGRIB2=""
for cand in \
    "$ROOT/opt/bin/wgrib2" \
    /home/kenny/anaconda3/envs/wrf-post/bin/wgrib2 \
    wgrib2; do
  if [[ -x $cand ]]; then
    WGRIB2="$cand"
    break
  fi
  if command -v "$cand" >/dev/null 2>&1; then
    WGRIB2=$(command -v "$cand")
    break
  fi
done
if [[ -z $WGRIB2 ]]; then
  echo "FATAL: wgrib2 is required to subset AWS HRRR (NOMADS has no wrfprs filter)." >&2
  exit 1
fi
echo "wgrib2=$WGRIB2"

is_grib2() {
  local magic
  magic=$(head -c 4 "$1" 2>/dev/null || true)
  [[ $magic == GRIB ]]
}

file_bytes() {
  stat -c%s "$1" 2>/dev/null || echo 0
}

is_regional() {
  local dest="$1"
  local sz
  sz=$(file_bytes "$dest")
  [[ $sz -gt 10000 && $sz -le $MAX_REGIONAL_BYTES ]] && is_grib2 "$dest"
}

cleanup_temps() {
  rm -f "$OUT"/*.full "$OUT"/*.sub "$OUT"/*.tmp "$OUT"/*.full.* 2>/dev/null || true
  # Unlink the snapshot inode; bash still holds it open until exit.
  rm -f "${PHX_HRRR_SNAP:-}"
}
trap cleanup_temps EXIT

require_regional() {
  local dest="$1"
  local sz
  sz=$(file_bytes "$dest")
  if ! is_grib2 "$dest"; then
    echo "FATAL: $dest is not GRIB2 (size=$sz)" >&2
    rm -f "$dest"
    return 1
  fi
  if [[ $sz -gt $MAX_REGIONAL_BYTES ]]; then
    echo "FATAL: $dest is still full-CONUS ($sz bytes > $MAX_REGIONAL_BYTES). Refusing to keep it." >&2
    rm -f "$dest"
    return 1
  fi
  echo "regional $dest ($sz bytes)"
}

subset_file() {
  local src="$1"
  local dest="$2"
  rm -f "$dest.sub"
  if "$WGRIB2" "$src" -inv /dev/null -small_grib "$LONBOX" "$LATBOX" "$dest.sub" \
      && [[ -s $dest.sub ]] && is_grib2 "$dest.sub"; then
    mv "$dest.sub" "$dest"
    require_regional "$dest"
    return 0
  fi
  rm -f "$dest.sub"
  echo "wgrib2 -small_grib failed for $src" >&2
  return 1
}

nomads_filter() {
  local product="$1"  # prs | 2d
  local file="$2"
  local dest="$3"
  local cgi params sz
  if [[ $product == prs ]]; then
    cgi="https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_prs.pl"
  else
    cgi="https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
  fi
  # NOMADS 2.3 uses all_var/all_lev; older CGI used allvar/alllev.
  params="dir=%2Fhrrr.${DATE}%2Fconus&file=${file}&all_var=on&all_lev=on&allvar=on&alllev=on&subregion=&leftlon=${LEFTLON}&rightlon=${RIGHTLON}&toplat=${TOPLAT}&bottomlat=${BOTTOMLAT}"
  rm -f "$dest.tmp"
  wget -4 -q --timeout=120 --tries=2 -O "$dest.tmp" "${cgi}?${params}" || {
    rm -f "$dest.tmp"
    return 1
  }
  sz=$(file_bytes "$dest.tmp")
  if [[ $sz -gt 10000 ]] && is_grib2 "$dest.tmp" && [[ $sz -le $MAX_REGIONAL_BYTES ]]; then
    mv "$dest.tmp" "$dest"
    echo "nomads $dest ($sz bytes)"
    sleep 2
    return 0
  fi
  rm -f "$dest.tmp"
  return 1
}

aws_subset() {
  local file="$1"
  local dest="$2"
  local url="https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.${DATE}/conus/${file}"
  local full="$dest.full.$$"
  rm -f "$full" "$dest.sub"
  # Stream AWS → wgrib2 so the ~400 MB CONUS file never has to stay on disk.
  # -small_grib often needs a seekable file; fall back to one temp GRIB, then delete it.
  if wget -4 -q --timeout=180 --tries=3 -O - "$url" \
      | "$WGRIB2" - -inv /dev/null -small_grib "$LONBOX" "$LATBOX" "$dest.sub" \
      && [[ -s $dest.sub ]] && is_grib2 "$dest.sub"; then
    mv "$dest.sub" "$dest"
    echo "aws-stream $dest ($(file_bytes "$dest") bytes)"
    require_regional "$dest"
    return 0
  fi
  rm -f "$dest.sub"
  echo "aws stream/subset missed for $file; downloading one full GRIB then subsetting"
  wget -4 --timeout=180 --tries=5 -O "$full" "$url" || {
    rm -f "$full"
    return 1
  }
  if ! subset_file "$full" "$dest"; then
    rm -f "$full" "$dest"
    return 1
  fi
  rm -f "$full"
  echo "aws-subset $dest ($(file_bytes "$dest") bytes); deleted full CONUS temp"
  return 0
}

ensure_file() {
  local product="$1"
  local file="$2"
  local dest="$3"
  assert_disk
  if is_regional "$dest"; then
    echo "skip $dest ($(file_bytes "$dest") bytes)"
    return 0
  fi
  if [[ -s $dest ]] && is_grib2 "$dest"; then
    echo "existing $dest is too large ($(file_bytes "$dest") bytes); subsetting"
    if subset_file "$dest" "$dest"; then
      return 0
    fi
    rm -f "$dest"
  fi
  rm -f "$dest"
  if [[ $product == 2d ]]; then
    nomads_filter 2d "$file" "$dest" && return 0
  else
    # filter_hrrr_prs.pl is 404 on current NOMADS; try once, then AWS.
    if [[ ${NOMADS_PRS_OK:-0} -eq 1 ]]; then
      nomads_filter prs "$file" "$dest" && return 0
    fi
  fi
  aws_subset "$file" "$dest"
}

describe_grid() {
  local dest="$1"
  "$WGRIB2" "$dest" -nxny -end 2>/dev/null | head -2 || true
}

assert_disk

echo "Downloading regional HRRR ${DATE} t${CYCLE}z f00-f$(printf '%02d' "$FMAX") bbox lon=$LONBOX lat=$LATBOX"
echo "Output $OUT  wgrib2=$WGRIB2"

# --- prove f00 before the 18 h loop (never launch a full-CONUS pull) ---
phx_dl_status "Proving regional HRRR subset on f00"
prs0="$OUT/hrrr.t${CYCLE}z.wrfprsf00.grib2"
sfc0="$OUT/hrrr.t${CYCLE}z.wrfsfcf00.grib2"
NOMADS_PRS_OK=0
if is_regional "$prs0" && is_regional "$sfc0"; then
  echo "f00 already regional; skipping NOMADS prove"
else
  if nomads_filter prs "hrrr.t${CYCLE}z.wrfprsf00.grib2" "$prs0"; then
    NOMADS_PRS_OK=1
    echo "NOMADS wrfprs filter is available"
  else
    echo "NOMADS wrfprs filter unavailable (expected); using AWS + wgrib2 -small_grib"
  fi
fi
ensure_file prs "hrrr.t${CYCLE}z.wrfprsf00.grib2" "$prs0"
ensure_file 2d "hrrr.t${CYCLE}z.wrfsfcf00.grib2" "$sfc0"
echo "f00 wrfprs grid:"
describe_grid "$prs0"
echo "f00 wrfsfc grid:"
describe_grid "$sfc0"
require_regional "$prs0"
require_regional "$sfc0"
echo "f00 subset OK  prs=$(file_bytes "$prs0")  sfc=$(file_bytes "$sfc0")"

fail=0
for f in $(seq 0 "$FMAX"); do
  ff=$(printf '%02d' "$f")
  prs_file="hrrr.t${CYCLE}z.wrfprsf${ff}.grib2"
  sfc_file="hrrr.t${CYCLE}z.wrfsfcf${ff}.grib2"
  prs="$OUT/$prs_file"
  sfc="$OUT/$sfc_file"
  phx_dl_status "Downloading HRRR ${DATE} t${CYCLE}z f${ff}/f$(printf '%02d' "$FMAX") (regional subset)"
  if ! ensure_file prs "$prs_file" "$prs"; then
    echo "FAILED $prs_file" >&2
    fail=1
    break
  fi
  if ! ensure_file 2d "$sfc_file" "$sfc"; then
    echo "FAILED $sfc_file" >&2
    fail=1
    break
  fi
done

cleanup_temps
trap - EXIT

if [[ $fail -ne 0 ]]; then
  echo "HRRR download had failures" >&2
  exit 1
fi

big=$(find "$OUT" -name '*.grib2' -size +80M -print)
if [[ -n ${big:-} ]]; then
  echo "FATAL: full-CONUS GRIB still present:" >&2
  echo "$big" >&2
  exit 1
fi

echo "HRRR download complete (regional only)"
du -h "$OUT" | tail -1
find "$OUT" -name '*.grib2' -printf '%s %p\n' | sort -n | tail -5
