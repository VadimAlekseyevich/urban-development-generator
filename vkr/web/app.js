const el = (id) => document.getElementById(id);
const map = el("map");
let site;
let houses;
const number = new Intl.NumberFormat("ru-RU");

function svg(parent, tag, attrs = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs))
    node.setAttribute(key, value);
  if (text) node.textContent = text;
  parent.append(node);
  return node;
}

function rect(parent, area, style) {
  return svg(parent, "rect", {
    x: area.x,
    y: area.y,
    width: area.w,
    height: area.h,
    ...style,
  });
}

function label(parent, x, y, text, attrs = {}) {
  return svg(
    parent,
    "text",
    { x, y, fill: "#415b50", "font-size": 12, ...attrs },
    text,
  );
}

function drawSite() {
  const {
    width,
    height,
    park,
    school,
    school_point: [sx, sy],
    radius,
  } = site;
  map.setAttribute("viewBox", `-22 -24 ${width + 44} ${height + 50}`);
  const bounds = { x: 0, y: 0, w: width, h: height };
  const defs = svg(map, "defs");
  rect(svg(defs, "clipPath", { id: "boundary" }), bounds);
  const grid = svg(defs, "pattern", {
    id: "grid",
    width: 48,
    height: 48,
    patternUnits: "userSpaceOnUse",
  });
  svg(grid, "path", {
    d: "M0 48V0H48",
    fill: "none",
    stroke: "#d7dbcc",
    "stroke-width": 1,
  });
  rect(map, bounds, { fill: "#f4f3e7" });
  const surface = svg(map, "g", { "clip-path": "url(#boundary)" });
  rect(surface, bounds, { fill: "url(#grid)" });
  svg(surface, "circle", {
    cx: sx,
    cy: sy,
    r: radius,
    fill: "#c9dfd5",
    "fill-opacity": 0.27,
    stroke: "#518080",
    "stroke-width": 2,
    "stroke-dasharray": "6 6",
  });
  for (const road of site.roads) {
    rect(
      surface,
      { x: 0, y: road.y - road.width / 2, w: width, h: road.width },
      { fill: "#d1d1c5", stroke: "#aaaFA2" },
    );
    svg(surface, "path", {
      d: `M0 ${road.y}h${width}`,
      stroke: "#fcfaf0",
      "stroke-width": 1,
      "stroke-dasharray": "12 8",
    });
    label(surface, 14, road.y + 4, road.name, {
      "font-size": 11,
      fill: "#46534b",
      stroke: "#d1d1c5",
      "stroke-width": 4,
      "paint-order": "stroke",
    });
  }
  rect(surface, park, { fill: "#cadaae", stroke: "#90a979" });
  for (let x = park.x + 13; x < park.x + park.w - 6; x += 24) {
    for (let y = park.y + 15; y < park.y + park.h - 6; y += 28) {
      svg(surface, "circle", {
        cx: x,
        cy: y,
        r: 5,
        fill: "#b4c998",
        stroke: "#9bb98c",
      });
    }
  }
  const px = park.x + park.w / 2,
    py = park.y + park.h / 2;
  label(surface, px, py, "Парк", {
    "text-anchor": "middle",
    "font-size": 16,
    stroke: "#cadaae",
    "stroke-width": 5,
    "paint-order": "stroke",
  });
  rect(surface, school, { fill: "#efdbb6", stroke: "#b3976c" });
  svg(surface, "path", {
    d: `M${sx - 27} ${sy - 21}h54v10h-39v10h-15z`,
    fill: "#cd9b5e",
    stroke: "#9b753f",
  });
  svg(surface, "circle", { cx: sx, cy: sy, r: 3, fill: "#835e30" });
  label(surface, sx, sy + 22, "Школа", {
    "text-anchor": "middle",
    "font-size": 15,
    fill: "#684f30",
  });
  houses = svg(surface, "g");
  rect(map, bounds, {
    fill: "none",
    stroke: "#869483",
    "stroke-width": 1.5,
    "pointer-events": "none",
  });
  const scaleY = height - 23;
  svg(map, "path", {
    d: `M20 ${scaleY - 5}v5h100v-5`,
    stroke: "#41564c",
    fill: "none",
    "stroke-width": 2,
  });
  label(map, 20, scaleY - 9, "100 м");
  label(map, width - 15, height - 22, `Радиус школы · ${radius} м`, {
    "text-anchor": "end",
  });
}

function showBuildings(buildings) {
  houses.replaceChildren();
  el("house").textContent = buildings.length
    ? "Выберите дом на схеме, чтобы посмотреть его характеристики."
    : "В этом варианте жилые дома не размещены.";
  for (const b of buildings) {
    const detail = `Дом № ${b.id} · Этажей: ${b.floors} · Вместимость: ${b.capacity} чел. · Заселено: ${b.residents} чел.`;
    const group = svg(houses, "g", {
      class: "building",
      tabindex: 0,
      role: "button",
      "aria-label": detail,
    });
    svg(group, "title", {}, detail);
    rect(group, b, { rx: 1 });
    svg(group, "path", {
      d: `M${b.x + 3} ${b.y + 4}h${b.w - 6}`,
      stroke: "#96b3a2",
    });
    const select = () => {
      houses.querySelector(".selected")?.classList.remove("selected");
      group.classList.add("selected");
      el("house").textContent = detail;
    };
    group.addEventListener("click", select);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select();
      }
    });
  }
}

const reasons = {
  target: "Цель достигнута",
  school: "Исчерпаны места в школе",
  land: "Исчерпаны допустимые положения домов",
};

function showResult(params, data) {
  showBuildings(data.buildings);
  el("map-state").textContent = `Вариант ${params.seed}`;
  el("goal").textContent = number.format(params.target);
  el("population").textContent = number.format(data.population);
  el("deficit").textContent = number.format(data.deficit);
  el("count").textContent = number.format(data.buildings.length);
  el("used").textContent =
    `${number.format(data.used_seats)} / ${number.format(params.seats)}`;
  el("reason").textContent = reasons[data.reason];
  el("reason").dataset.limited = data.deficit > 0;
  el("limits").hidden = data.reason !== "land";
  el("limits").textContent =
    `Отклонено по геометрии: ${data.rejected.geometry}. За радиусом школы: ${data.rejected.radius}.`;
}

function showError(message) {
  el("error").textContent = message;
  el("error").hidden = false;
}

el("params").addEventListener("submit", async (event) => {
  event.preventDefault();
  const params = Object.fromEntries(
    ["target", "seats", "seed"].map((key) => [key, el(key).valueAsNumber]),
  );
  el("error").hidden = true;
  el("generate").disabled = true;
  try {
    const response = await fetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || "Ошибка генерации.");
    showResult(params, data);
  } catch (error) {
    showError("Не удалось получить план. " + error.message);
  } finally {
    el("generate").disabled = false;
  }
});

async function start() {
  try {
    const response = await fetch("/site.json");
    if (!response.ok) throw Error("Ошибка загрузки участка.");
    site = await response.json();
    drawSite();
    el("generate").disabled = false;
  } catch {
    showError(
      "Не удалось загрузить участок. Проверьте запуск сервиса и обновите страницу.",
    );
  }
}
start();
