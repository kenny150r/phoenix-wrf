const BUCKET = "https://phx-wrf-forecast.s3.amazonaws.com";
/** Fallback 200 km Lambert mass-grid AABB at 33.45N / 112.07W (e_we=e_sn=201, dx=1 km). */
const DEFAULT_BOUNDS = [
  [32.55050, -113.15377],
  [34.34493, -110.98623],
];
const DEFAULT_CENTER = [33.45, -112.07];
const KPHX = [33.4342, -112.0116];

const PRODUCTS = [
  { id: "refl", label: "Reflectivity" },
  { id: "precip", label: "Precip" },
  { id: "t2", label: "2 m temp" },
  { id: "wind", label: "10 m wind" },
  { id: "cape", label: "MUCAPE" },
  { id: "meteogram", label: "KPHX" },
];

const LEGENDS = {
  refl: {
    title: "Reflectivity",
    unit: "dBZ",
    ticks: ["5", "20", "35", "50", "75"],
    colors: [
      "#00ecec", "#01a0f6", "#0100f6", "#00ff00", "#00c800",
      "#009000", "#ffff00", "#e7c000", "#ff9000", "#ff0000",
      "#d60000", "#c00000", "#ff00ff", "#9955c9", "#ffffff",
    ],
    discrete: true,
  },
  precip: {
    title: "1-hour precip",
    unit: "in",
    ticks: ["0.01", "0.1", "0.5", "1", "2+"],
    colors: [
      "#98fb98", "#00ee00", "#009b00", "#ffff4d", "#ffcc00",
      "#ff7a00", "#ff0000", "#b00000", "#ff00ff",
    ],
    discrete: true,
  },
  t2: {
    title: "2 m temperature",
    unit: "°F",
    ticks: ["50", "70", "90", "110", "120"],
    colors: [
      "#2166ac", "#4393c3", "#74add1", "#9dc1d9", "#c5b56a",
      "#e8d070", "#f4b942", "#ee8f2a", "#e0691e", "#d04527",
      "#b91c1c", "#991b1b", "#7f1d1d", "#450a0a",
    ],
    discrete: true,
  },
  wind: {
    title: "10 m wind / gust",
    unit: "kt",
    ticks: ["0", "10", "20", "35", "50"],
    colors: [
      "#cfc4b0", "#d4b07a", "#c99050", "#c4784a", "#b45a32",
      "#9a3c28", "#7e2828", "#641828", "#3e1018",
    ],
    discrete: true,
  },
  cape: {
    title: "MUCAPE",
    unit: "J kg⁻¹",
    ticks: ["0", "1000", "2000", "3000", "4000"],
    colors: [
      "#d4c48a", "#f0d060", "#f0a830", "#e07820", "#c85020",
      "#a82828", "#8c1838", "#6e1048", "#4a0a5c",
    ],
    discrete: true,
  },
};

const banner = document.getElementById("banner");
const hour = document.getElementById("hour");
const hourLabel = document.getElementById("hour-label");
const validLabel = document.getElementById("valid-label");
const playBtn = document.getElementById("play");
const opacityInput = document.getElementById("opacity");
const opacityLabel = document.getElementById("opacity-label");
const productsEl = document.getElementById("products");
const legendEl = document.getElementById("legend");
const frameError = document.getElementById("frame-error");
const meteoPanel = document.getElementById("meteo-panel");
const meteoImg = document.getElementById("meteo-img");

let product = "refl";
let latest = null;
let playing = null;
let overlay = null;
let overlayGen = 0;
let domainRect = null;
let bounds = DEFAULT_BOUNDS;
let opacity = 0.75;
let meteoOpen = false;

const map = L.map("map", {
  zoomControl: false,
  minZoom: 7,
  maxZoom: 13,
  worldCopyJump: false,
});
L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a> · <a href="https://github.com/kenny150r/phoenix-wrf">source</a>',
  subdomains: "abcd",
  maxZoom: 19,
}).addTo(map);

map.createPane("radarPane");
map.getPane("radarPane").style.zIndex = 350;
map.getPane("radarPane").style.pointerEvents = "none";

