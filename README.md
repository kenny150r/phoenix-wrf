# Phoenix 1 km WRF monsoon forecast

Daily 18-hour WRF run over Phoenix on this machine, initialized from the **12Z HRRR**. Maps and a KPHX meteogram are published to GitHub Pages; PNG frames live on S3 for 14 days. `wrfout` stays local for 48 hours.

- Viewer: https://kenny150r.github.io/phoenix-wrf/
- Domain: 301×301 at 1 km, `ref_lat=33.45`, `ref_lon=-112.07`
- Physics: Thompson MP, MYNN PBL, RRTMG, no cumulus, `time_step=6`, 4 MPI ranks
- Cycle: systemd timer at **14:20 UTC** (07:20 MST)

## Layout

| Path | Purpose |
| --- | --- |
| `scripts/run_forecast.sh` | Daily driver (download → WPS → real → wrf → plot → S3 → purge) |
| `scripts/compile_wrf.sh` | WRF 4.6.1 + WPS 4.6.0 GNU dmpar (no Anaconda MPI) |
| `config/` | namelists, iofields, HRRR Vtable |
| `web/` | Static time-slider Pages app |
| `src/WRF`, `src/WPS` | Model builds (not in git) |
| `geog/` | WPS_GEOG high-res subset (not in git) |
| `data/wrfout/` | Local netCDF, 48 h |

## Commands

```bash
source /home/kenny/phoenix-wrf/env.sh          # WRF compilers / OpenMPI, no conda
bash scripts/compile_wrf.sh
bash scripts/run_forecast.sh --hours 1         # smoke test
bash scripts/run_forecast.sh                   # full 18 h 12Z cycle
bash scripts/install_systemd.sh
```

Python post-processing uses conda env `wrf-post` (`herbie-data`, `wrf-python`, `cartopy`, `boto3`).

## Notes

Passwordless sudo is not available on this host, so gfortran/OpenMPI/netCDF-Fortran were extracted from Ubuntu debs into `opt/prefix` instead of `apt install`. Preferred packages if sudo is enabled later:

`gfortran gfortran-11 m4 csh tcsh libopenmpi-dev openmpi-bin libnetcdf-dev libnetcdff-dev netcdf-bin libhdf5-dev libpng-dev zlib1g-dev libjpeg-turbo8-dev libevent-2.1-7 build-essential`
