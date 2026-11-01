/* Карьерный агент — фронтенд без сборки: auth, SSE-чат, вкладки.
   Модальные окна подтверждений — внутри приложения (не window.confirm/prompt),
   выпадающие списки — собственный компонент (в тёмной теме),
   города — комбобокс с фильтрацией по вводу и свободным текстом. */
"use strict";

const $ = (s) => document.querySelector(s);
const state = {
  token: localStorage.getItem("ca_token") || "",
  user: null,
  sessionId: "",
  busy: false,
};

/* ================= утилиты ================= */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// мини-маркдаун: абзацы, **жирный**, `код`, списки, ссылки (после экранирования!)
function md(text) {
  const lines = esc(text).split(/\r?\n/);
  let out = "", inList = false;
  const inline = (s) => s
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(https?:\/\/[^\s<)]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  for (const raw of lines) {
    const l = raw.trim();
    if (!l) { if (inList) { out += "</ul>"; inList = false; } out += "<p></p>"; continue; }
    const m = l.match(/^[-•*]\s+(.*)/);
    if (m) {
      if (!inList) { out += "<ul>"; inList = true; }
      out += "<li>" + inline(m[1]) + "</li>";
      continue;
    }
    if (inList) { out += "</ul>"; inList = false; }
    out += "<p>" + inline(l) + "</p>";
  }
  if (inList) out += "</ul>";
  return out.replace(/<p><\/p>/g, "");
}

function fmtMoney(v) {
  return v == null ? "—" : new Intl.NumberFormat("ru-RU").format(Math.round(v)) + " ₽";
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) { showAuth(); throw new Error("требуется вход"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || ("ошибка " + r.status));
  return data;
}

/* ================= модальные окна (внутри приложения) ================= */
function modalConfirm(title, text, okText) {
  return new Promise((resolve) => {
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML = `<p class="muted">${esc(text)}</p>`;
    $("#modal-ok").textContent = okText || "ОК";
    $("#modal").classList.remove("hidden");
    const done = (val) => {
      $("#modal").classList.add("hidden");
      $("#modal-ok").onclick = $("#modal-cancel").onclick = null;
      resolve(val);
    };
    $("#modal-ok").onclick = () => done(true);
    $("#modal-cancel").onclick = () => done(false);
  });
}

function modalPrompt(title, label, value, okText) {
  return new Promise((resolve) => {
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML =
      `<label class="modal-label">${esc(label)}</label><input id="modal-input" class="modal-input">`;
    const input = $("#modal-input");
    input.value = value || "";
    $("#modal-ok").textContent = okText || "Сохранить";
    $("#modal").classList.remove("hidden");
    setTimeout(() => { input.focus(); input.select(); }, 30);
    const done = (val) => {
      $("#modal").classList.add("hidden");
      $("#modal-ok").onclick = $("#modal-cancel").onclick = input.onkeydown = null;
      resolve(val);
    };
    $("#modal-ok").onclick = () => done(input.value.trim());
    $("#modal-cancel").onclick = () => done(null);
    input.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); done(input.value.trim()); }
      if (e.key === "Escape") done(null);
    };
  });
}

/* ================= выпадающие списки (собственный компонент) ================= */
function closeAllMenus() {
  document.querySelectorAll(".nsel-menu").forEach((m) => m.classList.add("hidden"));
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".nsel") && !e.target.closest(".combo")) closeAllMenus();
});

// красивый дропдаун поверх нативного <select> (значение синхронизировано с ним)
function enhanceSelect(sel) {
  if (sel.dataset.nsel) return;
  sel.dataset.nsel = "1";
  const wrap = document.createElement("div");
  wrap.className = "nsel";
  sel.parentNode.insertBefore(wrap, sel);
  wrap.appendChild(sel);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "nsel-btn";
  const menu = document.createElement("div");
  menu.className = "nsel-menu hidden";
  wrap.appendChild(btn);
  wrap.appendChild(menu);
  const build = () => {
    const cur = sel.options[sel.selectedIndex];
    btn.textContent = cur ? cur.text : "—";
    menu.innerHTML = "";
    for (const o of sel.options) {
      const it = document.createElement("div");
      it.className = "nsel-opt" + (o.selected ? " sel" : "");
      it.textContent = o.text;
      it.addEventListener("mousedown", (e) => {
        e.preventDefault();
        sel.value = o.value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        build();
        menu.classList.add("hidden");
      });
      menu.appendChild(it);
    }
  };
  btn.addEventListener("click", () => {
    const wasHidden = menu.classList.contains("hidden");
    closeAllMenus();
    if (wasHidden) { build(); menu.classList.remove("hidden"); }
  });
  build();
}