L.circleMarker(KPHX, {
  radius: 5,
  color: "#c4784a",
  weight: 2,
  fillColor: "#e8e0d4",
  fillOpacity: 0.95,
}).bindTooltip("KPHX", {
  permanent: true,
  direction: "right",
  offset: [8, 0],
  className: "city-label",
}).addTo(map);

L.circleMarker(DEFAULT_CENTER, {
  radius: 3,
  color: "#c4784a",
  weight: 1,
  fillColor: "#c4784a",
  fillOpacity: 0.9,
}).bindTooltip("Phoenix", {
  permanent: true,
  direction: "left",
  offset: [-6, -6],
  className: "city-label",
}).addTo(map);

function applyBounds(next) {
  if (!Array.isArray(next) || next.length !== 2) return;
  bounds = next;
  if (domainRect) map.removeLayer(domainRect);
  domainRect = L.rectangle(bounds, {
    color: "#c4784a",
    weight: 1,
    opacity: 0.45,
    fill: false,
    dashArray: "4 5",
    interactive: false,
  }).addTo(map);
  map.fitBounds(bounds, { padding: [48, 48], maxZoom: 10 });
  if (overlay) overlay.setBounds(bounds);
}

applyBounds(DEFAULT_BOUNDS);

document.querySelectorAll(".panel").forEach((el) => {
  L.DomEvent.disableClickPropagation(el);
  L.DomEvent.disableScrollPropagation(el);
});

function pad(n) {
  return String(n).padStart(2, "0");
}

function parseCycle(cycle) {
  const m = String(cycle || "").match(/^(\d{4})(\d{2})(\d{2})T(\d{1,2})z$/i);
  if (!m) return null;
  return { y: Number(m[1]), mo: Number(m[2]), d: Number(m[3]), h: Number(m[4]) };
}

function mstFromFxx(cycle, fxx) {
  const c = parseCycle(cycle);
  if (!c) return "";
  const utc = Date.UTC(c.y, c.mo - 1, c.d, c.h + fxx);
  const mst = new Date(utc - 7 * 3600 * 1000);
  return `${pad(mst.getUTCHours())}:00 MST ${pad(mst.getUTCMonth() + 1)}/${pad(mst.getUTCDate())}`;
}

function frameUrl(prod, fxx) {
  if (!latest) return "";
  const base = latest.base_url || `${BUCKET}/runs/${latest.cycle}`;
  if (prod === "meteogram") {
    return latest.meteogram_url || `${base}/meteogram/f00.png`;
  }
  return `${base}/${prod}/f${pad(fxx)}.png`;
}

function renderLegend(id) {
  const spec = LEGENDS[id];
  if (!spec) {
    legendEl.innerHTML = "";
    return;
  }
  const bar = spec.discrete
    ? `<div class="legend-bar discrete">${spec.colors.map((c) => `<span style="background:${c}"></span>`).join("")}</div>`
    : `<div class="legend-bar" style="background:${spec.gradient}"></div>`;
  legendEl.innerHTML = `
    <div class="legend-title">${spec.title}</div>
    ${bar}
    <div class="legend-ticks">${spec.ticks.map((t) => `<span>${t}</span>`).join("")}</div>
    <div class="legend-ticks"><span class="legend-unit">${spec.unit}</span></div>
  `;
}

function setOverlayUrl(url) {
  const my = ++overlayGen;
  frameError.hidden = true;
  const incoming = L.imageOverlay(url, bounds, {
    opacity: 0,
    pane: "radarPane",
    className: "wrf-overlay",
  });
  incoming.addTo(map);
  const finish = (ok) => {
    if (my !== overlayGen) {
      map.removeLayer(incoming);
      return;
    }
    if (!ok) {
      map.removeLayer(incoming);
      frameError.hidden = false;
      return;
    }
    incoming.setOpacity(opacity);
    if (overlay && overlay !== incoming) map.removeLayer(overlay);
    overlay = incoming;
  };
  incoming.on("load", () => finish(true));
  incoming.on("error", () => finish(false));
  const el = incoming.getElement();
  if (el && el.complete && el.naturalWidth) finish(true);
}

function preload(prod, fxx) {
  if (!latest || prod === "meteogram") return;
  const img = new Image();
  img.src = frameUrl(prod, fxx);
}

