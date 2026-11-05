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
let abortCtrl = null;  // прерывание текущей генерации (кнопка «Стоп»)

/* ================= утилиты ================= */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// мини-маркдаун: заголовки, таблицы, списки (марк. и нумер.), цитаты, код-блоки,
// hr, **жирный**, *курсив*, ~~зачёркнутый~~, `код`, ссылки (после экранирования!)
function md(text) {
  const lines = esc(text).split(/\r?\n/);
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/(^|[\s(])_([^_\n]+)_/g, "$1<i>$2</i>")
    .replace(/~~([^~]+)~~/g, "<s>$1</s>")
    .replace(/(https?:\/\/[^\s<)]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  let out = "", i = 0;
  let listOpen = "";   // "", "ul", "ol"
  let quoteOpen = false;
  const endBlocks = () => {
    if (listOpen) { out += "</" + listOpen + ">"; listOpen = ""; }
    if (quoteOpen) { out += "</blockquote>"; quoteOpen = false; }
  };
  while (i < lines.length) {
    const raw = lines[i];
    const l = raw.trim();
    // код-блок ``` … ```
    if (l.startsWith("```")) {
      endBlocks();
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) { buf.push(lines[i]); i++; }
      i++;
      out += "<pre><code>" + buf.join("\n") + "</code></pre>";
      continue;
    }
    // таблица | … |
    if (l.startsWith("|") && l.endsWith("|") && i + 1 < lines.length &&
        /^\|[\s:|-]+\|$/.test(lines[i + 1].trim())) {
      endBlocks();
      const cells = (row) => row.trim().slice(1, -1).split("|").map((c) => c.trim());
      const head = cells(lines[i]);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|") && lines[i].trim().endsWith("|")) {
        rows.push(cells(lines[i])); i++;
      }
      out += '<table class="md-table"><thead><tr>' +
        head.map((c) => "<th>" + inline(c) + "</th>").join("") + "</tr></thead><tbody>" +
        rows.map((r) => "<tr>" + r.map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("") +
        "</tbody></table>";
      continue;
    }
    if (!l) { endBlocks(); i++; continue; }
    // горизонтальная линия
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(l)) { endBlocks(); out += "<hr>"; i++; continue; }
    // заголовки
    const h = l.match(/^(#{1,6})\s+(.*)/);
    if (h) {
      endBlocks();
      const lvl = Math.min(h[1].length, 4);
      out += `<div class="md-h md-h${lvl}">${inline(h[2])}</div>`;
      i++; continue;
    }
    // цитата
    if (l.startsWith("&gt; ") || l.startsWith("&gt;")) {
      if (!quoteOpen) { endBlocks(); out += "<blockquote>"; quoteOpen = true; }
      out += "<p>" + inline(l.replace(/^&gt;\s?/, "")) + "</p>";
      i++; continue;
    }
    // нумерованный список
    const ol = l.match(/^(\d+)[.)]\s+(.*)/);
    if (ol) {
      if (listOpen !== "ol") { endBlocks(); out += "<ol>"; listOpen = "ol"; }
      out += "<li>" + inline(ol[2]) + "</li>";
      i++; continue;
    }
    // маркированный список
    const ul = l.match(/^[-•*+]\s+(.*)/);
    if (ul) {
      if (listOpen !== "ul") { endBlocks(); out += "<ul>"; listOpen = "ul"; }
      out += "<li>" + inline(ul[1]) + "</li>";
      i++; continue;
    }
    // обычный абзац
    endBlocks();
    out += "<p>" + inline(l) + "</p>";
    i++;
  }
  endBlocks();
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
  // загрузка резюме: drag&drop + выбор файла
  const dz = $("#drop-zone"), fileInput = $("#resume-file");
  if (dz && !dz.dataset.wired) {
    dz.dataset.wired = "1";
    dz.addEventListener("click", (e) => {
      if (e.target.tagName !== "BUTTON") fileInput.click();
    });
    ["dragenter", "dragover"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", (e) => {
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) parseResumeFile(f);
    });
    fileInput.addEventListener("change", () => {
      const f = fileInput.files && fileInput.files[0];
      fileInput.value = "";
      if (f) parseResumeFile(f);
    });
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

