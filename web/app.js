"use strict";
/* Клиент AI Market: страницы из прототипа Figma (СРО2, п. 2.7) + чат-виджет. Вся подстановка данных идёт через textContent. */

const $ = (s, r = document) => r.querySelector(s);
const view = $("#view");

/* ---------- утилиты ---------- */
function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "html") e.innerHTML = v;       // только для статичных SVG из этого файла
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : document.createTextNode(kid));
  return e;
}
const fmt = n => Number(n).toLocaleString("ru-RU").replace(/ /g, " ") + " ₸";
const pct = x => Math.round(x * 100) + "%";
const norm = t => t.toLowerCase().replace(/ё/g, "е");
/* текст отзыва с подсветкой фраз, на которых построен AI-анализ (фраза ищется без учёта регистра и «ё») */
function highlighted(text, aspects) {
  const nt = norm(text), spans = [];
  for (const a of aspects || []) {
    if (!a.clause || a.sentiment === "neutral") continue;
    const i = nt.indexOf(norm(a.clause));
    if (i >= 0) spans.push([i, i + a.clause.length, a.sentiment === "positive" ? "p" : "n"]);
  }
  spans.sort((x, y) => x[0] - y[0]);
  const out = []; let pos = 0;
  for (const [st, en, k] of spans) {
    if (st < pos) continue;
    if (st > pos) out.push(text.slice(pos, st));
    out.push(h("mark", { class: k }, text.slice(st, en))); pos = en;
  }
  if (pos < text.length) out.push(text.slice(pos));
  return out;
}
const joinRu = a => a.length < 2 ? a.join("") : a.slice(0, -1).join(", ") + " и " + a[a.length - 1];
function verdictLine(good, bad) {
  const g = (good || []).map(x => h("mark", { class: "p" }, x)), b = (bad || []).map(x => h("mark", { class: "n" }, x));
  const j = (arr) => arr.flatMap((m, i) => i === 0 ? [m] : [i === arr.length - 1 ? " и " : ", ", m]);
  if (g.length && b.length) return ["Хвалят ", ...j(g), ", ругают ", ...j(b), "."];
  if (g.length) return ["Хвалят ", ...j(g), "."];
  if (b.length) return ["Ругают ", ...j(b), "."];
  return ["Пока мало отзывов для вывода."];
}
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* приватный режим */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
let session = store.get("session", null);   // {token, user:{email, role}}
let compareIds = store.get("compare", []);
let dialogId = null;

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (session) headers.Authorization = "Bearer " + session.token;
  let res;
  try {
    res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  } catch {
    throw new Error("Нет связи с сервером. Проверьте, что он запущен.");
  }
  let data = {};
  try { data = await res.json(); } catch { /* пустой ответ */ }
  if (!res.ok) { const err = new Error(data.error || "Ошибка " + res.status); err.status = res.status; throw err; }
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2800);
}
function loading() { view.replaceChildren(h("div", { class: "grid" }, [1, 2, 3].map(() => h("div", { class: "skeleton" })))); }
function failure(e) {
  view.replaceChildren(h("div", { class: "empty" }, h("h2", {}, "Не удалось загрузить данные"), h("p", {}, e.message), h("button", { class: "btn primary", onclick: route }, "Повторить")));
}

/* ---------- иллюстрации товаров (SVG, без внешних файлов) ---------- */
const PALETTES = [["#eef2f1", "#cfd8d6"], ["#eef2f1", "#c3d0cd"], ["#eef2f1", "#d6dedc"], ["#eef2f1", "#bccac7"], ["#eef2f1", "#cad6d3"], ["#eef2f1", "#d0dad7"]];
function artSvg(cat, color) {
  if (cat === "ноутбуки") return `<svg viewBox="0 0 160 110"><rect x="26" y="10" width="108" height="72" rx="7" fill="#0b1f1d"/><rect x="31" y="15" width="98" height="62" rx="3" fill="${color}"/><path d="M8 88h144l-10 12H18z" fill="#2a4743"/></svg>`;
  if (cat === "наушники") return `<svg viewBox="0 0 120 120"><path d="M20 70V60a40 40 0 0 1 80 0v10" fill="none" stroke="#0b1f1d" stroke-width="9" stroke-linecap="round"/><rect x="12" y="66" width="24" height="38" rx="10" fill="${color}" stroke="#0b1f1d" stroke-width="5"/><rect x="84" y="66" width="24" height="38" rx="10" fill="${color}" stroke="#0b1f1d" stroke-width="5"/></svg>`;
  return `<svg viewBox="0 0 70 130"><rect x="4" y="4" width="62" height="122" rx="14" fill="#0b1f1d"/><rect x="9" y="12" width="52" height="106" rx="8" fill="${color}"/><rect x="27" y="7" width="16" height="4" rx="2" fill="#0b1f1d"/></svg>`;
}
function art(p, big) {
  const [bg, fg] = PALETTES[p.product_id % PALETTES.length];
  return h("div", { class: "art", style: `background:${bg}`, html: artSvg(p.category, fg) });
}

/* ---------- карточка товара ---------- */
function toggleCompare(id) {
  const i = compareIds.indexOf(id);
  if (i >= 0) compareIds.splice(i, 1);
  else if (compareIds.length >= 4) return toast("Можно сравнить не более четырёх товаров");
  else compareIds.push(id);
  store.set("compare", compareIds); updateCompareBadge();
  toast(i >= 0 ? "Убрано из сравнения" : "Добавлено к сравнению");
  if (location.hash.startsWith("#/catalog") || location.hash.startsWith("#/product")) route();
}
function updateCompareBadge() { const b = $("#cmp-count"); b.textContent = compareIds.length; b.hidden = !compareIds.length; }