// комбобокс: ввод + фильтрация списка + свободный текст (для городов)
const CITIES = [
  "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань", "Нижний Новгород",
  "Красноярск", "Челябинск", "Самара", "Уфа", "Ростов-на-Дону", "Краснодар", "Пермь",
  "Воронеж", "Волгоград", "Калининград", "Тюмень", "Иркутск", "Томск", "Барнаул", "Омск",
  "Владивосток", "Хабаровск", "Саратов", "Тольятти", "Липецк", "Ижевск", "Тула", "Пенза",
  "Оренбург", "Ставрополь", "Мурманск", "Архангельск", "Смоленск", "Рязань", "Ярославль",
  "Владимир", "Курск", "Тверь", "Белгород", "Сочи", "Магнитогорск", "Сургут",
  "Нижневартовск", "Южно-Сахалинск", "Улан-Удэ", "Петрозаводск", "Сыктывкар", "Чебоксары",
  "Киров", "Брянск", "Астрахань", "Набережные Челны", "Стерлитамак", "Грозный", "Махачкала",
  "Владикавказ", "Череповец", "Калуга", "Вологда", "Саранск", "Тамбов", "Орёл", "Кострома",
];

function enhanceCombo(input, items, opts = {}) {
  const wrap = document.createElement("div");
  wrap.className = "combo";
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  const menu = document.createElement("div");
  menu.className = "nsel-menu hidden";
  wrap.appendChild(menu);
  const render = () => {
    const q = input.value.trim().toLowerCase();
    const matches = items.filter((c) => !q || c.toLowerCase().includes(q)).slice(0, 8);
    menu.innerHTML = "";
    const addRow = (label, value, cls) => {
      const it = document.createElement("div");
      it.className = "nsel-opt" + (cls ? " " + cls : "");
      it.textContent = label;
      it.addEventListener("mousedown", (e) => {
        e.preventDefault();
        input.value = value;
        input.dispatchEvent(new Event("change", { bubbles: true }));
        menu.classList.add("hidden");
      });
      menu.appendChild(it);
    };
    if (opts.allowEmpty) addRow("— " + (opts.emptyLabel || "все") + " —", "", "dim");
    matches.forEach((c) => addRow(c, c));
    const exact = items.some((c) => c.toLowerCase() === q);
    if (q && !exact) addRow(`Использовать «${input.value.trim()}»`, input.value.trim(), "free");
    menu.classList.toggle("hidden", !menu.children.length);
  };
  input.addEventListener("focus", render);
  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.classList.add("hidden"); });
  input.addEventListener("blur", () => setTimeout(() => menu.classList.add("hidden"), 120));
}

function initEnhancements() {
  document.querySelectorAll("#app select").forEach(enhanceSelect);
  const vCity = $("#v-city"), sCity = $("#s-city");
  if (vCity && !vCity.dataset.combo) {
    vCity.dataset.combo = "1";
    enhanceCombo(vCity, CITIES);
  }
  if (sCity && !sCity.dataset.combo) {
    sCity.dataset.combo = "1";
    enhanceCombo(sCity, CITIES, { allowEmpty: true, emptyLabel: "вся Россия" });
  }
  const tSearch = $("#t-search");
  if (tSearch && !tSearch.dataset.combo) {
    tSearch.dataset.combo = "1";
    tSearch.addEventListener("blur",
      () => setTimeout(() => $("#t-search-menu").classList.add("hidden"), 150));
  }
}

