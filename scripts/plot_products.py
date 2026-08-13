#!/usr/bin/env python3
"""Plot WRF products as georeferenced transparent PNG overlays.

Real wrfout frames need conda env wrf-post (wrf-python, matplotlib).
Placeholder frames are synthetic rasters (matplotlib only) so S3 / Pages
can be tested before WRF is compiled:

    python3 scripts/plot_products.py --placeholder --out-dir plots/CYCLE --cycle YYYYMMDDT12z

Overlays have no titles, axes, or colorbars — the Pages Leaflet map supplies
the basemap and HTML legend. Bounds are written to meta.json as
[[south, west], [north, east]] for latest.json / L.imageOverlay.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from datetime import datetime
from glob import glob
from pathlib import Path

import numpy as np

KPHX = (33.4342, -112.0116)
# config/namelist.wps — Lambert tangent at domain center.
REF_LAT = 33.45
REF_LON = -112.07
TRUELAT1 = 33.45
TRUELAT2 = 33.45
STAND_LON = -112.07
E_WE = 201
E_SN = 201
DX_M = 1000.0
# WPS constants_module spherical radius (module_map_utils.F).
EARTH_RADIUS_M = 6370000.0
DOMAIN_KM = (E_WE - 1) * DX_M / 1000.0  # 200 km for e_we=201, dx=1000
KM_PER_DEG_LAT = 111.32

# Keep these hex lists in lockstep with web/app.js LEGENDS.
# Reflectivity: NWS 88D / NCEP WDSS-II 5 dBZ steps (5–75).
REFL_LEVELS = np.arange(5, 85, 5)
REFL_COLORS = [
    "#00ecec",
    "#01a0f6",
    "#0100f6",
    "#00ff00",
    "#00c800",
    "#009000",
    "#ffff00",
    "#e7c000",
    "#ff9000",
    "#ff0000",
    "#d60000",
    "#c00000",
    "#ff00ff",
    "#9955c9",
    "#ffffff",
]
# NWS QPE-style 1-hour precip (inches).
PRECIP_LEVELS = np.array([0.01, 0.10, 0.25, 0.50, 0.75, 1.00, 1.50, 2.00, 2.50, 3.00])
PRECIP_COLORS = [
    "#98fb98",
    "#00ee00",
    "#009b00",
    "#ffff4d",
    "#ffcc00",
    "#ff7a00",
    "#ff0000",
    "#b00000",
    "#ff00ff",
]
# Meteorological 2 m temp (°F), blue–gold–red (no rainbow / no near-white hinge).
T2_LEVELS = np.arange(50, 125, 5)
T2_COLORS = [
    "#2166ac",
    "#4393c3",
    "#74add1",
    "#9dc1d9",
    "#c5b56a",
    "#e8d070",
    "#f4b942",
    "#ee8f2a",
    "#e0691e",
    "#d04527",
    "#b91c1c",
    "#991b1b",
    "#7f1d1d",
    "#450a0a",
]
# 10 m wind / gust (kt): sand → copper → wine.
WIND_LEVELS = np.array([0, 5, 10, 15, 20, 25, 30, 35, 40, 55], dtype=float)
WIND_COLORS = [
    "#cfc4b0",
    "#d4b07a",
    "#c99050",
    "#c4784a",
    "#b45a32",
    "#9a3c28",
    "#7e2828",
    "#641828",
    "#3e1018",
]
# MUCAPE discrete meteorological bins (J kg⁻¹).
CAPE_LEVELS = np.array([0, 500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 5000], dtype=float)
CAPE_COLORS = [
    "#d4c48a",
    "#f0d060",
    "#f0a830",
    "#e07820",
    "#c85020",
    "#a82828",
    "#8c1838",
    "#6e1048",
    "#4a0a5c",
]

DESK_INK = "#0e1114"
DESK_PANEL = "#14181c"
DESK_PAPER = "#e8e0d4"
DESK_MUTED = "#9a8f82"
DESK_LINE = "#3a342e"
DESK_COPPER = "#c4784a"
DESK_SAGE = "#6a8a62"
DESK_SLATE = "#7a8fa0"

PRODUCTS = ["refl", "precip", "t2", "wind", "cape", "meteogram"]


def km_per_deg_lon(lat: float = REF_LAT) -> float:
    return KM_PER_DEG_LAT * math.cos(math.radians(lat))


def _wrf_lc_ij_to_ll(i, j):
    """WRF/WPS Lambert conformal (i,j) → lat/lon. Mass-grid i=1..e_we-1.

    Matches WPS v4.6 geogrid module_map_utils (set_lc / ijll_lc). knowni/j
    default to the domain center (e_we/2, e_sn/2), same as namelist ref_lat/lon.
    """
    i = np.asarray(i, dtype=float)
    j = np.asarray(j, dtype=float)
    hemi = 1.0 if TRUELAT1 >= 0.0 else -1.0
    rad = math.pi / 180.0
    deg = 180.0 / math.pi
    if abs(TRUELAT1 - TRUELAT2) > 0.1:
        cone = math.log10(math.cos(TRUELAT1 * rad)) - math.log10(math.cos(TRUELAT2 * rad))
        cone /= math.log10(math.tan((45.0 - abs(TRUELAT1) / 2.0) * rad)) - math.log10(
            math.tan((45.0 - abs(TRUELAT2) / 2.0) * rad)
        )
    else:
        cone = math.sin(abs(TRUELAT1) * rad)
    knowni = E_WE / 2.0
    knownj = E_SN / 2.0
    rebydx = EARTH_RADIUS_M / DX_M
    deltalon1 = REF_LON - STAND_LON
    if deltalon1 > 180.0:
        deltalon1 -= 360.0
    if deltalon1 < -180.0:
        deltalon1 += 360.0
    ctl1r = math.cos(TRUELAT1 * rad)
    rsw = (
        rebydx
        * ctl1r
        / cone
        * (
            math.tan((90.0 * hemi - REF_LAT) * rad / 2.0)
            / math.tan((90.0 * hemi - TRUELAT1) * rad / 2.0)
        )
        ** cone
    )
    arg = cone * (deltalon1 * rad)
    polei = hemi * knowni - hemi * rsw * math.sin(arg)
    polej = hemi * knownj + rsw * math.cos(arg)
    chi1 = (90.0 - hemi * TRUELAT1) * rad
    chi2 = (90.0 - hemi * TRUELAT2) * rad
    xx = hemi * i - polei
    yy = polej - hemi * j
    r2 = xx * xx + yy * yy
    r = np.sqrt(r2) / rebydx
    lon = STAND_LON + deg * np.arctan2(hemi * xx, yy) / cone
    lon = np.mod(lon + 360.0, 360.0)
    if chi1 == chi2:
        chi = 2.0 * np.arctan((r / math.tan(chi1)) ** (1.0 / cone) * math.tan(chi1 * 0.5))
    else:
        chi = 2.0 * np.arctan(
            (r * cone / math.sin(chi1)) ** (1.0 / cone) * math.tan(chi1 * 0.5)
        )
    lat = (90.0 - chi * deg) * hemi
    pole = r2 == 0.0
    lat = np.where(pole, hemi * 90.0, lat)
    lon = np.where(pole, STAND_LON, lon)
    lon = np.where(lon > 180.0, lon - 360.0, lon)
    lon = np.where(lon < -180.0, lon + 360.0, lon)
    return lat, lon


def domain_bounds_from_center() -> list[list[float]]:
    """Axis-aligned lat/lon box of the Lambert mass grid (wrfout XLAT/XLONG)."""
    ii, jj = np.meshgrid(
        np.arange(1, E_WE, dtype=float),
        np.arange(1, E_SN, dtype=float),
    )
    lats, lons = _wrf_lc_ij_to_ll(ii, jj)
    return domain_bounds_from_grid(lats, lons)


def domain_bounds_from_grid(lats, lons) -> list[list[float]]:
    return [
        [round(float(np.min(lats)), 5), round(float(np.min(lons)), 5)],
        [round(float(np.max(lats)), 5), round(float(np.max(lons)), 5)],
    ]


def overlay_figsize(bounds: list[list[float]]) -> tuple[float, float]:
    (south, west), (north, east) = bounds
    lat_span = max(north - south, 1e-6)
    lon_span = max(east - west, 1e-6)
    height = 6.0
    return (height * (lon_span / lat_span), height)


def _binned_cmap(colors, levels):
    from matplotlib.colors import BoundaryNorm, ListedColormap

    cmap = ListedColormap(list(colors))
    cmap.set_bad((0, 0, 0, 0))
    cmap.set_under((0, 0, 0, 0))
    cmap.set_over(colors[-1])
    return cmap, BoundaryNorm(levels, ncolors=len(colors), clip=True)


def nws_refl_cmap():
    return _binned_cmap(REFL_COLORS, REFL_LEVELS)


def nws_qpe_cmap():
    return _binned_cmap(PRECIP_COLORS, PRECIP_LEVELS)


def t2_cmap():
    return _binned_cmap(T2_COLORS, T2_LEVELS)


def wind_cmap():
    return _binned_cmap(WIND_COLORS, WIND_LEVELS)


def cape_cmap():
    return _binned_cmap(CAPE_COLORS, CAPE_LEVELS)


def _transparent_cmap(cmap):
    import matplotlib.pyplot as plt

    if isinstance(cmap, str):
        cmap = plt.get_cmap(cmap).copy()
    elif hasattr(cmap, "copy"):
        cmap = cmap.copy()
    try:
        cmap.set_bad((0, 0, 0, 0))
    except Exception:
        pass
    return cmap


def save_overlay(
    path: Path,
    lons,
    lats,
    data,
    bounds: list[list[float]],
    *,
    cmap="turbo",
    vmin=None,
    vmax=None,
    norm=None,
    mask_below=None,
) -> None:
    """North-up Plate-Carree raster filling the figure; transparent outside data."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.ma.masked_invalid(np.asarray(data, dtype=float))
    if mask_below is not None:
        arr = np.ma.masked_less(arr, mask_below)

    cmap = _transparent_cmap(cmap)
    (south, west), (north, east) = bounds

    fig = plt.figure(figsize=overlay_figsize(bounds), frameon=False)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_facecolor("none")
    fig.patch.set_facecolor("none")

    regular = (
        getattr(lons, "ndim", 1) == 2
        and getattr(lats, "ndim", 1) == 2
        and lats.shape == arr.shape
        and lons.shape == arr.shape
        and np.allclose(lats[:, 0], lats[:, -1])
        and np.allclose(lons[0, :], lons[-1, :])
    )
    im_kw = {"cmap": cmap, "interpolation": "bilinear", "aspect": "auto"}
    if norm is not None:
        im_kw["norm"] = norm
    else:
        im_kw["vmin"] = vmin
        im_kw["vmax"] = vmax
    if regular:
        ax.imshow(arr, origin="lower", extent=[west, east, south, north], **im_kw)
    else:
        mesh_kw = {"cmap": cmap, "shading": "nearest", "antialiased": False}
        if norm is not None:
            mesh_kw["norm"] = norm
        else:
            mesh_kw["vmin"] = vmin
            mesh_kw["vmax"] = vmax
        ax.pcolormesh(lons, lats, arr, **mesh_kw)
    fig.savefig(
        path,
        dpi=120,
        transparent=True,
        facecolor="none",
        edgecolor="none",
        pad_inches=0,
    )
    plt.close(fig)


