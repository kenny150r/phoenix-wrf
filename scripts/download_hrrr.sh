#!/bin/bash
# Download 12Z HRRR wrfprs + wrfsfc. NOMADS grib filter first; AWS Open Data fallback.
# Usage: download_hrrr.sh YYYYMMDD [FMAX]
set -euo pipefail
ROOT="${PHX_ROOT:-/home/kenny/phoenix-wrf}"
DATE="${1:?usage: $0 YYYYMMDD [FMAX]}"
FMAX="${2:-18}"
CYCLE=12
OUT="$ROOT/data/grib/$DATE"
mkdir -p "$OUT"

LEFTLON=-115.2
RIGHTLON=-108.9
TOPLAT=36.2
BOTTOMLAT=30.7

nomads_filter() {
  local product="$1"  # prs | 2d
  local file="$2"
  local dest="$3"
  local cgi
  if [[ $product == prs ]]; then
    cgi="https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_prs.pl"
  else
    cgi="https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
  fi
  local params="dir=%2Fhrrr.${DATE}%2Fconus&file=${file}&allvar=on&alllev=on&subregion=&leftlon=${LEFTLON}&rightlon=${RIGHTLON}&toplat=${TOPLAT}&bottomlat=${BOTTOMLAT}"
  wget -4 -q --timeout=120 --tries=2 -O "$dest.tmp" "${cgi}?${params}" || return 1
  local sz
  sz=$(stat -c%s "$dest.tmp" 2>/dev/null || echo 0)
  if [[ $sz -gt 10000 ]]; then
    mv "$dest.tmp" "$dest"
    echo "nomads $dest ($sz bytes)"
    return 0
  fi
  rm -f "$dest.tmp"
  return 1
}

aws_full() {
  local file="$1"
  local dest="$2"
  local url="https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.${DATE}/conus/${file}"
  wget -4 -c --timeout=180 --tries=5 -O "$dest" "$url"
  local sz
  sz=$(stat -c%s "$dest" 2>/dev/null || echo 0)
  if [[ $sz -gt 10000 ]]; then
    echo "aws $dest ($sz bytes)"
    return 0
  fi
  rm -f "$dest"
  return 1
}

echo "Downloading HRRR ${DATE} t${CYCLE}z f00-f$(printf '%02d' "$FMAX") to $OUT"
fail=0
for f in $(seq 0 "$FMAX"); do
  ff=$(printf '%02d' "$f")
  prs_file="hrrr.t${CYCLE}z.wrfprsf${ff}.grib2"
  sfc_file="hrrr.t${CYCLE}z.wrfsfcf${ff}.grib2"
  prs="$OUT/$prs_file"
  sfc="$OUT/$sfc_file"
  if [[ ! -s $prs ]]; then
    nomads_filter prs "$prs_file" "$prs" || aws_full "$prs_file" "$prs" || fail=1
  else
    echo "skip $prs"
  fi
  if [[ ! -s $sfc ]]; then
    nomads_filter 2d "$sfc_file" "$sfc" || aws_full "$sfc_file" "$sfc" || fail=1
  else
    echo "skip $sfc"
  fi
done

if [[ $fail -ne 0 ]]; then
  echo "HRRR download had failures" >&2
  exit 1
fi
echo "HRRR download complete"
du -h "$OUT" | tail -1