/* ================= авторизация ================= */
let authKind = "login";
function authMode(kind) {
  authKind = kind;
  $("#tab-login").classList.toggle("active", kind === "login");
  $("#tab-reg").classList.toggle("active", kind === "register");
  $("#auth-submit").textContent = kind === "login" ? "Войти" : "Зарегистрироваться";
  $("#auth-error").textContent = "";
}
function showAuth() {
  $("#auth").classList.remove("hidden");
  $("#app").classList.add("hidden");
}
async function doAuth(e) {
  e.preventDefault();
  $("#auth-error").textContent = "";
  try {
    const body = JSON.stringify({
      email: $("#auth-email").value.trim(),
      password: $("#auth-pass").value,
    });
    const res = await fetch("/api/" + (authKind === "login" ? "login" : "register"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "ошибка");
    state.token = data.token;
    state.user = data.user;
    localStorage.setItem("ca_token", data.token);
    startApp();
  } catch (err) {
    $("#auth-error").textContent = err.message;
  }
  return false;
}
function logout() {
  api("/api/logout", { method: "POST" }).catch(() => {});
  state.token = ""; state.user = null; state.sessionId = "";
  localStorage.removeItem("ca_token");
  showAuth();
}

/* ================= вкладки ================= */
document.addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if (!t) return;
  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
  t.classList.add("active");
  const name = t.dataset.tab;
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $("#view-" + name).classList.remove("hidden");
  if (name === "salary" && !$("#salary-compare").dataset.loaded) salaryCompare();
  if (name === "letters") loadLetters();
  if (name === "tracker") loadTracker();
});

/* ================= чат ================= */
function quick(btn) {
  $("#input").value = btn.textContent.trim();
  send(new Event("submit"));
}
function autogrow(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}
function onKey(e) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(e); return false; }
}

/* ---- список диалогов: название + переименование/удаление ---- */
async function loadSessions() {
  try {
    const data = await api("/api/sessions");
    const list = $("#session-list");
    list.innerHTML = "";
    for (const s of data.sessions) {
      const div = document.createElement("div");
      div.className = "session-item" + (s.session_id === state.sessionId ? " active" : "");
      div.innerHTML = `
        <span class="s-title">${esc(s.title || "Диалог")}</span>
        <span class="s-acts">
          <button class="s-btn" title="Переименовать">✎</button>
          <button class="s-btn" title="Удалить диалог">✕</button>
        </span>`;
      div.querySelector(".s-title").onclick = () => openSession(s.session_id);
      const [ren, del] = div.querySelectorAll(".s-btn");
      ren.onclick = (e) => { e.stopPropagation(); renameSessionAsk(s); };
      del.onclick = (e) => { e.stopPropagation(); deleteSessionAsk(s); };
      list.appendChild(div);
    }
  } catch (e) { /* список не критичен */ }
}

async function renameSessionAsk(s) {
  const title = await modalPrompt("Переименовать диалог", "Название", s.title || "");
  if (title === null || !title) return;
  await api(`/api/sessions/${s.session_id}`,
    { method: "PATCH", body: JSON.stringify({ title }) });
  loadSessions();
}

async function deleteSessionAsk(s) {
  const ok = await modalConfirm("Удалить диалог?",
    `«${s.title || "Диалог"}» будет удалён вместе с историей сообщений. Действие нельзя отменить.`,
    "Удалить");
  if (!ok) return;
  await api(`/api/sessions/${s.session_id}`, { method: "DELETE" });
  if (state.sessionId === s.sessionId) newSession();
  loadSessions();
}

async function newSession() {
  state.sessionId = "";
  $("#messages").innerHTML = "";
  addWelcome();
  loadSessions();
}

function addWelcome() {
  const div = document.createElement("div");
  div.className = "welcome";
  div.innerHTML = `<h2>Чем помочь?</h2>
    <p class="muted">Агент ищет вакансии по датасету hh (585k+), считает разрыв в навыках и
    зарплатную статистику, готовит письма и ведёт учёт откликов. Ничего не отправляется автоматически.</p>
    <div class="chips">
      <button class="chip" onclick="quick(this)">Подбери вакансии C#/.NET в Ростове-на-Дону</button>
      <button class="chip" onclick="quick(this)">Какие навыки мне стоит подтянуть?</button>
      <button class="chip" onclick="quick(this)">Какая зарплата у C#-разработчика?</button>
      <button class="chip" onclick="quick(this)">Напиши сопроводительное письмо на вакансию 137311444</button>
      <button class="chip" onclick="quick(this)">Покажи мои отклики</button>
    </div>`;
  $("#messages").appendChild(div);
  scrollChat();
}

async function openSession(id) {
  state.sessionId = id;
  const data = await api(`/api/sessions/${id}/history`);
  const box = $("#messages");
  box.innerHTML = "";
  for (const m of data.messages) {
    if (m.role === "user") addUserMsg(m.text);
    else { const t = newTurn(); t.setAnswer(m.text); t.finish([], 0); }
  }
  loadSessions();
  scrollChat();
}