def dewpoint_c(q2, psfc):
    e = q2 * psfc / (0.622 + q2)
    e = np.maximum(e, 1.0)
    td = 243.5 * np.log(e / 611.2) / (17.67 - np.log(e / 611.2))
    return td


def nearest_ij(lats, lons, lat, lon):
    dist = (lats - lat) ** 2 + (lons - lon) ** 2
    idx = np.unravel_index(np.nanargmin(dist), dist.shape)
    return int(idx[0]), int(idx[1])


def write_meta(out_root: Path, cycle: str, frames: int, bounds, **extra) -> dict:
    (south, west), (north, east) = bounds
    meta = {
        "cycle": cycle,
        "frames": frames,
        "products": PRODUCTS,
        "bounds": [[south, west], [north, east]],
        "center": [REF_LAT, REF_LON],
        "ref_lat": REF_LAT,
        "ref_lon": REF_LON,
        "domain_km": DOMAIN_KM,
    }
    meta.update(extra)
    (out_root / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def _mesh(bounds: list[list[float]], n: int = E_WE):
    (south, west), (north, east) = bounds
    lats = np.linspace(south, north, n)
    lons = np.linspace(west, east, n)
    lon2d, lat2d = np.meshgrid(lons, lats)
    return lat2d, lon2d


def _dist_km(lat2d, lon2d, lat0, lon0):
    return np.hypot((lat2d - lat0) * KM_PER_DEG_LAT, (lon2d - lon0) * km_per_deg_lon())


def _storm_center(fxx: int) -> tuple[float, float]:
    """Placeholder cell drifts NE across the valley through the 18 h cycle."""
    return 32.78 + 0.055 * fxx, -112.72 + 0.062 * fxx


def _synth_fields(lat2d, lon2d, fxx: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(12 + fxx)
    slat, slon = _storm_center(fxx)
    d = _dist_km(lat2d, lon2d, slat, slon)
    core = np.exp(-((d / 22.0) ** 2))
    ring = np.exp(-(((d - 38.0) / 16.0) ** 2))
    noise = 0.12 * rng.random(lat2d.shape)
    dbz = np.clip(62 * core + 28 * ring + 8 * noise, 0, 75)

    precip = np.clip(1.6 * core + 0.35 * ring, 0, 3.0)
    precip = np.where(dbz >= 18, precip, 0.0)

    # 12Z = 05:00 MST; afternoon peak around F10 (15:00 MST).
    diurnal = 78 + 28 * math.sin(math.pi * max(fxx - 1, 0) / 14.0)
    t2 = (
        diurnal
        - 6.5 * (lat2d - REF_LAT) / 1.35
        + 4.0 * np.exp(-(_dist_km(lat2d, lon2d, REF_LAT, REF_LON) / 45.0) ** 2)
        - 8.0 * core
        + rng.normal(0, 0.4, lat2d.shape)
    )

    gust = 8 + 28 * core + 12 * ring + 4 * rng.random(lat2d.shape)
    cape = np.clip(
        900
        + 2200 * math.sin(math.pi * max(fxx, 0) / 16.0)
        - 400 * (lat2d - 32.9)
        + 1800 * core
        + rng.normal(0, 40, lat2d.shape),
        0,
        4500,
    )
    return {"refl": dbz, "precip": precip, "t2": t2, "wind": gust, "cape": cape}


def _placeholder_meteogram(path: Path, cycle: str, hours: int) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.arange(hours + 1)
    t2 = 78 + 28 * np.sin(np.pi * np.maximum(x - 1, 0) / 14.0)
    td = t2 - 22 + 4 * np.sin(x / 3.0)
    wind = 8 + 10 * np.sin(x / 4.0) ** 2
    precip = np.where((x >= 8) & (x <= 14), 0.08 * np.exp(-(((x - 11) / 2.4) ** 2)), 0.0)

    fig, axes = plt.subplots(3, 1, figsize=(9.2, 6.4), sharex=True, facecolor=DESK_INK)
    for ax in axes:
        ax.set_facecolor(DESK_PANEL)
        ax.tick_params(colors=DESK_PAPER)
        ax.yaxis.label.set_color(DESK_PAPER)
        for spine in ax.spines.values():
            spine.set_color(DESK_LINE)
        ax.grid(True, alpha=0.22, color=DESK_MUTED)

    axes[0].plot(x, t2, color=DESK_COPPER, lw=2, label="T 2 m")
    axes[0].plot(x, td, color=DESK_SLATE, lw=2, label="Td 2 m")
    axes[0].set_ylabel("°F")
    axes[0].legend(loc="upper left", fontsize=8, facecolor=DESK_INK, edgecolor=DESK_LINE, labelcolor=DESK_PAPER)
    axes[0].set_title(f"KPHX meteogram — placeholder {cycle}", color=DESK_PAPER, fontsize=12)
    axes[1].plot(x, wind, color=DESK_SAGE, lw=2, label="10 m wind")
    axes[1].plot(x, wind + 6, color=DESK_COPPER, lw=2, label="gust")
    axes[1].set_ylabel("kt")
    axes[1].legend(loc="upper left", fontsize=8, facecolor=DESK_INK, edgecolor=DESK_LINE, labelcolor=DESK_PAPER)
    axes[2].bar(x, precip, color="#3cdb3c", label="1-h precip")
    axes[2].plot(x, np.cumsum(precip), color=DESK_PAPER, label="accumulated")
    axes[2].set_ylabel("inches")
    axes[2].set_xlabel("forecast hour  (12Z cycle, MST = UTC−7)", color=DESK_MUTED)
    axes[2].legend(loc="upper left", fontsize=8, facecolor=DESK_INK, edgecolor=DESK_LINE, labelcolor=DESK_PAPER)
    axes[2].set_xticks(x[::2])
    axes[2].set_xticklabels([f"F{i:02d}" for i in x[::2]], color=DESK_PAPER)
    fig.tight_layout()
    fig.savefig(path, dpi=120, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def plot_placeholder(out_root: Path, cycle: str, hours: int) -> None:
    """Synthetic georeferenced overlays matching the S3 layout (no wrfout)."""
    bounds = domain_bounds_from_center()
    lat2d, lon2d = _mesh(bounds)
    nframes = hours + 1
    jobs = [
        ("refl", "refl", nws_refl_cmap(), 5),
        ("precip", "precip", nws_qpe_cmap(), 0.01),
        ("t2", "t2", t2_cmap(), None),
        ("wind", "wind", wind_cmap(), None),
        ("cape", "cape", cape_cmap(), None),
    ]

    for i in range(nframes):
        fxx = f"f{i:02d}"
        fields = _synth_fields(lat2d, lon2d, i)
        for prod, key, (cmap, norm), mask in jobs:
            save_overlay(
                out_root / prod / f"{fxx}.png",
                lon2d,
                lat2d,
                fields[key],
                bounds,
                cmap=cmap,
                norm=norm,
                mask_below=mask,
            )

    meteo = out_root / "meteogram" / "f00.png"
    _placeholder_meteogram(meteo, cycle, hours)
    shutil.copyfile(meteo, out_root / "meteogram" / "kphx.png")
    write_meta(out_root, cycle, nframes, bounds, placeholder=True)
    print(f"Wrote {nframes} overlay frames under {out_root}")
    print(f"bounds={bounds}")


def cycle_start(cycle: str) -> datetime:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{1,2})z$", cycle, re.I)
    if not m:
        raise ValueError(f"bad cycle {cycle}")
    return datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]))