function productCard(p) {
  const inCmp = compareIds.includes(p.product_id);
  return h("article", { class: "card clickable", onclick: e => { if (!e.target.closest("button, a")) location.hash = "#/product/" + p.product_id; } },
    h("div", { style: "display:flex;justify-content:space-between;gap:12px;align-items:baseline" },
      h("h3", { style: "margin:0" }, p.title), h("span", { class: "price" }, fmt(p.price))),
    h("p", { class: "serif", style: "margin:4px 0 2px;font-size:19px;line-height:1.4" }, verdictLine(p.top_aspects, p.weak_aspects)),
    h("div", { style: "display:flex;flex-direction:column;gap:7px;margin:8px 0" }, (p.aspect_bars || []).map(a =>
      h("div", { class: "bar-mini" }, h("span", {}, cap(a.aspect)),
        h("span", { class: "bar", role: "img", "aria-label": `${a.aspect}: ${pct(a.pos_share)} положительных`, style: "height:5px" }, h("i", { style: `width:${pct(a.pos_share)};background:${a.pos_share < 0.6 ? "#d8574a" : "#3aa76d"}` })),
        h("b", {}, pct(a.pos_share))))),
    h("div", { class: "rate" }, (p.rating != null ? "★ " + Number(p.rating).toFixed(1) + " · " : ""), p.n_reviews, " отзывов"),
    h("div", { class: "btns" },
      h("a", { class: "btn", href: "#/product/" + p.product_id }, "Подробнее"),
      h("button", { class: "btn", type: "button", "aria-pressed": inCmp, onclick: () => toggleCompare(p.product_id) }, inCmp ? "В сравнении" : "Сравнить")));
}

/* ---------- страницы ---------- */
function sellerEntry() {
  if (session && ["seller", "admin"].includes(session.user.role)) { location.hash = "#/seller"; return; }
  openAuth(); $("#auth-email").value = "seller@demo.kz"; $("#auth-pass").value = "seller123"; toast("Демо-вход продавца подставлен, нажмите «Войти»");
}
function tryReviewPanel() {
  const box = h("div", { class: "try-out" });
  const ta = h("textarea", { id: "try-text", rows: 3, maxlength: 2000, placeholder: "Например: Камера отличная, но сильно греется, а батарея садится к обеду" });
  const run = async () => {
    box.replaceChildren(h("small", { class: "muted" }, "Анализирую…"));
    try {
      const r = await api("/api/analyze", { method: "POST", body: { text: ta.value } });
      box.replaceChildren(r.aspects.length ? h("div", { class: "chips-row" }, r.aspects.map(a => h("span", { class: "tag " + (a.sentiment === "positive" ? "good" : a.sentiment === "negative" ? "bad" : "") }, cap(a.aspect) + ": " + (a.sentiment === "positive" ? "плюс" : "минус") + " · «" + a.clause + "»"))) : h("p", { class: "muted" }, "Аспекты не найдены. Опишите камеру, батарею, экран, звук, цену и т. п."),
        h("small", { class: "muted" }, "Общая тональность: " + SENT_RU[r.overall] + " · модель: " + r.model));
    } catch (e) { box.replaceChildren(h("p", { class: "muted" }, e.message)); }
  };
  return h("section", { class: "panel try" }, h("h3", {}, "Проверьте, как ИИ читает отзыв"),
    h("p", { class: "hint" }, "Напишите отзыв своими словами: ИИ разобьёт его на части, найдёт аспекты и определит тональность каждой."),
    ta, h("button", { class: "btn primary", onclick: run }, "Разобрать отзыв"), box);
}

async function pageHome() {
  loading();
  try {
    const data = await api("/api/products?sort=rating");
    const cats = [["смартфоны", "Смартфоны", "смартфоны"], ["ноутбуки", "Ноутбуки", "ноутбуки"], ["наушники", "Наушники", "наушники"]];
    view.replaceChildren(
      h("section", { class: "hero intro" },
        h("div", { class: "eyebrow" }, "Платформа анализа отзывов"),
        h("h1", {}, "Выбирайте по тому, что пишут покупатели"),
        h("p", {}, "Спросите словами или выберите категорию. ИИ разберёт отзывы и покажет, что хвалят и за что ругают."),
        h("form", { class: "ask", onsubmit: e => { e.preventDefault(); const v = e.target.q.value.trim(); if (v) openChat(v); } },
          h("input", { name: "q", maxlength: 300, placeholder: "Например: смартфон до 300 000 ₸ с хорошей камерой", "aria-label": "Вопрос AI-консультанту" }),
          h("button", { class: "btn primary", type: "submit" }, "Спросить AI")),
        h("div", { class: "ask-chips" }, ["Как батарея у iPhone 15", "Сравни iPhone 15 и Galaxy S24 FE", "Сколько стоит доставка"].map(t => h("button", { type: "button", onclick: () => openChat(t) }, t))),
        h("div", { class: "btns" },
          h("a", { class: "btn", href: "#/catalog" }, "Каталог"),
          h("button", { class: "btn", type: "button", onclick: sellerEntry }, "Я продавец"))),
      tryReviewPanel(),
      h("h2", {}, "Категории"),
      h("div", { class: "tiles" }, cats.map(([key, name], i) =>
        h("a", { class: "tile", href: "#/catalog?category=" + encodeURIComponent(key) }, h("div", { html: artSvg(key, PALETTES[i][1]), style: "width:54px" }), h("div", {}, h("b", {}, name), h("span", { class: "muted" }, "разбор отзывов по аспектам"))))),
      h("h2", {}, "Лучшие по отзывам"),
      h("p", { class: "muted", style: "margin-top:-8px" }, "Товары с самой высокой оценкой среди проанализированных отзывов"),
      h("div", { class: "grid" }, data.items.slice(0, 4).map(productCard)));
  } catch (e) { failure(e); }
}

function parseHash() {
  const [path, qs] = location.hash.slice(1).split("?");
  return { path: path || "/", q: Object.fromEntries(new URLSearchParams(qs || "")) };
}

