const BUCKET = "https://phx-wrf-forecast.s3.amazonaws.com";
const PRODUCTS = [
  { id: "refl", label: "Reflectivity" },
  { id: "precip", label: "Precip" },
  { id: "t2", label: "2 m temp" },
  { id: "wind", label: "10 m wind" },
  { id: "cape", label: "MUCAPE" },
  { id: "meteogram", label: "KPHX meteogram" },
];

const tabs = document.getElementById("tabs");
const frame = document.getElementById("frame");
const banner = document.getElementById("banner");
const hour = document.getElementById("hour");
const hourLabel = document.getElementById("hour-label");
const caption = document.getElementById("caption");
const playBtn = document.getElementById("play");

let product = "refl";
let latest = null;
let playing = null;

function pad(n) {
  return String(n).padStart(2, "0");
}

function mstFromFxx(cycle, fxx) {
  // cycle: YYYYMMDDT12z  → 12Z + fxx hours, display MST (UTC-7)
  const y = cycle.slice(0, 4);
  const m = cycle.slice(4, 6);
  const d = cycle.slice(6, 8);
  const utc = Date.UTC(Number(y), Number(m) - 1, Number(d), 12 + fxx);
  const mst = new Date(utc - 7 * 3600 * 1000);
  const hh = pad(mst.getUTCHours());
  const mm = pad(mst.getUTCMonth() + 1);
  const dd = pad(mst.getUTCDate());
  return `${hh}:00 MST ${mm}/${dd}`;
}

function frameUrl(fxx) {
  if (!latest) return "";
  const base = latest.base_url || `${BUCKET}/runs/${latest.cycle}`;
  if (product === "meteogram") return `${base}/meteogram/kphx.png`;
  return `${base}/${product}/f${pad(fxx)}.png`;
}

function render() {
  const fxx = Number(hour.value);
  hour.max = String((latest && latest.hours) || 18);
  hourLabel.textContent =
    product === "meteogram" ? "KPHX" : `F${pad(fxx)} · ${latest ? mstFromFxx(latest.cycle, fxx) : ""}`;
  caption.textContent = latest
    ? `Cycle ${latest.cycle} · ${latest.status} · completed ${latest.completed_at || "n/a"}`
    : "";
  const url = frameUrl(fxx);
  if (url) frame.src = url;
  const sliderRow = document.querySelector(".slider-row");
  sliderRow.style.visibility = product === "meteogram" ? "hidden" : "visible";
}

PRODUCTS.forEach((p, i) => {
  const b = document.createElement("button");
  b.textContent = p.label;
  b.dataset.id = p.id;
  if (i === 0) b.classList.add("active");
  b.addEventListener("click", () => {
    product = p.id;
    [...tabs.children].forEach((el) => el.classList.toggle("active", el.dataset.id === p.id));
    render();
  });
  tabs.appendChild(b);
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
    return;
  }
  playBtn.textContent = "Pause";
  playing = setInterval(() => {
    const next = Number(hour.value) + 1;
    hour.value = String(next > Number(hour.max) ? 0 : next);
    render();
  }, 700);
});

fetch(`${BUCKET}/latest.json?t=${Date.now()}`)
  .then((r) => r.json())
  .then((j) => {
    latest = j;
    if (j.status === "success") {
      banner.classList.add("ok");
      banner.textContent = `Last successful run: ${j.cycle} (${j.hours} h) · ${j.completed_at || ""}`;
    } else if (j.status === "awaiting-first-run") {
      banner.textContent = "No forecast published yet. The 12Z cycle lands after the daily run.";
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