def wrfout_hour(path: Path, start: datetime) -> int | None:
    m = re.search(r"wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[:_](\d{2})[:_](\d{2})", path.name)
    if not m:
        return None
    t = datetime.strptime(f"{m[1]} {m[2]}:{m[3]}:{m[4]}", "%Y-%m-%d %H:%M:%S")
    return int(round((t - start).total_seconds() / 3600.0))


def list_wrfout_by_hour(wrfout_dir: Path, cycle: str) -> dict[int, Path]:
    start = cycle_start(cycle)
    out: dict[int, Path] = {}
    for fn in sorted(glob(str(Path(wrfout_dir) / "wrfout_d01_*"))):
        p = Path(fn)
        h = wrfout_hour(p, start)
        if h is not None:
            out[h] = p
    return out


def overlays_exist(out_root: Path, hour: int) -> bool:
    fxx = f"f{hour:02d}.png"
    return all((out_root / prod / fxx).exists() for prod in ("refl", "precip", "t2", "wind", "cape"))


def _write_meteogram(out_root: Path, series: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not series["times"]:
        return
    fig, axes = plt.subplots(3, 1, figsize=(10, 7.2), sharex=True, facecolor=DESK_INK)
    x = np.arange(len(series["times"]))
    for ax in axes:
        ax.set_facecolor(DESK_PANEL)
        ax.tick_params(colors=DESK_PAPER)
        ax.yaxis.label.set_color(DESK_PAPER)
        for spine in ax.spines.values():
            spine.set_color(DESK_LINE)
        ax.grid(True, alpha=0.22, color=DESK_MUTED)
    axes[0].plot(x, series["t2f"], color=DESK_COPPER, lw=2, label="T 2 m")
    axes[0].plot(x, series["tdf"], color=DESK_SLATE, lw=2, label="Td 2 m")
    axes[0].set_ylabel("°F")
    axes[0].legend(loc="upper right", fontsize=8, facecolor=DESK_INK, edgecolor=DESK_LINE, labelcolor=DESK_PAPER)
    axes[0].set_title("KPHX meteogram — Phoenix 1 km WRF", color=DESK_PAPER, fontsize=12)
    axes[1].plot(x, series["wspd"], color=DESK_SAGE, lw=2, label="10 m wind")
    axes[1].plot(x, series["gust"], color=DESK_COPPER, lw=2, label="gust")
    axes[1].set_ylabel("kt")
    axes[1].legend(loc="upper right", fontsize=8, facecolor=DESK_INK, edgecolor=DESK_LINE, labelcolor=DESK_PAPER)
    axes[2].bar(x, series["precip_hour"], color="#3cdb3c", label="1-h precip")
    axes[2].plot(x, series["precip_acc"], color=DESK_PAPER, label="accumulated")
    axes[2].set_ylabel("inches")
    axes[2].legend(loc="upper right", fontsize=8, facecolor=DESK_INK, edgecolor=DESK_LINE, labelcolor=DESK_PAPER)
    labels = [t[5:16] for t in series["times"]]
    axes[2].set_xticks(x[:: max(1, len(x) // 10)])
    axes[2].set_xticklabels(labels[:: max(1, len(x) // 10)], rotation=30, ha="right", color=DESK_PAPER)
    fig.tight_layout()
    meteo = out_root / "meteogram" / "f00.png"
    meteo.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(meteo, dpi=120, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    shutil.copyfile(meteo, out_root / "meteogram" / "kphx.png")


def plot_wrfout(
    wrfout_dir: Path,
    out_root: Path,
    cycle: str,
    only_hours: set[int] | None = None,
    skip_existing: bool = False,
) -> list[int]:
    """Plot wrfout frames. Hour index comes from the valid time vs cycle start.

    only_hours: if set, write overlays only for those forecast hours (meteogram
    still uses every readable wrfout so precip deltas stay correct).
    """
    import matplotlib

    matplotlib.use("Agg")
    from netCDF4 import Dataset
    from wrf import getvar, latlon_coords, to_np

    by_hour = list_wrfout_by_hour(wrfout_dir, cycle)
    if not by_hour:
        raise SystemExit(f"No wrfout files in {wrfout_dir}")

    rain_prev = None
    series = {
        "times": [],
        "t2f": [],
        "tdf": [],
        "wspd": [],
        "gust": [],
        "precip_hour": [],
        "precip_acc": [],
    }
    bounds = None
    refl_cmap, refl_norm = nws_refl_cmap()
    precip_cmap, precip_norm = nws_qpe_cmap()
    t2_cm, t2_norm = t2_cmap()
    wind_cm, wind_norm = wind_cmap()
    cape_cm, cape_norm = cape_cmap()
    plotted: list[int] = []

    for h in sorted(by_hour):
        fn = by_hour[h]
        nc = Dataset(fn)
        tstr = "".join(c.decode() if isinstance(c, bytes) else c for c in nc.variables["Times"][0])
        valid = tstr.replace("_", " ")
        fxx = f"f{h:02d}"

        lats, lons = latlon_coords(getvar(nc, "T2"))
        lats = to_np(lats)
        lons = to_np(lons)
        if bounds is None:
            bounds = domain_bounds_from_grid(lats, lons)

        t2 = to_np(getvar(nc, "T2"))
        t2f = t2 * 9 / 5 - 459.67
        q2 = to_np(getvar(nc, "Q2"))
        psfc = to_np(getvar(nc, "PSFC"))
        td_c = dewpoint_c(q2, psfc)
        tdf = td_c * 9 / 5 + 32

        u10 = to_np(getvar(nc, "U10"))
        v10 = to_np(getvar(nc, "V10"))
        wspd = np.sqrt(u10**2 + v10**2) * 1.94384
        gust = None
        if "WSPD10MAX" in nc.variables:
            gust = to_np(nc.variables["WSPD10MAX"][0]) * 1.94384

        rainc = to_np(nc.variables["RAINC"][0]) if "RAINC" in nc.variables else 0
        rainnc = to_np(nc.variables["RAINNC"][0]) if "RAINNC" in nc.variables else 0
        acc = np.array(rainc + rainnc, dtype=float)
        precip = acc * 0.0 if rain_prev is None else np.maximum(acc - rain_prev, 0)
        rain_prev = acc
        speed = gust if gust is not None else wspd

        j, iidx = nearest_ij(lats, lons, KPHX[0], KPHX[1])
        series["times"].append(valid)
        series["t2f"].append(float(t2f[j, iidx]))
        series["tdf"].append(float(tdf[j, iidx]))
        series["wspd"].append(float(wspd[j, iidx]))
        series["gust"].append(float(speed[j, iidx]))
        series["precip_hour"].append(float(precip[j, iidx] / 25.4))
        series["precip_acc"].append(float(acc[j, iidx] / 25.4))

        want = only_hours is None or h in only_hours
        if want and skip_existing and overlays_exist(out_root, h):
            plotted.append(h)
            nc.close()
            continue
        if not want:
            nc.close()
            continue

        try:
            dbz = to_np(getvar(nc, "mdbz"))
        except Exception:
            dbz = np.full_like(t2, np.nan)

        try:
            cape2d = getvar(nc, "cape_2d")
            mucape = to_np(cape2d[0])
        except Exception:
            if "AFWA_CAPE" in nc.variables:
                mucape = to_np(nc.variables["AFWA_CAPE"][0])
            else:
                mucape = np.full_like(t2, np.nan)

        save_overlay(
            out_root / "refl" / f"{fxx}.png",
            lons,
            lats,
            dbz,
            bounds,
            cmap=refl_cmap,
            norm=refl_norm,
            mask_below=5,
        )
        save_overlay(
            out_root / "precip" / f"{fxx}.png",
            lons,
            lats,
            precip / 25.4,
            bounds,
            cmap=precip_cmap,
            norm=precip_norm,
            mask_below=0.01,
        )
        save_overlay(
            out_root / "t2" / f"{fxx}.png",
            lons,
            lats,
            t2f,
            bounds,
            cmap=t2_cm,
            norm=t2_norm,
        )
        save_overlay(
            out_root / "wind" / f"{fxx}.png",
            lons,
            lats,
            speed,
            bounds,
            cmap=wind_cm,
            norm=wind_norm,
        )
        save_overlay(
            out_root / "cape" / f"{fxx}.png",
            lons,
            lats,
            mucape,
            bounds,
            cmap=cape_cm,
            norm=cape_norm,
        )
        plotted.append(h)
        nc.close()

    _write_meteogram(out_root, series)
    if bounds is None:
        bounds = domain_bounds_from_center()
    write_meta(out_root, cycle, len(by_hour), bounds, placeholder=False, kphx=series)
    print(f"Wrote {len(plotted)} overlay frames under {out_root} (hours={plotted})")
    print(f"bounds={bounds}")
    return plotted


def open_times(wrfout_dir: Path):
    files = sorted(glob(str(wrfout_dir / "wrfout_d01_*")))
    if not files:
        raise SystemExit(f"No wrfout files in {wrfout_dir}")
    return files


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wrfout-dir", help="Directory of wrfout_d01_* files")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--cycle", required=True, help="YYYYMMDDTHHz")
    p.add_argument("--hours", type=int, default=18)
    p.add_argument("--only-hours", default=None, help="Comma-separated forecast hours to overlay")
    p.add_argument("--skip-existing", action="store_true")
    p.add_argument(
        "--placeholder",
        action="store_true",
        help="Write synthetic overlay PNGs (no wrfout / wrf-python)",
    )
    args = p.parse_args()

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    if args.placeholder:
        plot_placeholder(out_root, args.cycle, args.hours)
        return
    if not args.wrfout_dir:
        raise SystemExit("--wrfout-dir is required unless --placeholder")
    only = None
    if args.only_hours:
        only = {int(x.strip()) for x in args.only_hours.split(",") if x.strip()}
    plot_wrfout(Path(args.wrfout_dir), out_root, args.cycle, only_hours=only, skip_existing=args.skip_existing)


if __name__ == "__main__":
    main()