async function pageCatalog(q) {
  loading();
  const f = { category: q.category || "", brand: q.brand || "", price_min: q.price_min || "", price_max: q.price_max || "", rating_min: q.rating_min || "", sort: q.sort || "popular", q: q.q || "" };
  const params = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
  try {
    const [data, all] = await Promise.all([api("/api/products?" + params), api("/api/products")]);
    const brands = [...new Set(all.items.map(p => p.brand))].sort();
    const go = patch => { const n = { ...f, ...patch }; const s = new URLSearchParams(Object.entries(n).filter(([, v]) => v)); location.hash = "#/catalog" + (s.toString() ? "?" + s : ""); };
    const radio = (name, val, label) => h("label", {}, h("input", { type: "radio", name, checked: f[name] === val, onchange: () => go({ [name]: val }) }), label);
    const filters = h("aside", { class: "filters", "aria-label": "Фильтры" },
      h("div", { style: "display:flex;justify-content:space-between" }, h("strong", {}, "Фильтры"), h("a", { href: "#/catalog", class: "muted" }, "Сбросить")),
      h("h4", {}, "Категория"), radio("category", "", "Все"), ["смартфоны", "ноутбуки", "наушники"].map(c => radio("category", c, c[0].toUpperCase() + c.slice(1))),
      h("h4", {}, "Цена, ₸"),
      h("form", { class: "row", onsubmit: e => { e.preventDefault(); go({ price_min: e.target.a.value, price_max: e.target.b.value }); } },
        h("input", { type: "number", name: "a", placeholder: "от", value: f.price_min, min: 0, "aria-label": "Цена от" }),
        h("input", { type: "number", name: "b", placeholder: "до", value: f.price_max, min: 0, "aria-label": "Цена до" }),
        h("button", { class: "btn", style: "padding:6px 10px" }, "OK")),
      h("h4", {}, "Бренд"), radio("brand", "", "Любой"), brands.map(b => radio("brand", b, b)),
      h("h4", {}, "Рейтинг"), radio("rating_min", "", "Любой"), radio("rating_min", "4.5", "4,5 и выше"), radio("rating_min", "4", "4,0 и выше"));
    const sel = h("select", { "aria-label": "Сортировка", onchange: e => go({ sort: e.target.value }) },
      [["popular", "По популярности"], ["rating", "По рейтингу"], ["price_asc", "Сначала дешевле"], ["price_desc", "Сначала дороже"]].map(([v, t]) => h("option", { value: v, selected: f.sort === v }, t)));
    view.replaceChildren(
      h("div", { class: "crumbs" }, h("a", { href: "#/" }, "Главная"), " / Каталог"),
      h("div", { class: "toolbar" }, h("div", {}, h("div", { class: "eyebrow" }, "Каталог"), h("h1", { style: "margin:0" }, f.q ? `Поиск: «${f.q}»` : "Выбирайте по отзывам"),
        h("span", { class: "muted" }, `${data.total} товаров с AI-анализом отзывов`)),
        h("button", { class: "btn primary", type: "button", onclick: () => openChat("Помоги подобрать " + (f.category || "товар")) }, "Подобрать с AI")),
      h("div", { class: "layout" }, filters, h("div", {}, h("div", { class: "toolbar" }, h("span", {}), sel),
        data.items.length ? h("div", { class: "grid" }, data.items.map(productCard)) : h("div", { class: "empty" }, "Ничего не найдено. Измените фильтры или спросите AI-консультанта."))));
  } catch (e) { failure(e); }
}

function barRow(a) {
  return h("div", { class: "bar-row" }, h("span", {}, a.aspect[0].toUpperCase() + a.aspect.slice(1)),
    h("div", { class: "bar", role: "img", "aria-label": `${a.aspect}: ${pct(a.pos_share)} положительных` }, h("i", { style: `width:${pct(a.pos_share)};background:${a.pos_share < 0.6 ? "#d8574a" : "#3aa76d"}` })),
    h("small", {}, `${pct(a.pos_share)} · ${Math.round(a.n_mentions)} упом.`));
}