function addUserMsg(text) {
  const div = document.createElement("div");
  div.className = "msg-user";
  div.innerHTML = `<div class="bubble">${md(text)}</div>`;
  $("#messages").appendChild(div);
  scrollChat();
}

// «ход» агента: блок, куда капают think/tool/delta/answer
function newTurn() {
  const div = document.createElement("div");
  div.className = "turn";
  div.innerHTML = `
    <div class="acts"></div>
    <div class="cards-box"></div>
    <div class="answer-body"></div>
    <div class="turn-done"></div>`;
  $("#messages").appendChild(div);
  const acts = div.querySelector(".acts");
  const answerBody = div.querySelector(".answer-body");
  let deltaBuf = "";
  scrollChat();
  return {
    el: div,
    addThink(text) {
      let d = acts.querySelector("details.think:last-of-type");
      if (!d) {
        d = document.createElement("details");
        d.className = "think";
        d.innerHTML = `<summary>🧠 Агент думает…</summary><div class="think-text"></div>`;
        acts.appendChild(d);
      }
      d.querySelector(".think-text").textContent += text + " ";
    },
    addToolCall(name, args) {
      const line = document.createElement("div");
      line.className = "tool-line";
      const pretty = Object.entries(args || {})
        .map(([k, v]) => `${k}=${String(v).slice(0, 40)}`).join(", ");
      line.innerHTML = `<span class="mono">⚙ ${esc(name)}</span><span class="mono">${esc(pretty)}</span>`;
      acts.appendChild(line);
      scrollChat();
    },
    addToolResult(name, payload) {
      const data = payload && payload.result !== undefined ? payload.result : payload;
      const line = document.createElement("div");
      line.className = "tool-line";
      let info = "";
      if (data && typeof data === "object") {
        if (data.found !== undefined) info = `найдено ${data.found}`;
        else if (data.n !== undefined) info = `выборка n=${data.n}`;
        else if (data.universe !== undefined) info = `стек: ${data.universe} вакансий`;
        else if (data.saved_to) info = "письмо сохранено локально";
        else if (data.count !== undefined) info = `${data.count} записей`;
        else if (data.candidates !== undefined) info = `кандидатов ${data.candidates}`;
      }
      line.innerHTML = `<span class="ok">✓</span><span class="mono">${esc(name)}</span><span>${esc(info)}</span>`;
      acts.appendChild(line);
      if (data && typeof data === "object") renderCards(name, data, div.querySelector(".cards-box"));
      scrollChat();
    },
    addStatus(text) {
      const line = document.createElement("div");
      line.className = "activity";
      line.textContent = "💬 " + text;
      acts.appendChild(line);
      scrollChat();
    },
    addDelta(text) {
      deltaBuf += text;
      answerBody.className = "answer answer-body";
      answerBody.innerHTML = md(deltaBuf) + '<span class="cursor"></span>';
      scrollChat();
    },
    setAnswer(text) {
      deltaBuf = text;
      answerBody.className = "answer answer-body";
      answerBody.innerHTML = md(text);
      scrollChat();
    },
    setRefusal(text) {
      answerBody.innerHTML = `<div class="refusal">⛔ ${esc(text)}</div>`;
      scrollChat();
    },
    finish(tools, elapsed) {
      answerBody.querySelectorAll(".cursor").forEach((c) => c.remove());
      const done = div.querySelector(".turn-done");
      done.textContent = (tools && tools.length ? "инструменты: " + tools.join(", ") + " · " : "")
        + (elapsed ? elapsed + " с" : "");
      scrollChat();
    },
  };
}

function scrollChat() {
  const m = $("#messages");
  m.scrollTop = m.scrollHeight;
}

