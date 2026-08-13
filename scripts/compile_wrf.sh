#!/bin/bash
# Compile WRF 4.6.1 (GNU dmpar) and WPS 4.6.0. Do not use Anaconda MPI.
set -euo pipefail
ROOT="/home/kenny/phoenix-wrf"
# shellcheck disable=SC1091
source "$ROOT/env.sh"

LOG="$ROOT/data/logs/compile.log"
mkdir -p "$ROOT/data/logs" "$ROOT/src" "$ROOT/opt/grib2"
exec > >(tee -a "$LOG") 2>&1

echo "=== $(date -u +%FT%TZ) compile start ==="
echo "gfortran: $(gfortran --version | head -1)"
echo "mpif90: $(which mpif90)"
mpif90 --showme | head -1
echo "NETCDF=$NETCDF"

# --- netCDF prefix WRF expects ($NETCDF/include + $NETCDF/lib) ---
N="$ROOT/opt/netcdf"
P="$ROOT/opt/prefix"
mkdir -p "$N/include" "$N/lib" "$N/bin"
ln -sfn "$P/usr/include/netcdf.h" "$N/include/"
ln -sfn "$P/usr/include/netcdf.inc" "$N/include/"
shopt -s nullglob
for f in "$P/usr/include/"*.mod "$P/usr/include/"netcdf_*.h "$P/usr/include/"typesizes.mod; do
  ln -sfn "$f" "$N/include/"
done
ln -sfn /usr/lib/x86_64-linux-gnu/libnetcdf.so.19 "$N/lib/libnetcdf.so.19"
ln -sfn /usr/lib/x86_64-linux-gnu/libnetcdf.so.19 "$N/lib/libnetcdf.so"
ln -sfn "$P/usr/lib/x86_64-linux-gnu/libnetcdff.so.7.1.0" "$N/lib/libnetcdff.so.7.1.0"
ln -sfn "$N/lib/libnetcdff.so.7.1.0" "$N/lib/libnetcdff.so.7"
ln -sfn "$N/lib/libnetcdff.so.7.1.0" "$N/lib/libnetcdff.so"
ln -sfn "$P/usr/bin/ncdump" "$N/bin/"
ln -sfn "$P/usr/bin/nc-config" "$N/bin/"

# --- Jasper (GRIB2 for WPS) ---
if [[ ! -f $ROOT/opt/grib2/lib/libjasper.a && ! -f $ROOT/opt/grib2/lib/libjasper.so ]]; then
  echo "=== building jasper ==="
  cd "$ROOT/src"
  if [[ ! -f jasper-1.900.1.tar.gz ]]; then
    wget -q -O jasper-1.900.1.tar.gz \
      https://www2.mmm.ucar.edu/wrf/OnLineTutorial/compile_tutorial/tar_files/jasper-1.900.1.tar.gz
  fi
  rm -rf jasper-1.900.1
  tar -xzf jasper-1.900.1.tar.gz
  cd jasper-1.900.1
  ./configure --prefix="$ROOT/opt/grib2"
  make -j4
  make install
fi
export JASPERLIB="$ROOT/opt/grib2/lib"
export JASPERINC="$ROOT/opt/grib2/include"
export LDFLAGS="-L$ROOT/opt/grib2/lib -L$N/lib"
export CPPFLAGS="-I$ROOT/opt/grib2/include -I$N/include"

# --- WRF 4.6.1 ---
cd "$ROOT/src"
if [[ ! -d WRF/main ]]; then
  echo "=== downloading WRF 4.6.1 ==="
  wget -c -O v4.6.1.tar.gz https://github.com/wrf-model/WRF/releases/download/v4.6.1/v4.6.1.tar.gz
  rm -rf WRF WRFV4.6.1
  tar -xzf v4.6.1.tar.gz
  if [[ -d WRFV4.6.1 ]]; then mv WRFV4.6.1 WRF
  elif [[ -d WRF-4.6.1 ]]; then mv WRF-4.6.1 WRF
  fi
fi

cd "$ROOT/src/WRF"
# Patch csh shebangs (no /bin/csh on this host)
while IFS= read -r -d '' f; do
  sed -i "1s|^#!.*csh.*|#!$ROOT/opt/bin/csh -f|" "$f"
done < <(grep -rlZ '^#!.*csh' . --include='*' 2>/dev/null || true)

# Ubuntu netCDF libs live in lib/x86_64-linux-gnu; our combined prefix uses lib/
sed -i 's#$NETCDF/lib#$NETCDF/lib#g' configure || true