async function pageProduct(id) {
  loading();
  try {
    const [p, rv] = await Promise.all([api("/api/products/" + id), api(`/api/products/${id}/reviews`)]);
    const inCmp = compareIds.includes(p.product_id);
    const shown = p.aspects.filter(a => a.aspect !== "доставка" && a.n_mentions >= 5);
    const reviewForm = h("form", { class: "panel form-review", onsubmit: async e => {
      e.preventDefault();
      if (!session) return openAuth();
      const btn = e.target.querySelector("button"); btn.disabled = true;
      try {
        await api("/api/reviews", { method: "POST", body: { product_id: p.product_id, rating: Number(e.target.rating.value), text: e.target.text.value, wait: true } });
        toast("Отзыв принят и проанализирован"); pageProduct(id);
      } catch (err) { toast(err.message); btn.disabled = false; }
    } },
      h("h3", {}, "Оставить отзыв"),
      h("label", {}, "Оценка: ", h("select", { name: "rating" }, [5, 4, 3, 2, 1].map(n => h("option", { value: n }, "★".repeat(n))))),
      h("textarea", { name: "text", required: true, minlength: 5, maxlength: 2000, placeholder: "Что понравилось, что нет? Например: «Камера отличная, но греется в играх»" }),
      h("div", { class: "btns" }, h("button", { class: "btn primary", type: "submit" }, session ? "Отправить" : "Войти и отправить")));
    view.replaceChildren(
      h("div", { class: "crumbs" }, h("a", { href: "#/" }, "Главная"), " / ", h("a", { href: "#/catalog?category=" + encodeURIComponent(p.category) }, p.category), " / " + p.title),
      h("div", { class: "pdp" },
        art(p),
        h("div", {},
          h("div", { class: "eyebrow" }, p.brand),
          h("h1", {}, p.title),
          h("div", { class: "rate" }, h("span", { class: "star" }, "★ "), (p.rating != null ? Number(p.rating).toFixed(1) : "—"), " · ", p.n_reviews, " отзывов"),
          h("div", { class: "price big", style: "margin:14px 0" }, fmt(p.price)),
          h("dl", { class: "specs" }, Object.entries(p.specs).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
          h("div", { class: "btns" },
            h("button", { class: "btn primary", type: "button", onclick: () => toast("Демо: корзина не реализована") }, "В корзину"),
            h("button", { class: "btn", type: "button", onclick: () => toggleCompare(p.product_id) }, inCmp ? "В сравнении" : "Сравнить"),
            h("button", { class: "btn", type: "button", onclick: () => openChat("Что покупатели говорят про " + p.title) }, "Спросить AI")))),
      h("section", { class: "panel ai" },
        h("div", { class: "eyebrow" }, `Вывод по ${p.n_analyzed} отзывам`),
        h("p", { class: "verdict" }, verdictLine(p.strengths.slice(0, 2), p.weaknesses.slice(0, 2))),
        h("div", { style: "display:flex;gap:30px;flex-wrap:wrap;align-items:center;margin:10px 0 18px" },
          h("div", {}, h("div", { class: "big" }, p.sentiment.positive + "%"), h("span", { class: "muted" }, "положительных отзывов")),
          h("div", { style: "flex:1;min-width:240px" },
            h("div", { class: "stack", role: "img", "aria-label": `Тональность: ${p.sentiment.positive}% положительных, ${p.sentiment.neutral}% нейтральных, ${p.sentiment.negative}% отрицательных` },
              h("i", { style: `width:${p.sentiment.positive}%;background:var(--good)` }), h("i", { style: `width:${p.sentiment.neutral}%;background:var(--neutral)` }), h("i", { style: `width:${p.sentiment.negative}%;background:var(--bad)` })),
            h("div", { class: "legend" }, h("span", {}, h("b", { style: "background:var(--good)" }), "положительные " + p.sentiment.positive + "%"), h("span", {}, h("b", { style: "background:var(--neutral)" }), "нейтральные " + p.sentiment.neutral + "%"), h("span", {}, h("b", { style: "background:var(--bad)" }), "отрицательные " + p.sentiment.negative + "%")))),
        h("h3", {}, "Оценка по аспектам"), shown.length ? shown.map(barRow) : h("p", { class: "muted" }, "Недостаточно упоминаний для оценки по аспектам."),
        h("p", { class: "muted" }, p.conclusion),
        h("div", { class: "two lists" },
          h("div", {}, h("h3", {}, "Покупатели чаще всего отмечают"), p.strengths.length ? h("ul", {}, p.strengths.map(s => h("li", {}, s))) : h("span", { class: "muted" }, "—")),
          h("div", {}, h("h3", {}, "На что жалуются покупатели"), p.weaknesses.length ? h("ul", {}, p.weaknesses.map(s => h("li", {}, s))) : h("span", { class: "muted" }, "Явных проблем не выявлено"))),
        h("p", { class: "hint" }, `Проанализировано отзывов: ${p.n_analyzed}. Исключено как подозрительные: ${p.n_excluded_suspicious}. Модель тональности: ${p.model}.`)),
      h("section", { class: "panel", id: "reviews" }, h("h3", {}, "Отзывы, учтённые в AI-анализе"),
        rv.items.map(r => h("div", { class: "review", id: "review-" + r.review_id },
          h("div", { class: "meta" }, h("span", { class: "star", style: "color:#c98a12" }, "★".repeat(r.rating)), h("span", {}, r.date.slice(0, 10)), r.is_verified ? h("span", {}, "покупка подтверждена") : h("span", {}, "покупка не подтверждена"), h("span", { class: "counted" }, "учтён в AI-анализе"), h("span", {}, "#" + r.review_id)),
          h("div", { class: "txt" }, highlighted(r.text, r.aspects))))),
      reviewForm);
    if (location.hash.includes("review=")) { const rid = location.hash.split("review=")[1]; setTimeout(() => $("#review-" + rid)?.scrollIntoView({ block: "center" }), 50); }
  } catch (e) { failure(e); }
}

async function pageCompare() {
  if (compareIds.length < 2) {
    view.replaceChildren(h("div", { class: "empty" }, h("h2", {}, "Сравнение товаров"), h("p", {}, "Выберите минимум два товара кнопкой «Сравнить» в каталоге."), h("a", { class: "btn primary", href: "#/catalog" }, "Перейти в каталог")));
    return;
  }
  loading();
  try {
    const c = await api("/api/compare", { method: "POST", body: { ids: compareIds } });
    const head = h("tr", {}, h("th", {}, ""), c.products.map(p => h("th", {}, h("a", { href: "#/product/" + p.product_id }, p.title), h("br"), h("button", { class: "btn", style: "padding:3px 8px;font-size:12px;margin-top:6px", onclick: () => toggleCompare(p.product_id) }, "Убрать"))));
    const rows = [
      h("tr", {}, h("th", {}, "Цена"), c.products.map(p => h("td", {}, fmt(p.price)))),
      h("tr", {}, h("th", {}, "Рейтинг"), c.products.map(p => h("td", {}, "★ " + (p.rating != null ? Number(p.rating).toFixed(1) : "—") + " · " + p.n_reviews + " отзывов"))),
      ...c.aspects.map(r => h("tr", {}, h("th", {}, r.aspect[0].toUpperCase() + r.aspect.slice(1), ),
        c.products.map(p => { const v = r.values[String(p.product_id)]; return h("td", { class: r.best === p.product_id ? "best" : "" }, v ? pct(v.pos_share) + " положительных" : "мало данных"); }))),
      h("tr", {}, h("th", {}, "Плюсы"), c.products.map(p => h("td", {}, p.strengths.join(", ") || "—"))),
      h("tr", {}, h("th", {}, "Минусы"), c.products.map(p => h("td", {}, p.weaknesses.join(", ") || "—"))),
    ];
    view.replaceChildren(h("div", { class: "crumbs" }, h("a", { href: "#/" }, "Главная"), " / Сравнение"), h("h1", {}, "Сравнение товаров"),
      h("table", { class: "cmp" }, h("thead", {}, head), h("tbody", {}, rows)),
      h("p", { class: "hint" }, "Зелёным выделено лучшее значение по аспекту. Доли положительных упоминаний рассчитаны со сглаживанием (формула (1) в отчёте)."));
  } catch (e) { failure(e); }
}

function lineChart(points) {
  if (!points.length) return h("p", { class: "muted" }, "Нет данных");
  const W = 640, H = 220, L = 44, R = 16, T = 16, B = 34, n = points.length;
  const x = i => L + (n === 1 ? (W - L - R) / 2 : i * (W - L - R) / (n - 1)), y = v => T + (1 - v / 100) * (H - T - B);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.positive_pct).toFixed(1)}`).join(" ");
  const avg = points.reduce((t, p) => t + p.positive_pct, 0) / n;
  const grid = [0, 25, 50, 75, 100].map(v => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${L - 8}" y="${y(v) + 4}" font-size="11" text-anchor="end" fill="var(--muted)" font-family="Geist Mono,monospace">${v}%</text>`).join("");
  const step = Math.ceil(n / 8);
  const dots = points.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.positive_pct)}" r="${i === n - 1 ? 5 : 3.5}" fill="var(--ai)"><title>${p.month}: ${Math.round(p.positive_pct)}%</title></circle>` +
    (i % step === 0 || i === n - 1 ? `<text x="${x(i)}" y="${H - 12}" font-size="11" text-anchor="middle" fill="var(--muted)" font-family="Geist Mono,monospace">${p.month.slice(5)}.${p.month.slice(2, 4)}</text>` : "")).join("");
  const last = points[n - 1];
  const avgLine = `<line x1="${L}" x2="${W - R}" y1="${y(avg)}" y2="${y(avg)}" stroke="var(--muted)" stroke-dasharray="5 4"/><text x="${L + 6}" y="${y(avg) + 16}" font-size="11" fill="var(--muted)">в среднем ${Math.round(avg)}%</text>`;
  const lab = `<text x="${x(n - 1)}" y="${y(last.positive_pct) - 10}" font-size="12" font-weight="600" text-anchor="end" fill="var(--ai-ink)" font-family="Geist Mono,monospace">${Math.round(last.positive_pct)}%</text>`;
  return h("div", { class: "chart", role: "img", "aria-label": `Динамика доли положительных отзывов по месяцам, в последнем месяце ${Math.round(last.positive_pct)}%`,
    html: `<svg viewBox="0 0 ${W} ${H}">${grid}${avgLine}<path d="${d}" fill="none" stroke="var(--ai)" stroke-width="2.5" stroke-linejoin="round"/>${dots}${lab}</svg>` },
  );
}