/* ---- карточки из результатов инструментов ---- */
function renderCards(name, data, box) {
  const list = [];
  if (name === "search_vacancies" && data.vacancies) list.push(...data.vacancies);
  if (name === "match_profile" && data.vacancies) list.push(...data.vacancies);
  if (list.length) {
    const wrap = document.createElement("div");
    wrap.className = "cards";
    for (const v of list.slice(0, 6)) wrap.appendChild(vacancyCard(v));
    box.appendChild(wrap);
  }
  if (name === "salary_stats" && data.n !== undefined) {
    const strip = document.createElement("div");
    strip.className = "stat-strip";
    strip.innerHTML = `
      <div class="stat"><div class="v">${fmtMoney(data.p25_net)}</div><div class="l">P25</div></div>
      <div class="stat"><div class="v">${fmtMoney(data.median_net)}</div><div class="l">медиана</div></div>
      <div class="stat"><div class="v">${fmtMoney(data.p75_net)}</div><div class="l">P75</div></div>
      <div class="stat"><div class="v">n=${data.n}</div><div class="l">вакансий</div></div>`;
    box.appendChild(strip);
    if (data.warning) {
      const w = document.createElement("div");
      w.className = "tiny muted";
      w.textContent = "⚠ " + data.warning;
      box.appendChild(w);
    }
  }
  if (name === "draft_letter" && data.saved_to) {
    const n = document.createElement("div");
    n.className = "tool-line";
    n.innerHTML = `<span class="ok">✉</span><span>${esc(data.note || "сохранено локально")}</span>`;
    box.appendChild(n);
    loadLetters();
  }
}

function vacancyCard(v) {
  const div = document.createElement("div");
  div.className = "vcard";
  const sal = (v.salary_net_from || v.salary_net_to)
    ? `${v.salary_net_from ? fmtMoney(v.salary_net_from) : ""}${v.salary_net_from && v.salary_net_to ? " – " : ""}${v.salary_net_to ? fmtMoney(v.salary_net_to) : ""} на руки`
    : "з/п не указана";
  const skills = (v.top_skills || v.matched_skills || []).slice(0, 5);
  const missing = (v.missing_skills || []).slice(0, 3);
  div.innerHTML = `
    ${v.score ? `<span class="score-badge">скор ${v.score}</span>` : ""}
    <div class="title">${esc(v.name)}</div>
    <div class="meta">${esc(v.employer || "")}${v.city ? " · " + esc(v.city) : ""}</div>
    <div class="salary ${(v.salary_net_from || v.salary_net_to) ? "" : "none"}">${esc(sal)}</div>
    <div class="tags">
      ${skills.map((s) => `<span class="tag">${esc(s)}</span>`).join("")}
      ${missing.map((s) => `<span class="tag miss">нет: ${esc(s)}</span>`).join("")}
    </div>
    <div class="foot">
      <span class="tiny muted">${esc(v.published_at || "")}</span>
      ${v.url ? `<a href="${esc(v.url)}" target="_blank" rel="noopener">открыть на hh.ru ↗</a>` : ""}
    </div>`;
  return div;
}

/* ---- SSE ---- */
async function send(e) {
  e && e.preventDefault();
  const input = $("#input");
  const text = input.value.trim();
  if (!text || state.busy) return false;
  input.value = "";
  autogrow(input);
  addUserMsg(text);
  const turn = newTurn();
  state.busy = true;
  $("#pending").classList.remove("hidden");
  $("#send-btn").disabled = true;

  try {
    const resp = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + state.token,
      },
      body: JSON.stringify({ message: text, session_id: state.sessionId }),
    });
    if (!resp.ok) throw new Error("ошибка сервера " + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        handleFrame(frame, turn);
      }
    }
  } catch (err) {
    turn.setRefusal("Ошибка связи: " + err.message);
  } finally {
    state.busy = false;
    $("#pending").classList.add("hidden");
    $("#send-btn").disabled = false;
  }
  loadSessions();
  return false;
}

function handleFrame(frame, turn) {
  let ev = "message", data = {};
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event: ")) ev = line.slice(7).trim();
    else if (line.startsWith("data: ")) {
      try { data = JSON.parse(line.slice(6)); } catch (e) { /* мимо */ }
    }
  }
  switch (ev) {
    case "session":
      state.sessionId = data.session_id || "";
      if (state.sessionId) loadSessions();
      break;
    case "think": turn.addThink(data.text || ""); break;
    case "status": turn.addStatus(data.text || ""); break;
    case "tool_call": turn.addToolCall(data.name, data.args); break;
    case "tool_result": turn.addToolResult(data.name, data.payload); break;
    case "delta": turn.addDelta(data.text || ""); break;
    case "answer": turn.setAnswer(data.text || ""); break;
    case "refusal": turn.setRefusal(data.text || ""); break;
    case "error": turn.setRefusal(data.text || "ошибка агента"); break;
    case "done": turn.finish(data.tools || [], data.elapsed); break;
  }
}