/* ================= вкладки (компактное бургер-меню) ================= */
const TAB_LABELS = { chat: "Чат", vacancies: "Вакансии", salary: "Зарплаты",
  skills: "Навыки", letters: "Письма", tracker: "Отклики" };

function toggleNav(e) {
  e && e.stopPropagation();
  const nav = $("#nav");
  const open = nav.classList.toggle("hidden") === false;
  $("#nav-burger").setAttribute("aria-expanded", open ? "true" : "false");
}
function closeNav() {
  $("#nav").classList.add("hidden");
  const b = $("#nav-burger");
  if (b) b.setAttribute("aria-expanded", "false");
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".nav-wrap")) closeNav();
});

document.addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if (!t) return;
  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
  t.classList.add("active");
  const name = t.dataset.tab;
  const label = $("#nav-current");
  if (label) label.textContent = TAB_LABELS[name] || "Чат";
  closeNav();
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $("#view-" + name).classList.remove("hidden");
  if (name === "salary" && !$("#salary-compare").dataset.loaded) salaryCompare();
  if (name === "salary") loadTrend();
  if (name === "skills") loadProfile();
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
  el.style.height = Math.min(el.scrollHeight, 220) + "px";
}
function onKey(e) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(e); return false; }
}
function stopGeneration() {
  if (abortCtrl) abortCtrl.abort();
}