/* ---------- кабинет продавца: разделы #/seller, /seller/products, /seller/reviews, /seller/analytics, /seller/settings ---------- */
const SELLER_NAV = [["", "Обзор"], ["products", "Товары"], ["reviews", "Отзывы"], ["analytics", "AI-аналитика"], ["settings", "Настройки"]];
const sellerPrefs = () => ({ sensitivity: store.get("sl_sens", "normal"), days: store.get("sl_days", 0) });
const SENT_RU = { positive: "положительный", neutral: "нейтральный", negative: "отрицательный" };
const cap = s => s[0].toUpperCase() + s.slice(1);
const qs = o => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v != null && v !== 0)).toString();

function sellerShell(section, ...content) {
  return h("div", { class: "sl" },
    h("aside", { class: "side" }, SELLER_NAV.map(([k, t]) => h("a", { href: "#/seller" + (k ? "/" + k : ""), class: k === section ? "on" : "" }, t))),
    h("div", {}, h("div", { class: "eyebrow" }, "Кабинет продавца"), ...content));
}
function kpis(a) {
  return h("div", { class: "kpis" },
    h("div", { class: "kpi" }, h("b", {}, a.total_reviews), "всего отзывов"),
    h("div", { class: "kpi" }, h("b", { style: "color:var(--good)" }, a.distribution.positive + "%"), "положительных"),
    h("div", { class: "kpi" }, h("b", {}, a.distribution.neutral + "%"), "нейтральных"),
    h("div", { class: "kpi" }, h("b", { style: "color:var(--bad)" }, a.distribution.negative + "%"), "отрицательных"));
}
function alertBox(al) {
  return h("div", { class: "alert", role: "alert" }, h("span", { class: "tag-a" }, "АЛЕРТ"), h("div", { class: "msg-a" }, al.message),
    h("a", { href: "#/seller/reviews?" + qs({ product_id: al.product_id, aspect: al.aspect, aspect_sentiment: "negative", days: 30 }) }, "Смотреть отзывы"));
}
function aspectRows(list, pid) {
  return list.map(t => {
    const tot = t.mentions || 1;
    return h("div", { class: "bar-row" },
      h("a", { href: "#/seller/reviews?" + qs({ product_id: pid, aspect: t.aspect }), title: "Показать отзывы с этим аспектом" }, cap(t.aspect)),
      h("div", { class: "bar" }, h("i", { style: `width:${Math.round(t.positive / tot * 100)}%;background:${t.positive / tot < 0.6 ? "#d8574a" : "#3aa76d"}` })),
      h("small", {}, `${t.positive} плюс · ${t.negative} минус`));
  });
}