function render() {
  const fxx = Number(hour.value);
  hour.max = String((latest && (latest.hours ?? latest.frames - 1)) || 18);
  hourLabel.textContent = `F${pad(fxx)}`;
  validLabel.textContent = latest ? mstFromFxx(latest.cycle, fxx) : "";

  meteoPanel.hidden = !meteoOpen;
  if (meteoOpen) {
    const meteoUrl = frameUrl("meteogram", 0);
    if (meteoUrl && meteoImg.dataset.loaded !== meteoUrl) {
      meteoImg.src = meteoUrl;
      meteoImg.dataset.loaded = meteoUrl;
    }
  }

  renderLegend(product);
  const url = frameUrl(product, fxx);
  if (!url) return;
  if (overlay && overlay._url === url) {
    overlay.setOpacity(opacity);
    return;
  }
  setOverlayUrl(url);
  const maxH = Number(hour.max);
  preload(product, fxx >= maxH ? 0 : fxx + 1);
}

PRODUCTS.forEach((p, i) => {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = p.label;
  b.dataset.id = p.id;
  if (i === 0) b.classList.add("active");
  b.addEventListener("click", () => {
    if (p.id === "meteogram") {
      meteoOpen = !meteoOpen;
      b.classList.toggle("active", meteoOpen);
      render();
      return;
    }
    product = p.id;
    [...productsEl.children].forEach((el) => {
      if (el.dataset.id !== "meteogram") {
        el.classList.toggle("active", el.dataset.id === p.id);
      }
    });
    render();
  });
  productsEl.appendChild(b);
});

document.getElementById("meteo-close").addEventListener("click", () => {
  meteoOpen = false;
  meteoPanel.hidden = true;
  [...productsEl.children].forEach((el) => {
    if (el.dataset.id === "meteogram") el.classList.remove("active");
  });
});

hour.addEventListener("input", render);
document.getElementById("prev").addEventListener("click", () => {
  hour.value = String(Math.max(0, Number(hour.value) - 1));
  render();
});
document.getElementById("next").addEventListener("click", () => {
  hour.value = String(Math.min(Number(hour.max), Number(hour.value) + 1));
  render();
});
playBtn.addEventListener("click", () => {
  if (playing) {
    clearInterval(playing);
    playing = null;
    playBtn.textContent = "Play";
    playBtn.classList.remove("active");
    return;
  }
  playBtn.textContent = "Pause";
  playBtn.classList.add("active");
  playing = setInterval(() => {
    const next = Number(hour.value) + 1;
    hour.value = String(next > Number(hour.max) ? 0 : next);
    render();
  }, 650);
});

opacityInput.addEventListener("input", () => {
  opacity = Number(opacityInput.value) / 100;
  opacityLabel.textContent = `${opacityInput.value}%`;
  if (overlay) overlay.setOpacity(opacity);
});

document.addEventListener("keydown", (ev) => {
  if (ev.target && ["INPUT", "TEXTAREA"].includes(ev.target.tagName)) return;
  if (ev.key === "ArrowLeft") {
    hour.value = String(Math.max(0, Number(hour.value) - 1));
    render();
  } else if (ev.key === "ArrowRight") {
    hour.value = String(Math.min(Number(hour.max), Number(hour.value) + 1));
    render();
  } else if (ev.key === " ") {
    ev.preventDefault();
    playBtn.click();
  }
});

renderLegend("refl");

fetch(`${BUCKET}/latest.json?t=${Date.now()}`)
  .then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  })
  .then((j) => {
    latest = j;
    if (j.bounds) applyBounds(j.bounds);
    if (j.status === "success") {
      banner.classList.add("ok");
      banner.textContent = `Last successful run: ${j.cycle} (${j.hours} h) · ${j.completed_at || ""}`;
    } else if (j.status === "awaiting-first-run") {
      banner.textContent =
        "No forecast published yet. The 12Z cycle lands after the daily WRF run.";
    } else if (j.status === "placeholder") {
      banner.textContent = `Placeholder overlays for ${j.cycle} — waiting on the first WRF cycle.`;
    } else {
      banner.classList.add("bad");
      banner.textContent = `Latest: ${j.status} (${j.cycle || "n/a"})`;
    }
    render();
  })
  .catch(() => {
    banner.classList.add("bad");
    banner.textContent = "Could not load latest.json from S3. Check the bucket CORS policy.";
  });
