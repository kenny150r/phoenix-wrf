const BUCKET = "https://phx-wrf-forecast.s3.amazonaws.com";
const PRODUCTS = [
  { id: "refl", label: "Reflectivity" },
  { id: "precip", label: "Precip" },
  { id: "t2", label: "2 m temp" },
  { id: "wind", label: "10 m wind + gusts" },
  { id: "cape", label: "MUCAPE" },
  { id: "meteogram", label: "KPHX meteogram" },
];

const tabs = document.getElementById("tabs");
const frame = document.getElementById("frame");
const frameError = document.getElementById("frame-error");
const banner = document.getElementById("banner");
const hour = document.getElementById("hour");
const hourLabel = document.getElementById("hour-label");
const caption = document.getElementById("caption");
const playBtn = document.getElementById("play");
const sliderRow = document.getElementById("slider-row");

let product = "refl";
let latest = null;
let playing = null;

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

function frameUrl(fxx) {
  if (!latest) return "";
  const base = latest.base_url || `${BUCKET}/runs/${latest.cycle}`;
  if (product === "meteogram") {
    return latest.meteogram_url || `${base}/meteogram/f00.png`;
  }
  return `${base}/${product}/f${pad(fxx)}.png`;
}

function render() {
  const fxx = Number(hour.value);
  hour.max = String((latest && (latest.hours ?? latest.frames - 1)) || 18);
  const isMeteo = product === "meteogram";
  hourLabel.textContent = isMeteo
    ? "KPHX"
    : `F${pad(fxx)} · ${latest ? mstFromFxx(latest.cycle, fxx) : ""}`;
  caption.textContent = latest
    ? `Cycle ${latest.cycle} · ${latest.status} · completed ${latest.completed_at || "n/a"}`
    : "";
  sliderRow.classList.toggle("hidden", isMeteo);
  const url = frameUrl(fxx);
  if (!url) return;
  frameError.hidden = true;
  if (frame.dataset.loaded === url) return;
  frame.style.opacity = "0.45";
  frame.src = url;
  frame.dataset.loaded = url;
}

PRODUCTS.forEach((p, i) => {
  const b = document.createElement("button");
  b.type = "button";
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

document.addEventListener("keydown", (ev) => {
  if (product === "meteogram") return;
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

frame.addEventListener("load", () => {
  frame.style.opacity = "1";
  frameError.hidden = true;
});
frame.addEventListener("error", () => {
  frame.style.opacity = "0.2";
  frameError.hidden = false;
});

fetch(`${BUCKET}/latest.json?t=${Date.now()}`)
  .then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  })
  .then((j) => {
    latest = j;
    if (j.status === "success") {
      banner.classList.add("ok");
      banner.textContent = `Last successful run: ${j.cycle} (${j.hours} h) · ${j.completed_at || ""}`;
    } else if (j.status === "awaiting-first-run") {
      banner.textContent =
        "No forecast published yet. The 12Z cycle lands after the daily WRF run.";
    } else if (j.status === "placeholder") {
      banner.textContent = `Placeholder frames for ${j.cycle} — waiting on the first WRF cycle.`;
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