/* ---- список диалогов: название + переименование/удаление ---- */
function toggleSessions() {
  // мобильные: выдвижная плашка поверх чата; десктоп: свернуть/развернуть панель
  if (window.innerWidth <= 860) {
    document.body.classList.toggle("sessions-open");
    document.body.classList.remove("sessions-collapsed");
  } else {
    document.body.classList.toggle("sessions-collapsed");
    document.body.classList.remove("sessions-open");
  }
}
function closeSessions() {
  if (window.innerWidth <= 860) document.body.classList.remove("sessions-open");
  else document.body.classList.add("sessions-collapsed");
}

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
    // плашка: счётчик диалогов и название текущего
    const count = $("#sessions-count");
    if (count) count.textContent = "Диалоги" + (data.sessions.length ? " (" + data.sessions.length + ")" : "");
    const cur = data.sessions.find((s) => s.session_id === state.sessionId);
    const titleEl = $("#current-dialog-title");
    if (titleEl) titleEl.textContent = cur ? cur.title || "Диалог" : "";
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
  document.body.classList.remove("sessions-open");  // на мобильном — закрыть плашку
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
      <button class="chip" onclick="quick(this)">Подбери вакансии под мои навыки</button>
      <button class="chip" onclick="quick(this)">Какие навыки мне стоит подтянуть?</button>
      <button class="chip" onclick="quick(this)">Сколько платят в моей сфере?</button>
      <button class="chip" onclick="quick(this)">Помоги составить сопроводительное письмо</button>
      <button class="chip" onclick="quick(this)">Покажи мои отклики</button>
    </div>`;
  $("#messages").appendChild(div);
  scrollChat();
}

async function openSession(id) {
  state.sessionId = id;
  document.body.classList.remove("sessions-open");  // на мобильном — закрыть плашку
  const data = await api(`/api/sessions/${id}/history`);
  const box = $("#messages");
  box.innerHTML = "";
  for (const m of data.messages) {
    if (m.role === "user") { addUserMsg(m.text); continue; }
    // ход агента восстанавливается целиком: рассуждения (think), карточки инструментов
    // (кликабельные, как в живом потоке) и текст ответа
    const t = newTurn();
    if (m.think) t.addThink(m.think);
    for (const tl of m.tools || []) t.addToolResult(tl.name, tl.payload);
    if (m.text) t.setAnswer(m.text);
    t.finish([], 0);
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

// человекочитаемые подписи справочников hh
const EXP_LABELS = { noExperience: "нет опыта", between1And3: "от 1 года до 3 лет",
  between3And6: "от 3 до 6 лет", moreThan6: "более 6 лет" };
const SCHEDULE_LABELS = { remote: "удалённо", fullDay: "полный день", flexible: "гибкий график",
  shift: "сменный", flyInFlyOut: "вахта" };
const EMPLOYMENT_LABELS = { full: "полная занятость", part: "частичная занятость",
  project: "проектная работа", volunteer: "волонтёрство", probation: "стажировка" };

async function loadVacancyDetails(vid, box) {
  box.dataset.loaded = "1";
  box.innerHTML = '<p class="tiny muted">загружаю карточку…</p>';
  try {
    const d = await api("/api/vacancies/" + encodeURIComponent(vid));
    const ks = (d.key_skills || []).map((s) => s.canon || s).slice(0, 12);
    box.innerHTML = `
      <div class="vd-grid">
        <div><span>опыт</span>${esc(EXP_LABELS[d.experience] || d.experience || "—")}</div>
        <div><span>график</span>${esc(SCHEDULE_LABELS[d.schedule] || d.schedule || "—")}</div>
        <div><span>занятость</span>${esc(EMPLOYMENT_LABELS[d.employment] || d.employment || "—")}</div>
        <div><span>опубликовано</span>${esc((d.published_at || "").slice(0, 10) || "—")}</div>
      </div>
      ${ks.length ? `<div class="tags">${ks.map((s) => `<span class="tag">${esc(s)}</span>`).join("")}</div>` : ""}
      ${d.requirement ? `<h4>Требования</h4><div class="vd-text">${md(d.requirement)}</div>` : ""}
      ${d.responsibility ? `<h4>Обязанности</h4><div class="vd-text">${md(d.responsibility)}</div>` : ""}
      ${d.description ? `<h4>Описание</h4><div class="vd-text">${md(d.description)}</div>` : ""}
      ${d.url ? `<div class="row-actions"><a class="btn-ghost" href="${esc(d.url)}" target="_blank" rel="noopener">Открыть вакансию на hh.ru ↗</a></div>` : ""}`;
  } catch (err) {
    box.innerHTML = `<p class="tiny muted">не удалось загрузить карточку: ${esc(err.message)}</p>`;
  }
}

function vacancyCard(v) {
  const div = document.createElement("div");
  div.className = "vcard expandable";
  const sal = (v.salary_net_from || v.salary_net_to)
    ? `${v.salary_net_from ? fmtMoney(v.salary_net_from) : ""}${v.salary_net_from && v.salary_net_to ? " – " : ""}${v.salary_net_to ? fmtMoney(v.salary_net_to) : ""} на руки`
    : "з/п не указана";
  const skills = (v.top_skills || v.matched_skills || []).slice(0, 5);
  const missing = (v.missing_skills || []).slice(0, 3);
  div.innerHTML = `
    <div class="vcard-head">
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
        <button class="link-btn" onclick="event.stopPropagation(); similarTo('${esc(v.vacancy_id)}')">похожие ↻</button>
        ${v.url ? `<a href="${esc(v.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">hh.ru ↗</a>` : ""}
      </div>
      <div class="expand-hint tiny muted">подробнее ▾</div>
    </div>
    <div class="vcard-details hidden"></div>`;
  const head = div.querySelector(".vcard-head");
  const det = div.querySelector(".vcard-details");
  head.addEventListener("click", () => {
    const open = det.classList.toggle("hidden") === false;
    div.classList.toggle("open", open);
    div.querySelector(".expand-hint").textContent = open ? "свернуть ▴" : "подробнее ▾";
    if (open && !det.dataset.loaded) loadVacancyDetails(v.vacancy_id, det);
  });
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
  abortCtrl = new AbortController();
  $("#pending").classList.remove("hidden");
  $("#send-btn").disabled = true;
  $("#stop-btn").classList.remove("hidden");

  try {
    const resp = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + state.token,
      },
      body: JSON.stringify({ message: text, session_id: state.sessionId }),
      signal: abortCtrl.signal,
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
    if (err && err.name === "AbortError") {
      // пользователь нажал «Стоп» — не ошибка
      turn.addStatus("⏹ генерация остановлена");
      turn.finish([], 0);
    } else {
      turn.setRefusal("Ошибка связи: " + err.message);
    }
  } finally {
    state.busy = false;
    abortCtrl = null;
    $("#pending").classList.add("hidden");
    $("#send-btn").disabled = false;
    $("#stop-btn").classList.add("hidden");
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
  const sem = $("#v-mode").value === "sem";
  const box = $("#vacancy-cards");
  box.innerHTML = '<p class="muted">Ищу…</p>';
  try {
    let data;
    if (sem) {
      const p = new URLSearchParams({
        q: $("#v-q").value, city: $("#v-city").value, limit: 12,
      });
      data = await api("/api/semantic?" + p);
    } else {
      const p = new URLSearchParams({
        q: $("#v-q").value, city: $("#v-city").value,
        experience: $("#v-exp").value, salary_min: $("#v-salary").value || 0, limit: 12,
      });
      data = await api("/api/vacancies?" + p);
    }
    box.innerHTML = "";
    for (const v of data.vacancies) box.appendChild(vacancyCard(v));
    if (!data.vacancies.length) box.innerHTML = '<p class="muted">Ничего не найдено — измените фильтры.</p>';
    $("#v-method").textContent = data.method || data.note || "";
  } catch (err) {
    box.innerHTML = `<p class="muted">Ошибка: ${esc(err.message)}</p>`;
  }
  return false;
}

async function loadFresh() {
  const box = $("#vacancy-cards");
  box.innerHTML = '<p class="muted">Смотрю свежие вакансии…</p>';
  try {
    await flushProfileIfDirty();
    const data = await api("/api/fresh?days=7&limit=12");
    box.innerHTML = "";
    if (data.error) {
      box.innerHTML = `<p class="muted">${esc(data.error)}. ${esc(data.hint || "")}</p>`;
    } else if (!data.vacancies.length) {
      box.innerHTML = '<p class="muted">За 7 дней новых вакансий нет.</p>';
    } else {
      for (const v of data.vacancies) box.appendChild(vacancyCard(v));
    }
    $("#v-method").textContent = data.method || "";
  } catch (err) {
    box.innerHTML = `<p class="muted">Ошибка: ${esc(err.message)}</p>`;
  }
}

async function similarTo(vid) {
  // похожие вакансии — показываем на вкладке «Вакансии»
  document.querySelector('[data-tab="vacancies"]').click();
  const box = $("#vacancy-cards");
  box.innerHTML = '<p class="muted">Ищу похожие вакансии…</p>';
  try {
    const data = await api(`/api/similar/${encodeURIComponent(vid)}?limit=8`);
    box.innerHTML = "";
    for (const v of data.vacancies) box.appendChild(vacancyCard(v));
    if (!data.vacancies.length) box.innerHTML = '<p class="muted">Похожих не найдено.</p>';
    $("#v-method").textContent = data.method || "";
  } catch (err) {
    box.innerHTML = `<p class="muted">Ошибка: ${esc(err.message)}</p>`;
  }
}

async function loadMatch() {
  const box = $("#vacancy-cards");
  box.innerHTML = '<p class="muted">Подбираю под ваше резюме…</p>';
  try {
    await flushProfileIfDirty();
    const data = await api("/api/match?top=8");
    box.innerHTML = "";
    if (data.error) {
      box.innerHTML = `<p class="muted">${esc(data.error)}. ${esc(data.hint || "")}</p>`;
      $("#v-method").textContent = "";
      return;
    }
    for (const v of data.vacancies) box.appendChild(vacancyCard(v));
    $("#v-method").textContent = data.method || "";
  } catch (err) {
    box.innerHTML = `<p class="muted">Ошибка: ${esc(err.message)}</p>`;
  }
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
  loadTrend();
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

async function loadTrend() {
  const box = $("#salary-trend");
  box.innerHTML = '<p class="muted">Считаю динамику…</p>';
  try {
    const p = new URLSearchParams({
      skill: $("#s-skill").value, city: $("#s-city").value, months: 24,
    });
    const data = await api("/api/stats/salary_trend?" + p);
    if (!data.trend || !data.trend.length) {
      box.innerHTML = `<p class="muted">${esc(data.note || "нет данных")}</p>`;
      return;
    }
    const max = Math.max(...data.trend.map((t) => t.median_net), 1) * 1.15;
    box.innerHTML = data.trend.map((t) => `
      <div class="bar-row">
        <div class="bar-label">${esc(t.month)}</div>
        <div class="bar-track"><div class="bar-fill" style="left:0;width:${Math.max(2, (t.median_net / max) * 100)}%"></div></div>
        <div class="bar-val">${fmtMoney(t.median_net)} <span class="tiny muted">n=${t.n}</span></div>
      </div>`).join("") +
      `<p class="tiny muted">${esc(data.method || "")}</p>`;
  } catch (err) {
    box.innerHTML = `<p class="muted">Ошибка: ${esc(err.message)}</p>`;
  }
}

/* ================= моё резюме (вкладка «Навыки») ================= */
let profileSkills = [];   // [{name, weight}] — чипсы в форме
let profileDirty = false; // форма изменена и ещё не сохранена на сервер
let skillDict = [];       // словарь навыков из БД (GET /api/skills)
let skillHintIdx = -1;    // подсвеченная строка в выпадающем списке
let workPlaces = [{ company: "", experience: "" }]; // пары «место работы + опыт»

function workPlacesFromData(d) {
  if (d.work_places && d.work_places.length) {
    return d.work_places.map((p) => ({ company: p.company || "", experience: p.experience || "" }));
  }
  // миграция старых профилей: companies + общий summary -> пары
  const cs = d.companies || [];
  const exp = d.summary || d.work_experience || "";
  if (cs.length) return cs.map((c, i) => ({ company: c, experience: i === 0 ? exp : "" }));
  return [{ company: "", experience: exp }];
}

function renderWorkPlaces() {
  const box = $("#p-places");
  box.innerHTML = "";
  workPlaces.forEach((p, idx) => {
    const div = document.createElement("div");
    div.className = "work-place";
    div.innerHTML = `
      <div class="wp-head">
        <input class="wp-company" placeholder="Название компании">
        <button type="button" class="wp-remove" title="убрать место работы">✕</button>
      </div>
      <textarea class="wp-exp" rows="3"
        placeholder="Опыт работы: задачи, проекты, результаты"></textarea>`;
    const company = div.querySelector(".wp-company");
    const exp = div.querySelector(".wp-exp");
    company.value = p.company;
    exp.value = p.experience;
    company.oninput = () => { p.company = company.value; profileDirty = true; };
    exp.oninput = () => { p.experience = exp.value; profileDirty = true; };
    div.querySelector(".wp-remove").onclick = () => {
      workPlaces.splice(idx, 1);
      if (!workPlaces.length) workPlaces.push({ company: "", experience: "" });
      profileDirty = true;
      renderWorkPlaces();
    };
    box.appendChild(div);
  });
}

function addWorkPlace() {
  workPlaces.push({ company: "", experience: "" });
  profileDirty = true;
  renderWorkPlaces();
  const inputs = document.querySelectorAll("#p-places .wp-company");
  if (inputs.length) inputs[inputs.length - 1].focus();
}

async function loadSkillDict() {
  try {
    const data = await api("/api/skills");
    skillDict = data.skills || [];
  } catch (err) {
    skillDict = [];
  }
}

function skillMatches(q) {
  q = (q || "").trim().toLowerCase();
  if (!q) return [];
  return skillDict
    .filter((s) => s.toLowerCase().includes(q))
    .sort((a, b) => {
      const ai = a.toLowerCase().indexOf(q), bi = b.toLowerCase().indexOf(q);
      return ai - bi || a.length - b.length;
    })
    .slice(0, 8);
}

function showSkillHints() {
  const box = $("#p-skill-hints");
  const list = skillMatches($("#p-skill-input").value);
  if (!list.length) { hideSkillHints(); return; }
  if (skillHintIdx >= list.length) skillHintIdx = list.length - 1;
  box.innerHTML = list.map((s, i) =>
    `<div class="skill-hint${i === skillHintIdx ? " active" : ""}" data-name="${esc(s)}">${esc(s)}</div>`
  ).join("");
  box.querySelectorAll(".skill-hint").forEach((el) => {
    el.onmousedown = (e) => { e.preventDefault(); pickSkill(el.dataset.name); };
  });
  box.classList.remove("hidden");
}

function hideSkillHints() {
  $("#p-skill-hints").classList.add("hidden");
  skillHintIdx = -1;
}

function pickSkill(name) {
  hideSkillHints();
  if (!profileSkills.some((s) => s.name.toLowerCase() === name.toLowerCase())) {
    profileSkills.push({ name, weight: "core" });
    profileDirty = true;
    renderSkillChips();
  }
  const input = $("#p-skill-input");
  input.value = "";
  input.focus();
}

function renderSkillChips() {
  const box = $("#p-skills");
  box.innerHTML = "";
  if (!profileSkills.length) {
    box.innerHTML = '<span class="tiny muted">пока нет навыков — добавьте стек (C#, .NET, SQL…)</span>';
    return;
  }
  for (const s of profileSkills) {
    const chip = document.createElement("span");
    chip.className = "skill-chip";
    chip.innerHTML = `${esc(s.name)}<button type="button" title="убрать">✕</button>`;
    chip.querySelector("button").onclick = () => {
      profileSkills = profileSkills.filter((x) => x.name !== s.name);
      profileDirty = true;
      renderSkillChips();
    };
    box.appendChild(chip);
  }
}

function addSkillKey(e) {
  const hints = [...$("#p-skill-hints").querySelectorAll(".skill-hint")];
  if (e.key === "ArrowDown" && hints.length) {
    e.preventDefault();
    skillHintIdx = Math.min(skillHintIdx + 1, hints.length - 1);
    showSkillHints();
    return false;
  }
  if (e.key === "ArrowUp" && hints.length) {
    e.preventDefault();
    skillHintIdx = Math.max(skillHintIdx - 1, 0);
    showSkillHints();
    return false;
  }
  if (e.key === "Escape") { hideSkillHints(); return false; }
  if (e.key === "Enter") {
    e.preventDefault();
    const active = hints[skillHintIdx];
    if (active) pickSkill(active.dataset.name);
    else addSkill();
    return false;
  }
}

function addSkill() {
  const input = $("#p-skill-input");
  const name = (input.value || "").trim();
  if (!name) return;
  // только словарь БД: точное совпадение или единственное явное совпадение префикса
  const exact = skillDict.find((s) => s.toLowerCase() === name.toLowerCase());
  const near = skillMatches(name);
  const hit = exact || (near.length === 1 && near[0].toLowerCase().startsWith(name.toLowerCase()))
    ? (exact || near[0]) : null;
  if (!hit) {
    $("#p-status").textContent = near.length
      ? "выберите навык из списка совпадений — своё значение внести нельзя"
      : "навык «" + name + "» не найден в словаре — выберите из списка";
    showSkillHints();
    return;
  }
  $("#p-status").textContent = "";
  pickSkill(hit);
}

function focusProfileForm() {
  $("#profile-form").scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => $("#p-name").focus(), 250);
}

async function loadProfile() {
  try {
    if (!skillDict.length) await loadSkillDict();
    const data = await api("/api/profile");
    const d = data.data;
    state.profileId = data.profile_id;
    $("#p-name").value = d.name || "";
    $("#p-title").value = d.title || "";
    $("#p-city").value = d.location || "";
    $("#p-exp").value = d.experience_years || "";
    $("#p-salary").value = (d.preferences && d.preferences.salary_net) || "";
    workPlaces = workPlacesFromData(d);
    renderWorkPlaces();
    profileSkills = (d.skills || []).map((s) =>
      typeof s === "string" ? { name: s, weight: "core" } : { name: s.name, weight: s.weight || "core" });
    profileDirty = false;
    renderSkillChips();
    $("#resume-empty").classList.toggle("hidden", !data.empty);
    $("#p-status").textContent = data.empty
      ? "резюме ещё не заполнено — прикрепите файл или заполните форму"
      : `навыков: ${profileSkills.length} · id: ${data.profile_id}`;
  } catch (err) {
    $("#p-status").textContent = "ошибка загрузки: " + err.message;
  }
}

async function saveProfile(quiet) {
  addSkill();  // последний введённый навык — тоже в дело
  try {
    const r = await api("/api/profile", {
      method: "PUT",
      body: JSON.stringify({
        name: $("#p-name").value,
        title: $("#p-title").value,
        location: $("#p-city").value,
        experience_years: parseInt($("#p-exp").value || "0", 10),
        salary_net: parseInt($("#p-salary").value || "0", 10),
        work_places: workPlaces.filter((p) => (p.company + p.experience).trim()),
        skills: profileSkills.map((s) => s.name),
      }),
    });
    workPlaces = workPlacesFromData(r.data);
    renderWorkPlaces();
    profileSkills = (r.data.skills || []).map((s) =>
      typeof s === "string" ? { name: s, weight: "core" } : { name: s.name, weight: s.weight || "core" });
    profileDirty = false;
    renderSkillChips();
    $("#resume-empty").classList.add("hidden");
    if (!quiet) $("#p-status").textContent = "резюме сохранено ✓ (подбор и навыки пересчитаются)";
    return true;
  } catch (err) {
    if (!quiet) $("#p-status").textContent = "ошибка: " + err.message;
    return false;
  }
}

async function flushProfileIfDirty() {
  // расчёты (разрыв навыков, подбор, свежие) всегда идут по сохранённому резюме:
  // несохранённые правки формы сначала фиксируем на сервере
  if (profileDirty) await saveProfile(true);
}

function applyDraft(draft, note) {
  if (draft.name) $("#p-name").value = draft.name;
  if (draft.title) $("#p-title").value = draft.title;
  if (draft.location) $("#p-city").value = draft.location;
  if (draft.experience_years) $("#p-exp").value = draft.experience_years;
  if ((draft.work_places && draft.work_places.length) || draft.summary ||
      (draft.companies || []).length) {
    workPlaces = workPlacesFromData(draft);
    renderWorkPlaces();
  }
  const parsed = draft.skills || [];
  if (parsed.length) {
    profileSkills = parsed.map((n) => ({ name: n, weight: "core" }));
    renderSkillChips();
  }
  profileDirty = true;
  $("#resume-empty").classList.add("hidden");
  $("#p-status").textContent = note || "проверьте данные и нажмите «Сохранить резюме»";
  $("#profile-form").scrollIntoView({ behavior: "smooth", block: "center" });
}

async function parseResumeFile(file) {
  if (!file) return;
  const status = $("#p-status");
  status.textContent = "разбираю файл «" + file.name + "»…";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch("/api/profile/parse_file", {
      method: "POST",
      headers: { Authorization: "Bearer " + state.token },
      body: fd,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || ("ошибка " + r.status));
    applyDraft(data.draft, data.note + " (файл: " + (data.source || file.name) + ")");
  } catch (err) {
    status.textContent = "не удалось разобрать файл: " + err.message;
  }
}

async function parseResumeText() {
  const text = ($("#resume-text").value || "").trim();
  const status = $("#p-status");
  if (text.length < 50) {
    status.textContent = "вставьте текст резюме (минимум 50 символов)";
    return;
  }
  status.textContent = "разбираю текст…";
  try {
    const data = await api("/api/profile/parse", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    applyDraft(data.draft, data.note);
  } catch (err) {
    status.textContent = "не удалось разобрать текст: " + err.message;
  }
}

/* ================= вкладка «Навыки» ================= */
async function loadGap() {
  const prof = $("#g-profile").value;
  const box = $("#gap-table");
  try {
    if (!prof) await flushProfileIfDirty();  // разрыв — по актуальному резюме
    const data = await api(`/api/gap?profile=${encodeURIComponent(prof)}&top=25`);
    if (data.error) {
      box.innerHTML = `<p class="muted">${esc(data.error)}. ${esc(data.hint || "")}</p>`;
      return;
    }
    const rows = [...(data.skills || []), ...(data.covered || [])];
    const maxShare = Math.max(...rows.map((s) => s.share_pct), 1);
    const rowHtml = (s) => `
          <tr>
            <td>${esc(s.skill)}</td>
            <td><span class="share-bar" style="width:${(s.share_pct / maxShare) * 90}px"></span>${s.share_pct}% <span class="tiny muted">(${s.vacancies})</span></td>
            <td>${s.in_profile ? '<span class="badge ok">есть</span>' : '<span class="badge">нет</span>'}</td>
            <td>${badge(s.priority)}</td>
          </tr>`;
    box.innerHTML = `
      <table>
        <tr><th>Навык</th><th>Доля на рынке</th><th>В резюме</th><th>Приоритет</th></tr>
        ${(data.skills || []).map(rowHtml).join("")}
        ${(data.covered || []).map(rowHtml).join("")}
      </table>
      <p class="tiny muted" style="margin-top:10px">${esc(data.method || "")}</p>`;
  } catch (err) {
    box.innerHTML = `<p class="muted">Ошибка: ${esc(err.message)}</p>`;
  }
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
        <button class="btn-ghost b-dl">Скачать .txt</button>
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
  div.querySelector(".b-dl").addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([ta.value], { type: "text/plain;charset=utf-8" }));
    a.download = (l.title || "письмо").replace(/[\\/:*?"<>|]/g, "_") + ".txt";
    a.click();
    URL.revokeObjectURL(a.href);
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
  state.trackerRows = data.responses;
  if (!data.responses.length) {
    box.innerHTML = '<p class="muted">Откликов пока нет — найдите вакансию выше и добавьте первый.</p>';
    return;
  }
  const today = new Date().toISOString().slice(0, 10);
  const followCell = (r) => {
    if (!r.follow_up_at) return '<span class="tiny muted">—</span>';
    const due = r.follow_up_at <= today && r.status !== "отказ" && r.status !== "приглашение";
    return `<span class="badge ${due ? "high" : "mid"}">🔔 ${esc(r.follow_up_at)}</span>`;
  };
  box.innerHTML = `
    <table>
      <tr><th>Вакансия</th><th>Название</th><th>Статус</th><th>Напомнить</th><th>Заметка</th><th>Обновлён</th></tr>
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
          <td>${followCell(r)}</td>
          <td>${esc(r.note || "")}</td>
          <td class="tiny muted">${esc((r.updated_at || "").replace("T", " "))}</td>
        </tr>`).join("")}
    </table>
    <p class="tiny muted" style="margin-top:10px">${esc(data.note || "")}</p>`;
  box.querySelectorAll("select").forEach(enhanceSelect);
}

function exportTrackerCsv() {
  const rows = state.trackerRows || [];
  if (!rows.length) return;
  const head = ["vacancy_id", "name", "status", "follow_up_at", "note", "updated_at", "url"];
  const q = (s) => '"' + String(s ?? "").replace(/"/g, '""') + '"';
  const csv = "\uFEFF" + [head.join(";")].concat(rows.map((r) =>
    [r.vacancy_id, r.name, r.status, r.follow_up_at, r.note, r.updated_at,
     "https://hh.ru/vacancy/" + r.vacancy_id].map(q).join(";"))).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  a.download = "отклики_" + new Date().toISOString().slice(0, 10) + ".csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

async function addTracker(e) {
  e.preventDefault();
  await api("/api/tracker", {
    method: "POST",
    body: JSON.stringify({
      vacancy_id: $("#t-vid").value.trim(),
      status: $("#t-status").value,
      note: $("#t-note").value.trim(),
      follow_up: $("#t-follow").value || "",
    }),
  });
  $("#t-vid").value = ""; $("#t-note").value = ""; $("#t-search").value = "";
  $("#t-follow").value = "";
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