async function pageSeller(sub = "", q = {}) {
  if (!session || !["seller", "admin"].includes(session.user.role)) {
    view.replaceChildren(h("div", { class: "empty" }, h("h2", {}, "Кабинет продавца"), h("p", {}, "Войдите как продавец (демо: seller@demo.kz / seller123)."), h("button", { class: "btn primary", onclick: openAuth }, "Войти")));
    return;
  }
  loading();
  const pr = sellerPrefs();
  try {
    if (sub === "settings") return sellerSettings();
    if (sub === "products") {
      const { products } = await api("/api/seller/products");
      view.replaceChildren(sellerShell("products", h("h1", {}, "Мои товары"),
        h("section", { class: "panel" }, h("div", { class: "tbl-wrap" }, h("table", { class: "cmp" },
          h("thead", {}, h("tr", {}, ["Товар", "Цена", "Отзывов", "Оценка", "Плюс", "Минус", "Подозр.", "Сигналы", ""].map(t => h("th", {}, t)))),
          h("tbody", {}, products.map(p => h("tr", {},
            h("td", {}, h("b", {}, p.title), h("br"), h("small", { class: "muted" }, cap(p.category) + " · " + p.brand)),
            h("td", {}, fmt(p.price)), h("td", {}, p.n_reviews), h("td", {}, p.rating ?? "—"),
            h("td", {}, p.positive_pct + "%"), h("td", {}, p.negative_pct + "%"), h("td", {}, p.suspicious),
            h("td", {}, p.alerts ? h("span", { class: "tag bad" }, "алертов: " + p.alerts) : h("span", { class: "muted" }, "—")),
            h("td", {}, h("div", { class: "acts" },
              h("a", { href: "#/seller/reviews?product_id=" + p.product_id }, "Отзывы"),
              h("a", { href: "#/seller/analytics?product_id=" + p.product_id }, "Аналитика"),
              h("a", { href: "#/product/" + p.product_id }, "Магазин"))))))))),
        h("p", { class: "hint" }, `Всего товаров: ${products.length}.`)));
      return;
    }
    if (sub === "reviews") {
      const [{ products }, data] = await Promise.all([api("/api/seller/products"), api("/api/seller/reviews?" + qs({ ...q, limit: 100 }))]);
      const go = patch => { location.hash = "#/seller/reviews?" + qs({ ...q, ...patch }); };
      const sel = h("select", { onchange: e => go({ product_id: e.target.value, aspect: "", aspect_sentiment: "" }) }, h("option", { value: "" }, "Все товары"),
        products.map(p => h("option", { value: p.product_id, selected: String(p.product_id) === String(q.product_id) }, p.title)));
      const ss = h("select", { onchange: e => go({ sentiment: e.target.value }) }, h("option", { value: "" }, "Любая тональность"),
        ["positive", "neutral", "negative"].map(k => h("option", { value: k, selected: q.sentiment === k }, cap(SENT_RU[k]))));
      const susp = h("label", { class: "chk" }, h("input", { type: "checkbox", checked: q.suspicious === "1", onchange: e => go({ suspicious: e.target.checked ? "1" : "" }) }), " только подозрительные");
      const chips = [];
      if (q.aspect) chips.push(h("span", { class: "tag" }, "Аспект: " + q.aspect + (q.aspect_sentiment ? " (" + SENT_RU[q.aspect_sentiment] + ")" : "")));
      if (q.days) chips.push(h("span", { class: "tag" }, "за " + q.days + " дн."));
      if (chips.length) chips.push(h("button", { class: "btn", onclick: () => go({ aspect: "", aspect_sentiment: "", days: "" }) }, "Сбросить аспект/период"));
      const exportCsv = () => {
        const esc = v => '"' + String(v).replace(/"/g, '""') + '"';
        const rows = [["id", "товар", "оценка", "дата", "тональность", "подозрительный", "текст"].join(";")].concat(data.reviews.map(r =>
          [r.review_id, esc(r.title), r.rating, r.date.slice(0, 10), r.overall || "", r.suspicious ? "да" : "нет", esc(r.text)].join(";")));
        const url = URL.createObjectURL(new Blob(["﻿" + rows.join("\n")], { type: "text/csv;charset=utf-8" }));
        const a = h("a", { href: url, download: "reviews.csv" }); document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
        toast("Файл reviews.csv сохранён");
      };
      view.replaceChildren(sellerShell("reviews", h("h1", {}, "Отзывы покупателей"),
        h("div", { class: "toolbar" }, sel, ss, susp, h("button", { class: "btn", onclick: exportCsv }, "Экспорт CSV")),
        chips.length ? h("div", { class: "chips-row" }, chips) : null,
        h("p", { class: "hint" }, `Найдено: ${data.total}` + (data.total > data.reviews.length ? `, показаны последние ${data.reviews.length}` : "")),
        data.reviews.length ? data.reviews.map(r => h("div", { class: "panel rv" + (r.suspicious ? " sus" : "") },
          h("div", { class: "rv-head" }, h("b", {}, r.title), h("span", {}, "★".repeat(r.rating)), h("small", { class: "muted" }, r.date.slice(0, 10)),
            r.overall ? h("span", { class: "tag " + (r.overall === "positive" ? "good" : r.overall === "negative" ? "bad" : "") }, SENT_RU[r.overall]) : null,
            r.suspicious ? h("span", { class: "tag bad" }, "подозрительный " + Math.round(r.fake_score * 100) + "%") : null,
            r.is_verified ? null : h("small", { class: "muted" }, "покупка не подтверждена")),
          h("p", { class: "txt" }, highlighted(r.text, r.aspects)),
          h("div", { class: "rv-asp" }, r.aspects.map(a => h("span", { class: "tag " + (a.sentiment === "positive" ? "good" : a.sentiment === "negative" ? "bad" : "") }, a.aspect))))) :
          h("div", { class: "empty" }, h("p", {}, "По выбранным фильтрам отзывов нет."))));
      return;
    }
    const pid = sub === "analytics" ? q.product_id : "";
    const [a, { products }] = await Promise.all([
      api("/api/seller/analytics?" + qs({ product_id: pid, sensitivity: pr.sensitivity, days: pr.days })),
      sub === "analytics" ? api("/api/seller/products") : Promise.resolve({ products: [] })]);
    const parts = [h("h1", {}, sub === "analytics" ? "AI-аналитика отзывов" : "Обзор")];
    if (sub === "analytics") parts.push(h("div", { class: "toolbar" },
      h("select", { onchange: e => { location.hash = "#/seller/analytics?" + qs({ product_id: e.target.value }); } }, h("option", { value: "" }, "Все товары"),
        products.map(p => h("option", { value: p.product_id, selected: String(p.product_id) === String(pid) }, p.title))),
      h("small", { class: "muted" }, `Период: ${pr.days ? pr.days + " дн." : "всё время"} · чувствительность: ${{ low: "низкая", normal: "обычная", high: "высокая" }[pr.sensitivity]} (меняется в «Настройках»)`)));
    if (sub !== "analytics") parts.push(h("p", { class: "lead" }, a.alerts.length
      ? ["Есть тревожный сигнал: ", h("mark", { class: "n" }, a.alerts[0].aspect), ". Остальное без резких изменений."]
      : ["Резких изменений в жалобах нет. Положительных отзывов ", h("mark", { class: "p" }, a.distribution.positive + "%"), "."]));
    parts.push(a.alerts.length ? a.alerts.map(alertBox) : h("p", { class: "hint" }, "Резких изменений в жалобах не обнаружено."), kpis(a),
      h("section", { class: "panel" }, h("h3", {}, "Доля положительных отзывов по месяцам"), lineChart(a.dynamics),
        h("div", { class: "chart-legend" }, h("span", {}, h("i"), "доля положительных, %"), h("span", {}, "пунктир: среднее за период")),
        h("p", { class: "hint" }, "Доля положительных отзывов по месяцам. Исключено подозрительных отзывов: " + a.suspicious_excluded +
          (a.suspicious_excluded ? " — " : ""), a.suspicious_excluded ? h("a", { href: "#/seller/reviews?" + qs({ product_id: pid, suspicious: "1" }) }, "показать") : null)));
    const asp = h("section", { class: "panel" }, h("h3", {}, "Основные аспекты"));
    let all = false;
    const draw = () => {
      asp.replaceChildren(h("h3", {}, "Основные аспекты"), ...aspectRows(all ? a.top_aspects : a.top_aspects.slice(0, 5), pid),
        a.top_aspects.length > 5 ? h("button", { class: "btn", onclick: () => { all = !all; draw(); } }, all ? "Свернуть" : "Все аспекты (" + a.top_aspects.length + ")") : null);
    };
    draw(); parts.push(asp);
    if (sub !== "analytics") parts.push(h("a", { class: "btn primary", href: "#/seller/analytics" }, "Подробная AI-аналитика →"));
    view.replaceChildren(sellerShell(sub === "analytics" ? "analytics" : "", ...parts));
  } catch (e) { e.status === 401 || e.status === 403 ? (session = null, store.del("session"), refreshAuth(), pageSeller(sub, q)) : failure(e); }
}

function sellerSettings() {
  const pr = sellerPrefs();
  const sens = h("select", { id: "set-sens" }, [["low", "Низкая (реже сигналы)"], ["normal", "Обычная (рекомендуется)"], ["high", "Высокая (чаще сигналы)"]].map(([v, t]) => h("option", { value: v, selected: v === pr.sensitivity }, t)));
  const per = h("select", { id: "set-days" }, [[0, "Всё время"], [30, "30 дней"], [90, "90 дней"], [180, "180 дней"]].map(([v, t]) => h("option", { value: v, selected: v === pr.days }, t)));
  view.replaceChildren(sellerShell("settings", h("h1", {}, "Настройки"),
    h("section", { class: "panel" }, h("h3", {}, "Аналитика"),
      h("label", { class: "fld" }, "Чувствительность предупреждений о росте жалоб", sens),
      h("label", { class: "fld" }, "Период анализа", per),
      h("p", { class: "hint" }, "Чувствительность задаёт порог роста доли жалоб и значимость z-критерия: низкая — рост от 30 п.п. и z ≥ 4,0; обычная — от 20 п.п. и z ≥ 3,0; высокая — от 15 п.п. и z ≥ 2,0."),
      h("button", { class: "btn primary", onclick: () => { store.set("sl_sens", sens.value); store.set("sl_days", Number(per.value)); toast("Настройки сохранены"); } }, "Сохранить")),
    h("section", { class: "panel" }, h("h3", {}, "Аккаунт"), h("p", {}, session.user.email + " · роль: " + session.user.role),
      h("button", { class: "btn", onclick: () => { session = null; store.del("session"); refreshAuth(); location.hash = "#/"; toast("Вы вышли из аккаунта"); } }, "Выйти"))));
}

/* ---------- роутер ---------- */
function route() {
  const { path, q } = parseHash();
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("on", a.dataset.nav === (path.split("/")[1] || "home") || (a.dataset.nav === "home" && path === "/")));
  window.scrollTo(0, 0);
  if (path === "/" || path === "") return pageHome();
  if (path === "/catalog") return pageCatalog(q);
  if (path.startsWith("/product/")) return pageProduct(parseInt(path.split("/")[2], 10));
  if (path === "/compare") return pageCompare();
  if (path === "/seller" || path.startsWith("/seller/")) return pageSeller(path.split("/")[2] || "", q);
  view.replaceChildren(h("div", { class: "empty" }, h("h2", {}, "Страница не найдена"), h("a", { class: "btn primary", href: "#/" }, "На главную")));
}
window.addEventListener("hashchange", route);

