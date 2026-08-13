#!/bin/bash
# Download 12Z HRRR wrfprs + wrfsfc via NOMADS grib filter (lat/lon subset).
# Usage: download_hrrr.sh YYYYMMDD [FMAX]
#   FMAX default 18; use 1 for the smoke test.
set -euo pipefail
ROOT="${PHX_ROOT:-/home/kenny/phoenix-wrf}"
DATE="${1:?usage: $0 YYYYMMDD [FMAX]}"
FMAX="${2:-18}"
CYCLE=12
OUT="$ROOT/data/grib/$DATE"
mkdir -p "$OUT"

# Domain 301 km around PHX plus LBC buffer
LEFTLON=-115.2
RIGHTLON=-108.9
TOPLAT=36.2
BOTTOMLAT=30.7

download_one() {
  local product="$1"  # prs | 2d
  local file="$2"
  local dest="$3"
  local url
  if [[ $product == prs ]]; then
    url="https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_prs.pl"
  else
    url="https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
  fi
  local params="dir=%2Fhrrr.${DATE}%2Fconus&file=${file}&allvar=on&alllev=on&subregion=&leftlon=${LEFTLON}&rightlon=${RIGHTLON}&toplat=${TOPLAT}&bottomlat=${BOTTOMLAT}"
  local attempt
  for attempt in 1 2 3 4 5; do
    if wget -q --timeout=180 --tries=2 -O "$dest.tmp" "${url}?${params}"; then
      local sz
      sz=$(stat -c%s "$dest.tmp" 2>/dev/null || echo 0)
      if [[ $sz -gt 10000 ]]; then
        mv "$dest.tmp" "$dest"
        echo "ok $dest ($sz bytes)"
        return 0
      fi
      echo "too small ($sz) $dest attempt $attempt" >&2
    else
      echo "wget fail $file attempt $attempt" >&2
    fi
    sleep $((attempt * 8))
  done
  rm -f "$dest.tmp"
  return 1
}

echo "Downloading HRRR ${DATE} t${CYCLE}z f00-f$(printf '%02d' "$FMAX") to $OUT"
fail=0
for f in $(seq 0 "$FMAX"); do
  ff=$(printf '%02d' "$f")
  prs="$OUT/hrrr.t${CYCLE}z.wrfprsf${ff}.grib2"
  sfc="$OUT/hrrr.t${CYCLE}z.wrfsfcf${ff}.grib2"
  if [[ ! -s $prs ]]; then
    download_one prs "hrrr.t${CYCLE}z.wrfprsf${ff}.grib2" "$prs" || fail=1
  else
    echo "skip $prs"
  fi
  sleep 2
  if [[ ! -s $sfc ]]; then
    download_one 2d "hrrr.t${CYCLE}z.wrfsfcf${ff}.grib2" "$sfc" || fail=1
  else
    echo "skip $sfc"
  fi
  sleep 2
done

if [[ $fail -ne 0 ]]; then
  echo "HRRR download had failures" >&2
  exit 1
fi
echo "HRRR download complete"
