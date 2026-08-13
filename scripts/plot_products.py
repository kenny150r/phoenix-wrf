#!/usr/bin/env python3
"""Plot WRF products to PNG frames for S3 / GitHub Pages.

Real wrfout frames need conda env wrf-post (wrf-python, cartopy, matplotlib).
Placeholder frames work with ImageMagick `convert` so publishing can be
tested before WRF is compiled:

    python3 scripts/plot_products.py --placeholder --out-dir plots/CYCLE --cycle YYYYMMDDT12z
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from glob import glob
from pathlib import Path

import numpy as np

KPHX = (33.4342, -112.0116)
CITIES = {
    "Phoenix": (33.45, -112.07),
    "Tucson": (32.22, -110.97),
    "Flagstaff": (35.20, -111.65),
    "Yuma": (32.69, -114.63),
    "Prescott": (34.54, -112.47),
}

REFL_LEVELS = np.arange(0, 80, 5)
REFL_COLORS = [
    "#00ffff",
    "#00b0f0",
    "#0070ff",
    "#00ff00",
    "#00c000",
    "#008000",
    "#ffff00",
    "#ffc000",
    "#ff8000",
    "#ff0000",
    "#c00000",
    "#800000",
    "#ff00ff",
    "#c000c0",
    "#800080",
]

PRODUCTS = ["refl", "precip", "t2", "wind", "cape", "meteogram"]
PLACEHOLDER_COLORS = {
    "refl": ("#06243a", "#7fe0ff", "Simulated composite reflectivity"),
    "precip": ("#06281f", "#9ee7c2", "1-hour / accumulated precip"),
    "t2": ("#3a1c08", "#ffcc80", "2 m temperature"),
    "wind": ("#2a2208", "#ffe082", "10 m wind + gusts"),
    "cape": ("#2a0810", "#ff8a80", "Most-unstable CAPE"),
    "meteogram": ("#121826", "#c5cae9", "KPHX meteogram (T, Td, wind, precip)"),
}


def nws_refl_cmap():
    from matplotlib.colors import BoundaryNorm, ListedColormap

    return ListedColormap(REFL_COLORS), BoundaryNorm(REFL_LEVELS, len(REFL_COLORS))


def open_times(wrfout_dir: Path):
    files = sorted(glob(str(wrfout_dir / "wrfout_d01_*")))
    if not files:
        raise SystemExit(f"No wrfout files in {wrfout_dir}")
    return files


def basemap(ax, ccrs, cfeature):
    ax.add_feature(cfeature.STATES.with_scale("10m"), linewidth=0.6, edgecolor="#333")
    ax.add_feature(cfeature.BORDERS.with_scale("10m"), linewidth=0.4)
    ax.coastlines(resolution="10m", linewidth=0.4)
    for name, (lat, lon) in CITIES.items():
        ax.plot(lon, lat, "k.", markersize=4, transform=ccrs.PlateCarree())
        ax.text(
            lon + 0.05,
            lat + 0.05,
            name,
            transform=ccrs.PlateCarree(),
            fontsize=7,
            color="#111",
        )


def save_fig(fig, plt, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_field(ccrs, plt, lats, lons, data, title, cbar_label, out, vmin=None, vmax=None, cmap="turbo", levels=None):
    import cartopy.feature as cfeature

    fig = plt.figure(figsize=(8.5, 7.2))
    ax = plt.axes(projection=ccrs.PlateCarree())
    ax.set_extent([float(np.min(lons)), float(np.max(lons)), float(np.min(lats)), float(np.max(lats))])
    if levels is not None:
        cf = ax.contourf(
            lons, lats, data, levels=levels, cmap=cmap, extend="both", transform=ccrs.PlateCarree()
        )
    else:
        cf = ax.pcolormesh(
            lons, lats, data, vmin=vmin, vmax=vmax, cmap=cmap, transform=ccrs.PlateCarree(), shading="auto"
        )
    basemap(ax, ccrs, cfeature)
    ax.set_title(title, fontsize=11)
    cb = plt.colorbar(cf, ax=ax, shrink=0.82, pad=0.02)
    cb.set_label(cbar_label)
    save_fig(fig, plt, out)


def dewpoint_c(q2, psfc):
    e = q2 * psfc / (0.622 + q2)
    e = np.maximum(e, 1.0)
    td = 243.5 * np.log(e / 611.2) / (17.67 - np.log(e / 611.2))
    return td


def nearest_ij(lats, lons, lat, lon):
    dist = (lats - lat) ** 2 + (lons - lon) ** 2
    idx = np.unravel_index(np.nanargmin(dist), dist.shape)
    return int(idx[0]), int(idx[1])


def _convert_label(path: Path, bg: str, fg: str, title: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    convert = shutil.which("convert")
    if not convert:
        raise SystemExit("ImageMagick `convert` is required for --placeholder on this host")
    caption = "\\n".join([title, *lines])
    subprocess.run(
        [
            convert,
            "-size",
            "960x720",
            f"xc:{bg}",
            "-gravity",
            "center",
            "-font",
            "DejaVu-Sans",
            "-pointsize",
            "28",
            "-fill",
            fg,
            "-annotate",
            "0",
            caption,
            str(path),
        ],
        check=True,
    )


def plot_placeholder(out_root: Path, cycle: str, hours: int) -> None:
    """Labeled PNG frames matching the S3 layout, no wrfout required."""
    nframes = hours + 1
    for prod, (bg, fg, title) in PLACEHOLDER_COLORS.items():
        if prod == "meteogram":
            dest = out_root / "meteogram" / "f00.png"
            _convert_label(
                dest,
                bg,
                fg,
                title,
                [f"cycle {cycle}", "T / Td / wind / precip", "awaiting first WRF run"],
            )
            shutil.copyfile(dest, out_root / "meteogram" / "kphx.png")
            continue
        for i in range(nframes):
            fxx = f"f{i:02d}"
            _convert_label(
                out_root / prod / f"{fxx}.png",
                bg,
                fg,
                title,
                [f"Phoenix 1 km WRF  {cycle}  {fxx.upper()}", "placeholder — no wrfout yet"],
            )
    meta = {
        "cycle": cycle,
        "frames": nframes,
        "products": PRODUCTS,
        "placeholder": True,
    }
    (out_root / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"Wrote {nframes} placeholder frames under {out_root}")


def plot_wrfout(wrfout_dir: Path, out_root: Path, cycle: str) -> None:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from netCDF4 import Dataset
    from wrf import getvar, latlon_coords, to_np

    files = open_times(wrfout_dir)
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

    for i, fn in enumerate(files):
        nc = Dataset(fn)
        tstr = "".join(c.decode() if isinstance(c, bytes) else c for c in nc.variables["Times"][0])
        valid = tstr.replace("_", " ")
        fxx = f"f{i:02d}"

        lats, lons = latlon_coords(getvar(nc, "T2"))
        lats = to_np(lats)
        lons = to_np(lons)

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
        hour = acc * 0.0 if rain_prev is None else np.maximum(acc - rain_prev, 0)
        rain_prev = acc

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

        title_suffix = f"Phoenix 1 km WRF  {valid} UTC  ({cycle} {fxx})"

        cmap, norm = nws_refl_cmap()
        fig = plt.figure(figsize=(8.5, 7.2))
        ax = plt.axes(projection=ccrs.PlateCarree())
        ax.set_extent([float(np.min(lons)), float(np.max(lons)), float(np.min(lats)), float(np.max(lats))])
        cf = ax.contourf(
            lons,
            lats,
            np.ma.masked_less(dbz, 5),
            levels=REFL_LEVELS,
            cmap=cmap,
            norm=norm,
            extend="max",
            transform=ccrs.PlateCarree(),
        )
        basemap(ax, ccrs, cfeature)
        ax.set_title(f"Simulated composite reflectivity\n{title_suffix}", fontsize=11)
        cb = plt.colorbar(cf, ax=ax, shrink=0.82, pad=0.02, ticks=REFL_LEVELS)
        cb.set_label("dBZ")
        save_fig(fig, plt, out_root / "refl" / f"{fxx}.png")

        plot_field(
            ccrs,
            plt,
            lats,
            lons,
            hour / 25.4,
            f"1-hour precipitation\n{title_suffix}",
            "inches",
            out_root / "precip" / f"{fxx}.png",
            cmap="YlGnBu",
            levels=[0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3],
        )

        plot_field(
            ccrs,
            plt,
            lats,
            lons,
            t2f,
            f"2 m temperature\n{title_suffix}",
            "°F",
            out_root / "t2" / f"{fxx}.png",
            vmin=50,
            vmax=120,
            cmap="turbo",
        )

        fig = plt.figure(figsize=(8.5, 7.2))
        ax = plt.axes(projection=ccrs.PlateCarree())
        ax.set_extent([float(np.min(lons)), float(np.max(lons)), float(np.min(lats)), float(np.max(lats))])
        skip = max(1, lats.shape[0] // 22)
        speed = gust if gust is not None else wspd
        cf = ax.pcolormesh(
            lons, lats, speed, vmin=0, vmax=50, cmap="YlOrRd", transform=ccrs.PlateCarree(), shading="auto"
        )
        ax.barbs(
            lons[::skip, ::skip],
            lats[::skip, ::skip],
            u10[::skip, ::skip] * 1.94384,
            v10[::skip, ::skip] * 1.94384,
            length=5,
            transform=ccrs.PlateCarree(),
            linewidth=0.4,
        )
        basemap(ax, ccrs, cfeature)
        ax.set_title(f"10 m wind + gusts\n{title_suffix}", fontsize=11)
        cb = plt.colorbar(cf, ax=ax, shrink=0.82, pad=0.02)
        cb.set_label("gust or wind (kt)")
        save_fig(fig, plt, out_root / "wind" / f"{fxx}.png")

        plot_field(
            ccrs,
            plt,
            lats,
            lons,
            mucape,
            f"Most-unstable CAPE\n{title_suffix}",
            "J kg⁻¹",
            out_root / "cape" / f"{fxx}.png",
            vmin=0,
            vmax=4000,
            cmap="YlOrRd",
        )

        j, iidx = nearest_ij(lats, lons, KPHX[0], KPHX[1])
        series["times"].append(valid)
        series["t2f"].append(float(t2f[j, iidx]))
        series["tdf"].append(float(tdf[j, iidx]))
        series["wspd"].append(float(wspd[j, iidx]))
        series["gust"].append(float(speed[j, iidx]))
        series["precip_hour"].append(float(hour[j, iidx] / 25.4))
        series["precip_acc"].append(float(acc[j, iidx] / 25.4))
        nc.close()

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    x = np.arange(len(series["times"]))
    axes[0].plot(x, series["t2f"], color="#d62728", label="T 2 m")
    axes[0].plot(x, series["tdf"], color="#1f77b4", label="Td 2 m")
    axes[0].set_ylabel("°F")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[0].set_title("KPHX meteogram — Phoenix 1 km WRF")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(x, series["wspd"], color="#2ca02c", label="10 m wind")
    axes[1].plot(x, series["gust"], color="#ff7f0e", label="gust")
    axes[1].set_ylabel("kt")
    axes[1].legend(loc="upper right", fontsize=8)
    axes[1].grid(True, alpha=0.3)

    axes[2].bar(x, series["precip_hour"], color="#17becf", label="1-h precip")
    axes[2].plot(x, series["precip_acc"], color="#000", label="accumulated")
    axes[2].set_ylabel("inches")
    axes[2].legend(loc="upper right", fontsize=8)
    axes[2].grid(True, alpha=0.3)
    labels = [t[5:16] for t in series["times"]]
    axes[2].set_xticks(x[:: max(1, len(x) // 10)])
    axes[2].set_xticklabels(labels[:: max(1, len(x) // 10)], rotation=30, ha="right")
    fig.tight_layout()
    meteo = out_root / "meteogram" / "f00.png"
    save_fig(fig, plt, meteo)
    shutil.copyfile(meteo, out_root / "meteogram" / "kphx.png")

    meta = {
        "cycle": cycle,
        "frames": len(files),
        "products": PRODUCTS,
        "kphx": series,
    }
    (out_root / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"Wrote {len(files)} frames under {out_root}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wrfout-dir", help="Directory of wrfout_d01_* files")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--cycle", required=True, help="YYYYMMDDTHHz")
    p.add_argument("--hours", type=int, default=18)
    p.add_argument(
        "--placeholder",
        action="store_true",
        help="Write labeled placeholder PNGs (no wrfout / wrf-python)",
    )
    args = p.parse_args()

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    if args.placeholder:
        plot_placeholder(out_root, args.cycle, args.hours)
        return
    if not args.wrfout_dir:
        raise SystemExit("--wrfout-dir is required unless --placeholder")
    plot_wrfout(Path(args.wrfout_dir), out_root, args.cycle)


if __name__ == "__main__":
    main()