/* ================= вкладка «Вакансии» ================= */
async function loadVacancies(e) {
  e && e.preventDefault();
  const p = new URLSearchParams({
    q: $("#v-q").value, city: $("#v-city").value,
    experience: $("#v-exp").value, salary_min: $("#v-salary").value || 0, limit: 12,
  });
  const data = await api("/api/vacancies?" + p);
  const box = $("#vacancy-cards");
  box.innerHTML = "";
  for (const v of data.vacancies) box.appendChild(vacancyCard(v));
  if (!data.vacancies.length) box.innerHTML = '<p class="muted">Ничего не найдено — измените фильтры.</p>';
  $("#v-method").textContent = data.method || "";
  return false;
}

async function loadMatch() {
  const data = await api("/api/match?profile=anastasia&top=8");
  const box = $("#vacancy-cards");
  box.innerHTML = "";
  for (const v of data.vacancies) box.appendChild(vacancyCard(v));
  $("#v-method").textContent = data.method || "";
}

/* ================= вкладка «Зарплаты» ================= */
async function loadSalary(e) {
  e && e.preventDefault();
  const p = new URLSearchParams({
    skill: $("#s-skill").value, city: $("#s-city").value,
    experience: $("#s-exp").value, period_days: $("#s-period").value,
  });
  const data = await api("/api/stats/salary?" + p);
  const strip = $("#salary-strip");
  strip.innerHTML = data.n
    ? `<div class="stat"><div class="v">${fmtMoney(data.p25_net)}</div><div class="l">P25</div></div>
       <div class="stat"><div class="v">${fmtMoney(data.median_net)}</div><div class="l">медиана</div></div>
       <div class="stat"><div class="v">${fmtMoney(data.p75_net)}</div><div class="l">P75</div></div>
       <div class="stat"><div class="v">n=${data.n}</div><div class="l">вакансий в выборке</div></div>`
    : `<p class="muted">${esc(data.note || "нет данных")}</p>`;
  const range = $("#salary-range");
  range.innerHTML = data.n ? `
    <div class="bar-row">
      <div class="bar-label">${esc($("#s-skill").value || "все")}</div>
      <div class="bar-track">${rangeBar(data.p25_net, data.median_net, data.p75_net, data.p75_net * 1.25)}</div>
      <div class="bar-val">${fmtMoney(data.median_net)}</div>
    </div>
    <p class="tiny muted">${esc(data.method || "")}</p>` : "";
  return false;
}

function rangeBar(p25, med, p75, max) {
  const pc = (v) => Math.min(100, (v / max) * 100);
  return `<div class="bar-fill" style="left:${pc(p25)}%;width:${Math.max(2, pc(p75) - pc(p25))}%">
    <div class="cap" style="left:${Math.max(0, ((med - p25) / Math.max(1, p75 - p25)) * 100)}%"></div>
  </div>`;
}

async function salaryCompare() {
  const skills = ["C#", ".NET", "python", "java", "sql", "docker"];
  const box = $("#salary-compare");
  box.dataset.loaded = "1";
  box.innerHTML = '<p class="muted">Считаю…</p>';
  const results = await Promise.all(skills.map((s) =>
    api("/api/stats/salary?skill=" + encodeURIComponent(s)).catch(() => null)));
  const rows = results.filter((r) => r && r.n >= 3);
  const max = Math.max(...rows.map((r) => r.p75_net), 1) * 1.15;
  box.innerHTML = rows.map((r, i) => `
    <div class="bar-row">
      <div class="bar-label">${esc(skills[i] || r.filters.skill)}</div>
      <div class="bar-track">${rangeBar(r.p25_net, r.median_net, r.p75_net, max)}</div>
      <div class="bar-val">${fmtMoney(r.median_net)} <span class="tiny muted">n=${r.n}</span></div>
    </div>`).join("") + '<p class="tiny muted">Полоса — диапазон P25…P75, засечка — медиана (net, на руки).</p>';
}