/* ---------- авторизация ---------- */
let authMode = "login";
function refreshAuth() { const b = $("#auth-btn"); b.textContent = session ? session.user.email.split("@")[0] + " · выйти" : "Войти"; b.title = session ? session.user.role : "Войти"; }
function openAuth() { $("#auth-err").textContent = ""; $("#auth-dlg").showModal(); $("#auth-email").focus(); }
$("#auth-btn").addEventListener("click", () => { if (session) { session = null; store.del("session"); refreshAuth(); toast("Вы вышли"); route(); } else openAuth(); });
$("#auth-switch").addEventListener("click", () => { authMode = authMode === "login" ? "register" : "login"; $("#auth-title").textContent = authMode === "login" ? "Вход" : "Регистрация"; $("#auth-submit").textContent = authMode === "login" ? "Войти" : "Создать аккаунт"; $("#auth-switch").textContent = authMode === "login" ? "Регистрация" : "Есть аккаунт"; });
$("#auth-cancel").addEventListener("click", () => $("#auth-dlg").close());
$("#auth-form").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const r = await api("/api/auth/" + authMode, { method: "POST", body: { email: $("#auth-email").value, password: $("#auth-pass").value } });
    session = r; store.set("session", r); refreshAuth(); $("#auth-dlg").close(); toast("Здравствуйте, " + r.user.email); route();
  } catch (err) { $("#auth-err").textContent = err.message; }
});

