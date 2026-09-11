/* ============================================================================
   StudyBuddy chat frontend
   Talks to the FastAPI backend (/api/chat, /api/subjects, /api/sessions).
   ========================================================================== */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    subjects: {},        // id -> SubjectInfo
    subjectId: null,
    sessionId: null,
    turns: [],           // local transcript for the active session
    busy: false,
  };

  /* ------------------------- tiny utilities ------------------------------ */
  const htmlEsc = (s) =>
    String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function inlineMd(s) {
    return htmlEsc(s)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\$([^$\n]+)\$/g, '<span class="math">$1</span>')
      .replace(/`([^`\n]+)`/g, "<code>$1</code>");
  }

  function md(text) {
    const out = [];
    let inList = false;
    for (const raw of String(text).split(/\n/)) {
      const line = raw.trim();
      if (!line) {
        inList = false;
        continue;
      }
      if (/^[-*•]\s+/.test(line)) {
        if (!inList) {
          out.push("<ul>");
          inList = true;
        }
        out.push("<li>" + inlineMd(line.replace(/^[-*•]\s+/, "")) + "</li>");
      } else {
        if (inList) {
          out.push("</ul>");
          inList = false;
        }
        out.push("<p>" + inlineMd(line) + "</p>");
      }
    }
    if (inList) out.push("</ul>");
    return out.join("");
  }

  const timeOf = (ts) => {
    if (!ts) return "";
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  const ago = (ts) => {
    if (!ts) return "";
    const s = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
    if (s < 1) return "now";
    if (s < 60) return s + "m";
    const h = Math.round(s / 60);
    return h < 24 ? h + "h" : Math.round(h / 24) + "d";
  };

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      /* empty body */
    }
    if (!res.ok) {
      throw new Error((data && (data.detail || data.message)) || "Request failed (" + res.status + ")");
    }
    return data;
  }

  /* ------------------------- theme --------------------------------------- */
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    $("themeLight").classList.toggle("is-active", theme === "light");
    $("themeDark").classList.toggle("is-active", theme === "dark");
    $("themeLight").setAttribute("aria-pressed", theme === "light" ? "true" : "false");
    $("themeDark").setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  }

  function initTheme() {
    const saved = localStorage.getItem("sb-theme");
    const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(saved || (dark ? "dark" : "light"));
    localStorage.setItem("sb-theme", saved || (dark ? "dark" : "light"));
  }

  /* ------------------------- toast ---------------------------------------- */
  let toastTimer = null;
  function toast(msg, kind) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.toggle("error", kind === "error");
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.hidden = true;
    }, 4200);
  }

  /* =========================================================================
   SUBJECTS & SESSIONS (sidebar)
   ========================================================================= */
  function renderSubjects() {
    const list = $("subjectList");
    list.innerHTML = "";
    for (const sub of Object.values(state.subjects)) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "subject-card" + (sub.id === state.subjectId ? " is-active" : "");
      btn.dataset.sub = sub.id;
      btn.setAttribute("role", "listitem");
      btn.innerHTML =
        htmlEsc(sub.label) +
        '<span class="subject-sub">' +
        htmlEsc(sub.blurb || "") +
        "</span><span class=\"subject-meta\">" +
        sub.documents +
        " docs · " +
        sub.chunks +
        " chunks</span>";
      btn.addEventListener("click", () => setSubject(sub.id));
      list.appendChild(btn);
    }
    if (!list.children.length) {
      list.innerHTML = '<p class="nav-empty">No subjects configured. Add folders under /data/.</p>';
    }
  }

  async function renderSessions() {
    let sessions = [];
    try {
      sessions = (await fetchJSON("/api/sessions")).sessions || [];
    } catch (_) {
      sessions = [];
    }
    const list = $("sessionList");
    list.innerHTML = "";
    if (!sessions.length) {
      list.innerHTML = '<p class="nav-empty">Conversations you start appear here. Nothing is stored until you chat.</p>';
      return;
    }
    for (const s of sessions) {
      const row = document.createElement("div");
      row.className = "session-item" + (s.id === state.sessionId ? " is-active" : "");
      row.setAttribute("role", "listitem");
      row.innerHTML =
        '<span class="session-title" title="' +
        htmlEsc(s.title) +
        '">' +
        htmlEsc(s.title) +
        '</span><span class="session-when">' +
        ago(s.updated) +
        '</span><button type="button" class="session-del" data-del="' +
        s.id +
        '" aria-label="Delete conversation"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M5 7 L19 7 M9 7 L10 4 L14 4 L15 7 M7 7 L8 19 L16 19 L17 7 M10 10.5 L10 16 M14 10.5 L14 16"/></svg></button>';
      row.querySelector(".session-title").addEventListener("click", () => loadSession(s.id));
      row.querySelector(".session-del").addEventListener("click", async (ev) => {
        ev.stopPropagation();
        try {
          await fetchJSON("/api/sessions/" + s.id, { method: "DELETE" });
          if (state.sessionId === s.id) newChat();
          renderSessions();
        } catch (e) {
          toast("Could not delete session: " + e.message, "error");
        }
      });
      list.appendChild(row);
    }
  }

  /* =========================================================================
     SUBJECT SWITCHER
     ========================================================================= */
  function setSubject(id, opts) {
    if (!state.subjects[id]) return;
    state.subjectId = id;
    localStorage.setItem("sb-subject", id);
    renderSubjects();
    const sub = state.subjects[id];
    $("crumbLabel").textContent = sub.label;
    const pill = $("groundedPill");
    pill.classList.toggle("ok", sub.chunks > 0);
    pill.classList.toggle("warn", sub.chunks === 0);
    $("groundedText").textContent =
      sub.chunks > 0
        ? "Grounded in " + sub.label + " syllabus · " + sub.chunks + " passages"
        : "Not ingested yet — run `python ingest.py`";
    closeSidebar();
    if (!state.turns.length && !opts) renderEmpty();
  }

  /* =========================================================================
     SIDEBAR (mobile off-canvas)
     ========================================================================= */
  function openSidebar() {
    document.querySelector(".app").classList.add("sidebar-open");
    $("menuBtn").setAttribute("aria-expanded", "true");
    const sc = $("scrim");
    sc.hidden = false;
    requestAnimationFrame(() => sc.classList.add("show"));
  }
  function closeSidebar() {
    document.querySelector(".app").classList.remove("sidebar-open");
    $("menuBtn").setAttribute("aria-expanded", "false");
    const sc = $("scrim");
    sc.classList.remove("show");
    setTimeout(() => {
      sc.hidden = true;
    }, 240);
  }

  /* =========================================================================
   THREAD RENDERING
   ========================================================================= */
  function ensureWrap() {
    let wrap = document.querySelector(".thread-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "thread-wrap";
      $("thread").appendChild(wrap);
    }
    return wrap;
  }

  function scrollThread() {
    const t = $("thread");
    t.scrollTo({ top: t.scrollHeight, behavior: "smooth" });
  }

  function appendMessage(turn, animate) {
    const wrap = ensureWrap();
    const isUser = turn.role === "user";
    const msg = document.createElement("article");
    msg.className = "msg" + (isUser ? " msg-user" : "");
    if (!animate) msg.style.animation = "none";

    const avatar = document.createElement("div");
    avatar.className = "msg-avatar" + (isUser ? " avatar-user" : "");
    avatar.setAttribute("aria-hidden", "true");

    const card = document.createElement("div");
    card.className = "msg-card";
    if (isUser) {
      card.innerHTML = '<div class="msg-body">' + md(turn.content) + "</div>";
    } else {
      const tag = turn.subject_id && state.subjects[turn.subject_id]
        ? '<span class="subject-tag">' + htmlEsc(state.subjects[turn.subject_id].label) + "</span>"
        : "";
      card.innerHTML =
        '<div class="msg-head"><span class="who">StudyBuddy</span>' +
        tag +
        (turn.ts ? '<span class="when">' + timeOf(turn.ts) + "</span>" : "") +
        '</div><div class="msg-body">' +
        md(turn.content) +
        "</div>";
      if (turn.status === "no_match") {
        card.insertAdjacentHTML("beforeend", '<div class="note-box info">No syllabus match — nothing passed the relevance bar, so StudyBuddy isn\u2019t guessing.</div>');
      } else if (turn.status === "error") {
        card.insertAdjacentHTML("beforeend", '<div class="note-box error">The answer service hit an error. Check the backend logs and try again.</div>');
      }
      if (turn.sources && turn.sources.length) card.appendChild(buildSources(turn.sources));
      if (turn.followups && turn.followups.length) card.appendChild(buildFollowups(turn.followups));
    }
    msg.appendChild(avatar);
    msg.appendChild(card);
    wrap.appendChild(msg);
    scrollThread();
    return msg;
  }

  /* ---- sources accordion ---------------------------------------------------- */
  function buildSources(sources) {
    const box = document.createElement("div");
    box.className = "sources";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "sources-toggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML =
      '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M3 5.5 Q3 4.5 4 4.5 H10.5 A1 1 0 0 1 11.5 5.5 V19.5 Q11.5 18.5 10.5 18.5 H4 Q3 18.5 3 17.5 Z M21 5.5 Q21 4.5 20 4.5 H13.5 A1 1 0 0 0 12.5 5.5 V19.5 Q12.5 18.5 13.5 18.5 H20 Q21 18.5 21 17.5 Z"/></svg>' +
      (sources.length + (sources.length === 1 ? " source" : " sources")) +
      '<svg class="ic chev" viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" d="M6 9 L12 15 L18 9"/></svg>';

    const panel = document.createElement("div");
    panel.className = "sources-panel";
    sources.forEach((s) => panel.appendChild(sourceCard(s)));
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", open ? "false" : "true");
      panel.classList.toggle("open", !open);
    });
    box.appendChild(toggle);
    box.appendChild(panel);
    return box;
  }

  function sourceCard(s) {
    const el = document.createElement("div");
    el.className = "source-card";
    const page = s.page ? '<span class="md">p. ' + htmlEsc(s.page) + "</span>" : "";
    const pct = Math.max(2, Math.min(100, Math.round((s.score || 0) * 100)));
    el.innerHTML =
      '<div class="src-head"><span class="src-unit" title="' + htmlEsc(s.unit) + '">' + htmlEsc(s.unit) + "</span></div>" +
      '<div class="src-meta"><span class="md">' + htmlEsc(s.file || "") + "</span>" + page + "</div>" +
      '<div class="src-excerpt">' + htmlEsc(s.excerpt || "") + "</div>" +
      '<div class="relevance"><span class="rel-label">Relevance</span><span class="rel-bar"><i style="width:' + pct + '%"></i></span><span class="rel-pct">' + pct + "%</span></div>";
    const ex = el.querySelector(".src-excerpt");
    const shrink = () => {
      const over = ex.scrollHeight > 84;
      if (over) {
        ex.setAttribute("data-clamped", "");
        const more = document.createElement("button");
        more.type = "button";
        more.className = "src-more";
        more.textContent = "Show more";
        more.addEventListener("click", () => {
          if (ex.dataset.show) {
            delete ex.dataset.show;
            ex.setAttribute("data-clamped", "");
            more.textContent = "Show more";
          } else {
            ex.dataset.show = "1";
            ex.removeAttribute("data-clamped");
            more.textContent = "Show less";
          }
        });
        el.insertBefore(more, el.querySelector(".relevance"));
      }
    };
    requestAnimationFrame(shrink);
    return el;
  }

  /* ---- follow-up chips ---------------------------------------------------- */
  function buildFollowups(fups) {
    const box = document.createElement("div");
    box.className = "followups";
    const label = document.createElement("span");
    label.className = "fu-label";
    label.textContent = "Try asking:";
    box.appendChild(label);
    fups.forEach((f) => {
      const c = document.createElement("button");
      c.type = "button";
      c.className = "chip";
      c.textContent = f;
      c.addEventListener("click", () => send(f));
      box.appendChild(c);
    });
    return box;
  }

  /* ---- empty state ----------------------------------------------------------- */
  function renderEmpty() {
    state.turns = [];
    $("thread").innerHTML = "";
    const sub = state.subjects[state.subjectId];
    if (!sub) return;
    const hero = document.createElement("div");
    hero.className = "empty-state";
    hero.innerHTML =
      '<div class="hero-logo" aria-hidden="true"></div>' +
      "<h1 class=\"hero-title\">Hi, I'm StudyBuddy</h1>" +
      '<p class="hero-sub">I answer questions strictly from the <strong>' + htmlEsc(sub.label) + "</strong> syllabus. Every answer cites the unit and document it comes from, and I'll say plainly when a question is <em>not</em> on the syllabus.</p>" +
      '<div class="hero-chips"></div>';
    const chips = [
      ["Walk me through the first unit", "Walk me through the first unit, step by step"],
      ["Give me a practice problem", "Give me a practice problem, then check my work"],
      ["What's NOT on the syllabus?", "What topics are NOT covered by this syllabus?"],
    ];
    if (sub.chunks > 0) {
      const chipsEl = hero.querySelector(".hero-chips");
      chips.forEach(([label, q]) => {
        const c = document.createElement("button");
        c.type = "button";
        c.className = "chip chip-ask";
        c.textContent = label;
        c.addEventListener("click", () => {
          $("composerInput").value = q;
          send();
        });
        chipsEl.appendChild(c);
      });
    }
    ensureWrap().appendChild(hero);
  }

  /* =========================================================================
     CHAT FLOW
     ========================================================================= */
  function setBusy(b) {
    state.busy = b;
    $("sendBtn").classList.toggle("busy", b);
    $("statusline").hidden = !b;
    $("composerInput").disabled = b;
    updateSendState();
  }

  function updateSendState() {
    $("sendBtn").disabled = state.busy || !$("composerInput").value.trim();
  }

  async function send(override) {
    const input = $("composerInput");
    const text = (override || input.value).trim();
    if (!text || state.busy) return;
    const subjectId = state.subjectId;
    const history = state.turns.map((t) => ({ role: t.role, content: t.content }));
    const tsNow = Math.floor(Date.now() / 1000);

    state.turns.push({ role: "user", content: text, subject_id: subjectId, ts: tsNow });
    appendMessage(state.turns[state.turns.length - 1], true);
    input.value = "";
    autoResize();
    setBusy(true);
    $("statusText").textContent = "Searching the " + (state.subjects[subjectId] ? state.subjects[subjectId].label : subjectId) + " syllabus…";

    try {
      const res = await fetchJSON("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject_id: subjectId, message: text, session_id: state.sessionId, history }),
      });
      state.sessionId = res.session_id;
      state.turns.push({
        role: "assistant",
        content: res.reply,
        sources: res.sources || [],
        status: res.status,
        followups: res.followups || [],
        subject_id: subjectId,
        ts: tsNow,
      });
      appendMessage(state.turns[state.turns.length - 1], true);
      renderSessions();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  /* =========================================================================
   SESSIONS
   ========================================================================= */
  function newChat() {
    if (state.busy) return;
    state.sessionId = null;
    renderEmpty();
    renderSessions();
    $("composerInput").focus();
  }

  async function loadSession(id) {
    try {
      const res = await fetchJSON("/api/sessions/" + id);
      state.sessionId = id;
      state.turns = res.turns || [];
      $("thread").innerHTML = "";
      state.turns.forEach((t) => appendMessage(t, false));
      const last = state.turns[state.turns.length - 1];
      setSubject(last && last.subject_id && state.subjects[last.subject_id] ? last.subject_id : state.subjectId);
      renderSessions();
      closeSidebar();
    } catch (e) {
      toast("Could not open conversation: " + e.message, "error");
    }
  }

  /* =========================================================================
     EVENTS + INIT
     ========================================================================= */
  function autoResize() {
    const i = $("composerInput");
    i.style.height = "auto";
    i.style.height = Math.min(i.scrollHeight, 168) + "px";
  }

  function wireEvents() {
    const appEl = document.querySelector(".app");
    $("newChatBtn").addEventListener("click", newChat);
    $("menuBtn").addEventListener("click", () => {
      appEl.classList.contains("sidebar-open") ? closeSidebar() : openSidebar();
    });
    $("scrim").addEventListener("click", closeSidebar);
    $("themeLight").addEventListener("click", () => {
      localStorage.setItem("sb-theme", "light");
      applyTheme("light");
    });
    $("themeDark").addEventListener("click", () => {
      localStorage.setItem("sb-theme", "dark");
      applyTheme("dark");
    });

    const input = $("composerInput");
    input.addEventListener("input", () => {
      autoResize();
      updateSendState();
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        send();
      }
    });
    $("sendBtn").addEventListener("click", () => send());

    document.addEventListener("keydown", (ev) => {
      if (ev.key === "/" && document.activeElement !== input && !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) {
        ev.preventDefault();
        input.focus();
      }
      if (ev.key === "Escape") {
        closeSidebar();
        document.querySelectorAll(".sources-panel.open").forEach((p) => {
          p.classList.remove("open");
          if (p.previousElementSibling) p.previousElementSibling.setAttribute("aria-expanded", "false");
        });
      }
    });
  }

  async function loadSubjects() {
    const data = await fetchJSON("/api/subjects");
    state.subjects = Object.fromEntries(data.subjects.map((s) => [s.id, s]));
    $("modeBadges").innerHTML = [
      ["LLM", data.mode.llm],
      ["Embed", data.mode.embedder],
      ["Store", data.mode.vector_store],
    ]
      .map(([k, v]) => '<span class="mode-badge">' + k + ": " + htmlEsc(v) + "</span>")
      .join("");
    const saved = localStorage.getItem("sb-subject");
    state.subjectId = saved && state.subjects[saved] ? saved : data.subjects[0] ? data.subjects[0].id : null;
    renderSubjects();
    if (state.subjectId) {
      setSubject(state.subjectId, { keep: true });
      renderEmpty();
    }
    renderSessions();
  }

  function init() {
    initTheme();
    wireEvents();
    loadSubjects().catch((e) => toast("Failed to load subjects: " + e.message, "error"));
  }

  document.addEventListener("DOMContentLoaded", init);
})();