/* ================= вкладка «Навыки» ================= */
async function loadGap() {
  const prof = $("#g-profile").value;
  const data = await api(`/api/gap?profile=${encodeURIComponent(prof)}&top=25`);
  const maxShare = Math.max(...data.skills.map((s) => s.share_pct), 1);
  $("#gap-table").innerHTML = `
    <table>
      <tr><th>Навык</th><th>Доля на рынке</th><th>В профиле</th><th>Приоритет</th></tr>
      ${data.skills.map((s) => `
        <tr>
          <td>${esc(s.skill)}</td>
          <td><span class="share-bar" style="width:${(s.share_pct / maxShare) * 90}px"></span>${s.share_pct}% <span class="tiny muted">(${s.vacancies})</span></td>
          <td>${s.in_profile ? '<span class="badge ok">есть</span>' : '<span class="badge">нет</span>'}</td>
          <td>${badge(s.priority)}</td>
        </tr>`).join("")}
    </table>
    <p class="tiny muted" style="margin-top:10px">${esc(data.method || "")}</p>`;
}
function badge(p) {
  if (p === "высокий") return '<span class="badge high">высокий</span>';
  if (p === "средний") return '<span class="badge mid">средний</span>';
  if (p === "низкий") return '<span class="badge low">низкий</span>';
  return '<span class="badge low">—</span>';
}

/* ================= вкладка «Письма» (аккордеон) ================= */
async function loadLetters() {
  const data = await api("/api/outbox");
  const box = $("#letters-list");
  if (!data.letters.length) {
    box.innerHTML = '<p class="muted">Писем пока нет. Попросите агента в чате: «Напиши сопроводительное письмо на вакансию …».</p>';
    return;
  }
  box.innerHTML = "";
  for (const l of data.letters) box.appendChild(letterItem(l));
}

function letterItem(l) {
  const div = document.createElement("div");
  div.className = "letter-item";
  const date = l.created || (l.modified ? new Date(l.modified * 1000).toLocaleDateString("ru-RU") : "");
  div.innerHTML = `
    <div class="letter-head">
      <div class="lh-main">
        <div class="lh-title">${esc(l.title)}</div>
        <div class="lh-meta">${esc(l.employer || "работодатель не указан")}${l.vacancy ? " · " + esc(l.vacancy) : ""}${date ? " · " + esc(date) : ""}</div>
      </div>
      <span class="badge">черновик · не отправлено</span>
      <button class="s-btn lh-rename" title="Переименовать">✎</button>
      <span class="chev">▸</span>
    </div>
    <div class="letter-body hidden">
      <div class="lh-info">
        ${l.url ? `<a href="${esc(l.url)}" target="_blank" rel="noopener">открыть вакансию на hh.ru ↗</a>` : ""}
        <span class="tiny muted">тон: ${esc(l.tone || "—")} · создано: ${esc(l.created || "—")} · файл: ${esc(l.file)}</span>
      </div>
      <textarea class="letter-edit" spellcheck="false"></textarea>
      <div class="row-actions">
        <button class="btn-primary b-copy">Копировать текст</button>
        <button class="btn-ghost b-save">Сохранить правки</button>
        <button class="btn-ghost b-regen">Перегенерировать</button>
        <span class="tiny muted b-status"></span>
      </div>
    </div>`;
  const body = div.querySelector(".letter-body");
  const ta = div.querySelector(".letter-edit");
  const status = div.querySelector(".b-status");
  ta.value = l.body || "";
  div.querySelector(".letter-head").addEventListener("click", () => {
    const open = body.classList.toggle("hidden") === false;
    div.querySelector(".chev").textContent = open ? "▾" : "▸";
    div.classList.toggle("open", open);
  });
  div.querySelector(".lh-rename").addEventListener("click", async (e) => {
    e.stopPropagation();
    const title = await modalPrompt("Название черновика", "Название", l.title);
    if (title === null || !title) return;
    const r = await api("/api/outbox/" + encodeURIComponent(l.file),
      { method: "PATCH", body: JSON.stringify({ title }) });
    l.title = r.title;
    div.querySelector(".lh-title").textContent = r.title;
  });
  div.querySelector(".b-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(ta.value);
      status.textContent = "скопировано ✓ — вставьте в переписку с работодателем";
    } catch (e) {
      status.textContent = "выделите текст и скопируйте вручную";
    }
  });
  div.querySelector(".b-save").addEventListener("click", async () => {
    try {
      const r = await api("/api/outbox/" + encodeURIComponent(l.file),
        { method: "PATCH", body: JSON.stringify({ body: ta.value }) });
      l.body = r.body;
      ta.value = r.body;
      status.textContent = "правки сохранены ✓";
    } catch (e) {
      status.textContent = "ошибка: " + e.message;
    }
  });
  div.querySelector(".b-regen").addEventListener("click", async () => {
    status.textContent = "перегенерирую текст…";
    try {
      const r = await api("/api/outbox/" + encodeURIComponent(l.file) + "/regenerate",
        { method: "POST" });
      l.body = r.body;
      ta.value = r.body;
      status.textContent = "новый текст готов — можно копировать или поправить";
    } catch (e) {
      status.textContent = "ошибка: " + e.message;
    }
  });
  return div;
}