if [[ ! -f configure.wrf ]]; then
  echo "=== configuring WRF (GNU dmpar, basic nesting) ==="
  printf '34\n1\n' | ./configure
  # Link netcdff explicitly if configure missed it
  if grep -q -- '-lnetcdf' configure.wrf && ! grep -q -- '-lnetcdff' configure.wrf; then
    sed -i 's/-lnetcdf/-lnetcdff -lnetcdf/g' configure.wrf
  fi
  # Use our gfortran wrapper
  sed -i "s|^SFC *=.*|SFC = gfortran|" configure.wrf
  sed -i "s|^SCC *=.*|SCC = gcc|" configure.wrf
  sed -i "s|^CCOMP *=.*|CCOMP = gcc|" configure.wrf
  sed -i "s|^DM_FC *=.*|DM_FC = mpif90|" configure.wrf
  sed -i "s|^DM_CC *=.*|DM_CC = mpicc -DMPI2_SUPPORT|" configure.wrf
fi

ensure_md_calls() {
  # m4 without libsigsegv leaves a 0-byte md_calls.inc; make then skips
  # regeneration and module_io.F compiles without CONTAINS.
  if [[ -s frame/md_calls.inc ]] && grep -q CONTAINS frame/md_calls.inc; then
    return 0
  fi
  echo "=== generating frame/md_calls.inc ==="
  rm -f frame/md_calls.inc
  if ! m4 --version >/dev/null 2>&1; then
    echo "WARNING: m4 cannot run (libsigsegv?); copying arch/md_calls.inc" >&2
    cp -f "$ROOT/src/WRF/arch/md_calls.inc" frame/md_calls.inc
  elif ! ( cd frame && m4 -G md_calls.m4 > md_calls.inc ) || ! grep -q CONTAINS frame/md_calls.inc; then
    echo "WARNING: m4 md_calls.m4 failed; copying arch/md_calls.inc" >&2
    cp -f "$ROOT/src/WRF/arch/md_calls.inc" frame/md_calls.inc
  fi
}

if [[ ! -x main/wrf.exe ]]; then
  echo "=== compiling WRF em_real (this takes a while) ==="
  # Drop leftover objects from a failed make -i so module_io.f90 is rebuilt.
  ./clean || true
  ensure_md_calls
  "$ROOT/opt/bin/csh" -c "setenv J 4; setenv NETCDF $NETCDF; setenv WRFIO_NCD_LARGE_FILE_SUPPORT 1; ./compile em_real"
  ls -l main/wrf.exe main/real.exe
fi

if [[ ! -x main/wrf.exe ]]; then
  echo "WRF compile failed — see $LOG"
  exit 1
fi

# --- WPS 4.6.0 ---
cd "$ROOT/src"
if [[ ! -d WPS/ungrib ]]; then
  echo "=== downloading WPS 4.6.0 ==="
  wget -c -O WPSV4.6.0.tar.gz https://github.com/wrf-model/WPS/archive/v4.6.0.tar.gz
  rm -rf WPS WPS-4.6.0
  tar -xzf WPSV4.6.0.tar.gz
  mv WPS-4.6.0 WPS
fi

cd "$ROOT/src/WPS"
while IFS= read -r -d '' f; do
  sed -i "1s|^#!.*csh.*|#!$ROOT/opt/bin/csh -f|" "$f"
done < <(grep -rlZ '^#!.*csh' . --include='*' 2>/dev/null || true)

export WRF_DIR="$ROOT/src/WRF"
if [[ ! -f configure.wps ]]; then
  echo "=== configuring WPS (GNU serial GRIB2) ==="
  # Prefer built-in grib2 libs if the flag exists; else option 3 + our Jasper
  if ./configure --help 2>&1 | grep -q build-grib2; then
    echo 1 | ./configure --build-grib2-libs
  else
    echo 3 | ./configure
  fi
  sed -i "s|^WRF_DIR *=.*|WRF_DIR = $ROOT/src/WRF|" configure.wps
  sed -i "s|^SFC *=.*|SFC = gfortran|" configure.wps || true
  sed -i "s|^SCC *=.*|SCC = gcc|" configure.wps || true
  if ! grep -q -- '-lnetcdff' configure.wps; then
    sed -i 's/-lnetcdf/-lnetcdff -lnetcdf/g' configure.wps || true
  fi
fi

if [[ ! -x geogrid.exe || ! -x ungrib.exe || ! -x metgrid.exe ]]; then
  echo "=== compiling WPS ==="
  "$ROOT/opt/bin/csh" -c "setenv J 4; setenv WRF_DIR $ROOT/src/WRF; setenv NETCDF $NETCDF; setenv JASPERLIB $JASPERLIB; setenv JASPERINC $JASPERINC; ./compile"
  ls -l geogrid.exe ungrib.exe metgrid.exe
fi

echo "=== $(date -u +%FT%TZ) compile done ==="
ls -l "$ROOT/src/WRF/main/wrf.exe" "$ROOT/src/WRF/main/real.exe" \
      "$ROOT/src/WPS/geogrid.exe" "$ROOT/src/WPS/ungrib.exe" "$ROOT/src/WPS/metgrid.exe"
