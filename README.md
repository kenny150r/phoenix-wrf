# Phoenix 1 km WRF monsoon forecast

Daily 18-hour WRF run over Phoenix on this machine, initialized from the **12Z HRRR**. Maps and a KPHX meteogram are published to GitHub Pages; PNG frames live on S3 for 14 days. `wrfout` stays local for 48 hours.

- Viewer: https://kenny150r.github.io/phoenix-wrf/ (full-page Leaflet map, forecast fields as georeferenced overlays)
- Domain: 201×201 at 1 km (~200 km), `ref_lat=33.45`, `ref_lon=-112.07`, Lambert
- Physics: Thompson MP, MYNN PBL, RRTMG, no cumulus, `time_step=6`, 4 MPI ranks
- Cycle: systemd timer at **14:20 UTC** (07:20 MST)

## Layout

| Path | Purpose |
| --- | --- |
| `scripts/run_forecast.sh` | Daily driver (download → WPS → real → wrf → plot → S3 → purge) |
| `scripts/compile_wrf.sh` | WRF 4.6.1 + WPS 4.6.0 GNU dmpar (no Anaconda MPI) |
| `config/` | namelists, iofields, HRRR Vtable |
| `web/` | Full-page Leaflet map (GitHub Pages) |
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

## Viewer and S3

Static GitHub Pages app in `web/`: a full-viewport Leaflet map with floating product/time/legend controls. Forecast fields are transparent georeferenced PNG overlays (`L.imageOverlay`) placed with `bounds` from `latest.json`. The KPHX meteogram is a chart panel, not a spatial overlay. Frames are loaded from S3, not stored in the repo.

- Site: https://kenny150r.github.io/phoenix-wrf/
- Bucket: `s3://phx-wrf-forecast` (`us-east-1`), public `GetObject` on `latest.json` + `runs/*`, CORS for `https://kenny150r.github.io`, lifecycle expire `runs/` after 14 days
- Object layout: `s3://phx-wrf-forecast/runs/YYYYMMDDTHHz/{refl,precip,t2,wind,cape,meteogram}/fXX.png` plus `s3://phx-wrf-forecast/latest.json`
- Live status: `latest.json` is polled every 20s (`status`, `stage`, `stage_label`, `wrf_hour_done`, `hours_available`, `updated_at`). During `wrf.exe` a watcher plots each `wrfout` (`frames_per_outfile=1`) and uploads that hour’s PNGs so the slider can enable hours as they appear.

```bash
export AWS_EC2_METADATA_DISABLED=true   # this desktop is not EC2
bash scripts/setup_s3.sh
# After a WRF cycle (conda env wrf-post):
python scripts/plot_products.py --wrfout-dir data/wrfout/CYCLE --out-dir plots/CYCLE --cycle CYCLE
python scripts/upload_s3.py --run-dir plots/CYCLE --cycle CYCLE --hours 18
# Before WRF exists, labeled placeholders:
python3 scripts/plot_products.py --placeholder --out-dir plots/CYCLE --cycle CYCLE --hours 18
python3 scripts/upload_s3.py --run-dir plots/CYCLE --cycle CYCLE --hours 18 --status placeholder
```

## Notes

Passwordless sudo is not available on this host, so gfortran/OpenMPI/netCDF-Fortran were extracted from Ubuntu debs into `opt/prefix` instead of `apt install`. Preferred packages if sudo is enabled later:

`gfortran gfortran-11 m4 csh tcsh libopenmpi-dev openmpi-bin libnetcdf-dev libnetcdff-dev netcdf-bin libhdf5-dev libpng-dev zlib1g-dev libjpeg-turbo8-dev libevent-2.1-7 build-essential`