/* ================= вкладка «Отклики» ================= */
let trackerLookupTimer = null;
function trackerLookup(q) {
  clearTimeout(trackerLookupTimer);
  const menu = $("#t-search-menu");
  q = (q || "").trim();
  if (q.length < 2) { menu.classList.add("hidden"); return; }
  trackerLookupTimer = setTimeout(async () => {
    try {
      const data = await api("/api/vacancies?" + new URLSearchParams({ q, limit: 8 }));
      menu.innerHTML = "";
      for (const v of data.vacancies) {
        const it = document.createElement("div");
        it.className = "nsel-opt";
        it.innerHTML = `<b>${esc(v.name)}</b> <span class="dim">${esc(v.employer || "")} · id ${esc(v.vacancy_id)}</span>`;
        it.addEventListener("mousedown", (e) => {
          e.preventDefault();
          $("#t-vid").value = v.vacancy_id;
          $("#t-search").value = v.name;
          $("#t-picked").textContent =
            `выбрана вакансия: ${v.name}${v.employer ? " (" + v.employer + ")" : ""} · id ${v.vacancy_id}`;
          menu.classList.add("hidden");
        });
        menu.appendChild(it);
      }
      if (!data.vacancies.length)
        menu.innerHTML = '<div class="nsel-opt dim">ничего не найдено — попробуйте другие слова</div>';
      menu.classList.remove("hidden");
    } catch (e) { /* поиск не критичен */ }
  }, 350);
}

async function loadTracker() {
  const data = await api("/api/tracker");
  const box = $("#tracker-table");
  if (!data.responses.length) {
    box.innerHTML = '<p class="muted">Откликов пока нет — найдите вакансию выше и добавьте первый.</p>';
    return;
  }
  box.innerHTML = `
    <table>
      <tr><th>Вакансия</th><th>Название</th><th>Статус</th><th>Заметка</th><th>Обновлён</th></tr>
      ${data.responses.map((r) => `
        <tr>
          <td><a href="https://hh.ru/vacancy/${esc(r.vacancy_id)}" target="_blank" rel="noopener">${esc(r.vacancy_id)}</a></td>
          <td>${esc(r.name || "—")}</td>
          <td>
            <select class="mini" onchange="updateStatus('${esc(r.vacancy_id)}', this.value)">
              ${["черновик", "отправлено", "просмотрено", "приглашение", "отказ"]
                .map((s) => `<option ${s === r.status ? "selected" : ""}>${s}</option>`).join("")}
            </select>
          </td>
          <td>${esc(r.note || "")}</td>
          <td class="tiny muted">${esc((r.updated_at || "").replace("T", " "))}</td>
        </tr>`).join("")}
    </table>
    <p class="tiny muted" style="margin-top:10px">${esc(data.note || "")}</p>`;
  box.querySelectorAll("select").forEach(enhanceSelect);
}

async function addTracker(e) {
  e.preventDefault();
  await api("/api/tracker", {
    method: "POST",
    body: JSON.stringify({
      vacancy_id: $("#t-vid").value.trim(),
      status: $("#t-status").value,
      note: $("#t-note").value.trim(),
    }),
  });
  $("#t-vid").value = ""; $("#t-note").value = ""; $("#t-search").value = "";
  $("#t-picked").textContent = "";
  loadTracker();
  return false;
}
async function updateStatus(vid, status) {
  await api("/api/tracker", {
    method: "POST",
    body: JSON.stringify({ vacancy_id: vid, status, note: "" }),
  });
  loadTracker();
}

/* ================= старт ================= */
async function startApp() {
  try {
    const me = await api("/api/me");
    state.user = me.user;
    $("#user-email").textContent = me.user.email;
  } catch (e) { return; }
  $("#auth").classList.add("hidden");
  $("#app").classList.remove("hidden");
  if (!$("#messages").children.length) addWelcome();
  initEnhancements();
  loadSessions();
}

if (state.token) startApp();
else showAuth();