/* ---------- поиск ---------- */
$("#search").addEventListener("submit", e => { e.preventDefault(); const v = $("#q").value.trim(); location.hash = v ? "#/catalog?q=" + encodeURIComponent(v) : "#/catalog"; });

/* ---------- чат ---------- */
const chat = $("#chat"), log = $("#chat-log");
const CHIPS = ["Смартфон до 300 000 ₸ с хорошей камерой", "Как батарея у iPhone 15", "Сравни iPhone 15 и Galaxy S24 FE", "Сколько стоит доставка", "Соедините с оператором"];
function renderChips() { $("#chat-chips").replaceChildren(...CHIPS.map(t => h("button", { type: "button", onclick: () => send(t) }, t))); }
function addMsg(role, text, extra = {}) {
  const m = h("div", { class: "msg " + role + (extra.warn ? " warn" : "") }, text);
  for (const p of extra.products || []) m.append(h("a", { class: "pcard", href: "#/product/" + p.product_id, onclick: () => { if (window.innerWidth < 900) chat.hidden = true; } }, h("b", {}, p.title), h("small", {}, fmt(p.price) + (p.why ? " · " + p.why.join("; ") : ""))));
  if (extra.sources?.length) m.append(h("div", { class: "src" }, "Источники: ", extra.sources.map(s => h("a", { title: s.text, onclick: () => openReview(extra.products?.[0]?.product_id, s.review_id) }, "отзыв #" + s.review_id))));
  if (extra.compare) m.append(h("a", { class: "pcard", href: "#/compare", onclick: () => { compareIds = extra.compare.slice(); store.set("compare", compareIds); updateCompareBadge(); } }, h("b", {}, "Открыть таблицу сравнения")));
  log.append(m); log.scrollTop = log.scrollHeight; return m;
}
function openReview(pid, rid) { if (!pid) return; location.hash = `#/product/${pid}?review=${rid}`; if (location.hash.includes("product")) route(); }
function openChat(prefill) {
  chat.hidden = false; $("#chat-fab").hidden = true;
  if (!log.children.length) addMsg("bot", "Здравствуйте! Я отвечаю по отзывам покупателей и показываю цитаты. Спросите про модель, попросите подобрать или сравнить.");
  renderChips(); $("#chat-input").focus();
  if (typeof prefill === "string") send(prefill);
}
async function send(text) {
  text = (text ?? $("#chat-input").value).trim(); if (!text) return;
  $("#chat-input").value = ""; addMsg("user", text);
  const wait = addMsg("bot typing", "Читаю отзывы…");
  try {
    const r = await api("/api/chat", { method: "POST", body: { message: text, dialog_id: dialogId } });
    dialogId = r.dialog_id; wait.remove();
    addMsg("bot", r.reply, { products: r.products, sources: r.sources, warn: r.degraded || r.grounded === false, compare: r.compare_ids });
  } catch (e) { wait.remove(); addMsg("bot", "Не удалось получить ответ: " + e.message, { warn: true }); }
}
$("#chat-fab").addEventListener("click", () => openChat());
$("#chat-close").addEventListener("click", () => { chat.hidden = true; $("#chat-fab").hidden = false; });
$("#chat-form").addEventListener("submit", e => { e.preventDefault(); send(); });
$("#chat-new").addEventListener("click", () => { dialogId = null; log.replaceChildren(); $("#chat-history").hidden = true; openChat(); });
$("#chat-hist").addEventListener("click", async () => {
  const box = $("#chat-history");
  if (!box.hidden) { box.hidden = true; return; }
  if (!session) { box.replaceChildren(h("p", { class: "hint", style: "padding:10px 14px" }, "Войдите, чтобы сохранять историю диалогов.")); box.hidden = false; return; }
  try {
    const d = await api("/api/chat/dialogs");
    box.replaceChildren(...(d.items.length ? d.items.map(x => h("button", { type: "button", onclick: () => loadDialog(x.dialog_id) }, `#${x.dialog_id} · ${x.title}${x.status === "escalated" ? " (оператор)" : ""}`)) : [h("p", { class: "hint", style: "padding:10px 14px" }, "История пуста")]));
    box.hidden = false;
  } catch (e) { toast(e.message); }
});
async function loadDialog(id) {
  try {
    const d = await api("/api/chat/dialogs/" + id); dialogId = id; log.replaceChildren();
    d.items.forEach(m => addMsg(m.role === "user" ? "user" : "bot", m.text));
    $("#chat-history").hidden = true;
  } catch (e) { toast(e.message); }
}

/* ---------- старт ---------- */
updateCompareBadge(); refreshAuth(); route();
