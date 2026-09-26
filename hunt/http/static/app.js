/* Hunt UI — thin client of hunt.core. No UI-only writes. */
(() => {
  const STATUSES = [
    "researching",
    "prepared",
    "sent",
    "waiting",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "parked",
  ];
  const OPEN_STATUSES = [
    "researching",
    "prepared",
    "sent",
    "waiting",
    "interview",
    "offer",
  ];
  const JOB_TYPES = ["source-poll", "screen-inbox", "triage-inbox", "tailor-cv"];
  const JOB_STATES = ["queued", "running", "done", "failed"];
  const TOKEN_KEY = "hunt_token";
  const THEME_KEY = "hunt.theme";
  const THEME_PREFS = ["light", "dark", "system"];
  const THEME_ICONS = {
    light: `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="2.75" fill="currentColor"/>
      <g stroke="currentColor" stroke-width="1.5" stroke-linecap="round" fill="none">
        <path d="M8 1.75v1.25M8 13v1.25M1.75 8H3M13 8h1.25M3.5 3.5l.9.9M11.6 11.6l.9.9M3.5 12.5l.9-.9M11.6 4.4l.9-.9"/>
      </g>
    </svg>`,
    dark: `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path fill="currentColor" d="M11.1 10.6A5.4 5.4 0 0 1 6.4 3.1 6.8 6.8 0 1 0 13.1 9.7a5.2 5.2 0 0 1-2 0.9z"/>
    </svg>`,
    system: `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
      <path fill="currentColor" d="M8 2.5a5.5 5.5 0 0 0 0 11V2.5z"/>
    </svg>`,
  };
  const BOARD_COLS = ["Company", "Title", "Status", "Modality", "Location", "Pay", "Updated", "Id"];
  const INBOX_COLS = ["Company", "Role", "Source", "Location", "Engagement", "EUR /mo", "Pros", "Cons", "Actions"];
  const INBOX_SORT_KEYS = {
    Company: "company",
    Role: "role",
    Source: "source_id",
    "EUR /mo": "gross_month",
  };
  const SOURCE_COLS = ["Name", "Adapter", "Enabled", "Last run", "Last error", "Listings", "Inbox", "", "Id"];
  const JOB_COLS = ["Type", "Position", "Pay", "State", "Created", "Error"];
  const JOB_LABELS = {
    "source-poll": "Source poll",
    "screen-inbox": "Screen inbox",
    "triage-inbox": "Triage inbox",
    "tailor-cv": "Tailor CV",
  };
  const PROFILE_TABS = [
    ["profile", "Profile"],
    ["positions", "Positions"],
    ["achievements", "Achievements"],
    ["skills", "Skills"],
    ["projects", "Projects"],
    ["integrity", "Integrity"],
  ];
  const SKILL_LEVELS = ["production", "working", "limited", "homelab"];
  const HARNESS_CHIPS = [
    ["auto", "Auto"],
    ["claude", "Claude"],
    ["cursor", "Cursor"],
    ["codex", "Codex"],
    ["opencode", "OpenCode"],
    ["openclaw", "OpenClaw"],
    ["paperclip", "Paperclip"],
  ];
  const HARNESS_HELP = {
    auto: "Hunt uses the first installed harness: OpenCode, then Claude Code or Codex. None found — Hunt will not start a chat here. Install OpenCode, or pick a harness you already use.",
    claude: "Writes project .mcp.json + .claude/skills/… (stdio hunt mcp)",
    cursor: "Writes .cursor/mcp.json",
    codex: "Writes ~/.codex/config.toml [mcp_servers.hunt]",
    opencode: "Writes opencode.json mcp.hunt (local stdio)",
    openclaw: "Writes OpenClaw MCP + Hunt skills",
    paperclip: "Imports Hunt packs as company skills. Hunt does not require Paperclip.",
  };
  const MODEL_PATHS = [
    ["spacexai", "SpaceXAI"],
    ["local", "Local"],
    ["custom", "Custom"],
  ];
  const SPACEXAI = {
    base_url: "https://api.x.ai/v1",
    api_key_env: "XAI_API_KEY",
    model: "grok-4.5",
  };
  const LOCAL_MODEL = {
    base_url: "http://127.0.0.1:8080/v1",
    api_key_env: "",
    model: "local-model",
  };

  let pendingKey = "";
  let liveTimer = 0;

  const state = {
    meta: null,
    route: { name: "board", id: null, tab: null },
    token: sessionStorage.getItem(TOKEN_KEY) || "",
    toast: null,
    dialog: null,
    gPending: false,
    boardFilters: new Set(OPEN_STATUSES),
    boardQuery: "",
    inboxStatus: "pending",
    inboxKnockout: "",
    inboxTriage: "",
    inboxSource: "",
    inboxQuery: "",
    inboxSort: "created_at",
    inboxOrder: "desc",
    jobStateFilters: new Set(JOB_STATES),
    jobTypeFilters: new Set(JOB_TYPES),
    quotedDirty: false,
    profileDirty: false,
    knowledge: null,
    profileConflict: null,
    agent: null,
    agentDoctor: null,
    agentDirty: false,
    agentBusy: false,
    agentReplaceKey: false,
    agentForm: null,
    agentError: null,
    detailApp: null,
  };

  function $(sel, root = document) {
    return root.querySelector(sel);
  }

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function readThemePref() {
    try {
      const value = localStorage.getItem(THEME_KEY);
      if (value === "light" || value === "dark" || value === "system") return value;
    } catch {
      /* private mode — keep in-memory preference */
    }
    return "system";
  }

  function writeThemePref(pref) {
    try {
      localStorage.setItem(THEME_KEY, pref);
    } catch {
      /* private mode — session-only */
    }
  }

  function osPrefersDark() {
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function resolveTheme(pref) {
    if (pref === "light" || pref === "dark") return pref;
    return osPrefersDark() ? "dark" : "light";
  }

  function applyTheme(pref) {
    const resolved = resolveTheme(pref);
    const root = document.documentElement;
    root.setAttribute("data-theme", resolved);
    root.style.colorScheme = resolved;
    return resolved;
  }

  function themeLabel(pref, resolved) {
    if (pref === "system") {
      return `Theme: System (${resolved === "dark" ? "Dark" : "Light"})`;
    }
    return pref === "dark" ? "Theme: Dark" : "Theme: Light";
  }

  let themePref = readThemePref();
  let themeMenuOpen = false;
  let rowMenuOpen = false;

  function setThemePref(pref) {
    if (pref !== "light" && pref !== "dark" && pref !== "system") pref = "system";
    themePref = pref;
    writeThemePref(pref);
    applyTheme(pref);
    syncThemeControls();
  }

  function syncThemeControls() {
    const pref = themePref;
    const resolved = resolveTheme(pref);
    const label = themeLabel(pref, resolved);
    document.querySelectorAll("[data-primitive=ThemeToggle]").forEach((btn) => {
      btn.setAttribute("data-theme-preference", pref);
      btn.setAttribute("data-theme-resolved", resolved);
      btn.setAttribute("aria-label", label);
      btn.setAttribute("title", label);
      btn.innerHTML = THEME_ICONS[pref];
    });
    document.querySelectorAll("[data-primitive=ThemeMenu] [data-theme-option]").forEach((item) => {
      const on = item.getAttribute("data-theme-option") === pref;
      item.setAttribute("aria-checked", on ? "true" : "false");
      item.tabIndex = on ? 0 : -1;
      const check = item.querySelector(".theme-menu-check");
      if (check) check.textContent = on ? "✓" : "";
    });
    document.querySelectorAll("[data-primitive=ThemePicker] [data-theme-pref]").forEach((chip) => {
      chip.setAttribute("aria-pressed", chip.getAttribute("data-theme-pref") === pref ? "true" : "false");
    });
  }

  function closeThemeMenu(restoreFocus) {
    const host = document.querySelector(".theme-toggle-host");
    document.querySelectorAll("[data-primitive=ThemeMenu]").forEach((menu) => {
      menu.hidden = true;
      menu.style.position = "";
      menu.style.top = "";
      menu.style.right = "";
      menu.style.left = "";
      menu.style.visibility = "";
      if (host && menu.parentElement !== host) host.appendChild(menu);
    });
    document.querySelectorAll("[data-primitive=ThemeToggle]").forEach((btn) => {
      const wasOpen = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", "false");
      if (restoreFocus && wasOpen) btn.focus();
    });
    themeMenuOpen = false;
  }

  function openThemeMenu(btn) {
    const host = btn.closest(".theme-toggle-host");
    const menu = (host && host.querySelector("[data-primitive=ThemeMenu]"))
      || document.querySelector("[data-primitive=ThemeMenu]");
    if (!menu) return;
    const opening = btn.getAttribute("aria-expanded") !== "true";
    closeThemeMenu();
    closeRowMenu();
    if (!opening) return;
    themeMenuOpen = true;
    btn.setAttribute("aria-expanded", "true");
    const rect = btn.getBoundingClientRect();
    document.body.appendChild(menu);
    menu.hidden = false;
    menu.style.position = "fixed";
    menu.style.visibility = "hidden";
    menu.style.top = "0";
    menu.style.left = "0";
    menu.style.right = "auto";
    const mw = menu.offsetWidth;
    const margin = 8;
    let left = rect.right - mw;
    if (left < margin) left = margin;
    if (left + mw > window.innerWidth - margin) {
      left = Math.max(margin, window.innerWidth - mw - margin);
    }
    menu.style.top = `${Math.round(rect.bottom + 4)}px`;
    menu.style.left = `${Math.round(left)}px`;
    menu.style.visibility = "";
    const checked = menu.querySelector('[aria-checked="true"]');
    if (checked) checked.focus();
  }

  function closeRowMenu(restoreFocus) {
    document.querySelectorAll("[data-primitive=RowMenu]").forEach((menu) => {
      const id = menu.getAttribute("data-row-menu-for");
      const trigger = id ? document.querySelector(`[data-primitive=IconBtn][data-row-menu="${id}"]`) : null;
      const host = trigger && trigger.closest(".row-menu-host");
      menu.hidden = true;
      menu.style.position = "";
      menu.style.top = "";
      menu.style.right = "";
      menu.style.left = "";
      menu.style.visibility = "";
      if (host && menu.parentElement !== host) host.appendChild(menu);
    });
    document.querySelectorAll("[data-primitive=IconBtn][data-row-menu]").forEach((btn) => {
      const wasOpen = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", "false");
      if (restoreFocus && wasOpen) btn.focus();
    });
    rowMenuOpen = false;
  }

  function openRowMenu(btn) {
    const host = btn.closest(".row-menu-host");
    const id = btn.getAttribute("data-row-menu");
    const menu = (host && host.querySelector("[data-primitive=RowMenu]"))
      || document.querySelector(`[data-primitive=RowMenu][data-row-menu-for="${id}"]`);
    if (!menu) return;
    const opening = btn.getAttribute("aria-expanded") !== "true";
    closeThemeMenu();
    closeRowMenu();
    if (!opening) return;
    rowMenuOpen = true;
    btn.setAttribute("aria-expanded", "true");
    const rect = btn.getBoundingClientRect();
    document.body.appendChild(menu);
    menu.hidden = false;
    menu.style.position = "fixed";
    menu.style.visibility = "hidden";
    menu.style.top = "0";
    menu.style.left = "0";
    menu.style.right = "auto";
    const mw = menu.offsetWidth;
    const margin = 8;
    let left = rect.right - mw;
    if (left < margin) left = margin;
    if (left + mw > window.innerWidth - margin) {
      left = Math.max(margin, window.innerWidth - mw - margin);
    }
    menu.style.top = `${Math.round(rect.bottom + 4)}px`;
    menu.style.left = `${Math.round(left)}px`;
    menu.style.visibility = "";
    const first = menu.querySelector("[role=menuitem]");
    if (first) first.focus();
  }

  function ThemeToggle() {
    const pref = themePref;
    const resolved = resolveTheme(pref);
    const label = themeLabel(pref, resolved);
    const items = THEME_PREFS.map((id) => {
      const name = id === "light" ? "Light" : id === "dark" ? "Dark" : "System";
      const checked = pref === id;
      const help = id === "system"
        ? `<span class="theme-menu-help" aria-hidden="true">Match the device</span>`
        : "";
      return `<div role="menuitemradio" data-theme-option="${id}" aria-checked="${checked}" tabindex="${checked ? "0" : "-1"}">
        <span class="theme-menu-check" aria-hidden="true">${checked ? "✓" : ""}</span>
        <span class="theme-menu-copy"><span>${name}</span>${help}</span>
      </div>`;
    }).join("");
    return `<div class="theme-toggle-host">
      <button type="button" data-primitive="ThemeToggle" data-theme-preference="${pref}" data-theme-resolved="${resolved}" aria-haspopup="menu" aria-expanded="false" aria-controls="hunt-theme-menu" aria-label="${esc(label)}" title="${esc(label)}">${THEME_ICONS[pref]}</button>
      <div data-primitive="ThemeMenu" id="hunt-theme-menu" role="menu" aria-label="Theme" hidden>${items}</div>
    </div>`;
  }

  function ThemePicker() {
    const pref = themePref;
    const chips = THEME_PREFS.map((id) => {
      const name = id === "light" ? "Light" : id === "dark" ? "Dark" : "System";
      return `<button type="button" data-primitive="FilterChip" data-theme-pref="${id}" aria-pressed="${pref === id}">${name}</button>`;
    }).join("");
    return `<div data-primitive="ThemePicker">${chips}</div>`;
  }

  function token() {
    return state.token || sessionStorage.getItem(TOKEN_KEY) || "";
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
    const t = token();
    if (t) headers.Authorization = `Bearer ${t}`;
    if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    if (opts.ifMatch) headers["If-Match"] = `"${opts.ifMatch}"`;
    let res;
    try {
      res = await fetch(path, Object.assign({}, opts, { headers }));
    } catch (err) {
      const error = new Error("Cannot reach Hunt HTTP.");
      error.offline = true;
      throw error;
    }
    if (res.status === 401) {
      const error = new Error("authorization required");
      error.status = 401;
      throw error;
    }
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { error: text };
      }
    }
    if (!res.ok) {
      const error = new Error((data && data.error) || res.statusText);
      error.status = res.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function go(href, replace) {
    const url = new URL(href, location.href);
    if (url.origin !== location.origin) {
      window.location.href = url.href;
      return;
    }
    const next = `${url.pathname}${url.search}${url.hash}`;
    if (replace) history.replaceState({}, "", next);
    else history.pushState({}, "", next);
    state.dialog = null;
    state.quotedDirty = false;
    render();
  }

  function parseRoute(loc) {
    const src = loc || location;
    const path = String(src.pathname || "/").replace(/\/+$/, "") || "/";
    const params = new URLSearchParams(src.search || "");
    if (path === "/") return { name: "board", id: null, tab: null };
    if (path === "/inbox") {
      const status = params.get("status") === "dismissed" ? "dismissed" : "pending";
      return { name: "inbox", id: null, tab: null, status };
    }
    if (path === "/sources") return { name: "sources", id: null, tab: null };
    if (path === "/jobs") return { name: "jobs", id: null, tab: null };
    if (path === "/applications/new") return { name: "new", id: null, tab: null };
    if (path === "/profile") {
      const allowed = PROFILE_TABS.map((t) => t[0]);
      const tab = params.get("tab") || "positions";
      return { name: "profile", id: null, tab: allowed.includes(tab) ? tab : "positions" };
    }
    if (path === "/settings") return { name: "settings", id: null, tab: null };
    const jobPage = path.match(/^\/jobs\/([^/]+)$/);
    if (jobPage) return { name: "job", id: decodeURIComponent(jobPage[1]), tab: null };
    const m = path.match(/^\/applications\/([^/]+)$/);
    if (m) return { name: "detail", id: decodeURIComponent(m[1]), tab: null };
    return { name: "notfound", id: null, tab: null };
  }

  function relative(iso) {
    if (!iso) return "";
    const then = Date.parse(iso);
    if (Number.isNaN(then)) return iso;
    const sec = Math.round((Date.now() - then) / 1000);
    if (sec < 60) return "just now";
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    if (sec < 86400 * 30) return `${Math.floor(sec / 86400)}d ago`;
    return iso.slice(0, 10);
  }

  function money(n) {
    if (n == null || n === "") return "";
    const num = Number(n);
    if (Number.isNaN(num)) return String(n);
    return num.toLocaleString("en-IE", { maximumFractionDigits: 2 });
  }

  function titleOf(app) {
    return (app.position && app.position.role) || app.title_ours || app.title_posted || "";
  }

  function modalityLabel(value) {
    const v = String(value || "").trim().toLowerCase();
    if (v === "remote") return "Remote";
    if (v === "hybrid") return "Hybrid";
    if (v === "onsite") return "Onsite";
    return v ? String(value) : "";
  }

  function positionMeta(it) {
    const view = (it && it.position) || it || {};
    const loc = view.location || locationOf(it);
    const eng = engagementOf(view.engagement_label || view.engagement ? view : it);
    const source = view.source || view.source_id || "";
    return [loc, eng, source].filter(Boolean).join(" · ");
  }

  function jobTypeLabel(value) {
    return JOB_LABELS[value] || value || "";
  }

  function modalityOptions(current) {
    const options = [
      { value: "", label: "—" },
      { value: "remote", label: "Remote" },
      { value: "hybrid", label: "Hybrid" },
      { value: "onsite", label: "Onsite" },
    ];
    if (current && !options.some((o) => o.value === current)) options.push({ value: current, label: modalityLabel(current) || current });
    return options;
  }

  function engagementOptions(current) {
    const options = [
      { value: "", label: "—" },
      { value: "b2b", label: "Freelance" },
      { value: "fte", label: "FTE" },
      { value: "uop", label: "Employment contract" },
      { value: "unknown", label: "Unknown" },
    ];
    if (current && !options.some((o) => o.value === current)) options.push({ value: current, label: engagementLabel(current) });
    return options;
  }

  function locationOf(app) {
    if (app && app.location) return app.location;
    const city = app && (app.location_city || (app.payload && app.payload.location_city));
    const country = app && (app.location_country || (app.payload && app.payload.location_country));
    return [city, country].filter(Boolean).join(", ");
  }

  function quotedOf(obj) {
    if (!obj) return null;
    if (obj.comp_quoted) return obj.comp_quoted;
    const p = obj.payload || {};
    if (p.comp_quoted) return p.comp_quoted;
    return null;
  }

  /* ---- primitives ---- */

  function Btn(label, { variant = "neutral", href, attrs = "", type = "button" } = {}) {
    const cls = variant === "primary" ? " primary" : variant === "danger" ? " danger" : variant === "ghost" ? " ghost" : "";
    if (href) {
      return `<a data-primitive="Btn" class="${cls.trim()}" href="${esc(href)}" ${attrs}>${esc(label)}</a>`;
    }
    return `<button type="${type}" data-primitive="Btn" class="${cls.trim()}" ${attrs}>${esc(label)}</button>`;
  }

  function CountBadge(n) {
    if (!n) return `<span data-primitive="CountBadge" hidden>0</span>`;
    return `<span data-primitive="CountBadge">${esc(n)}</span>`;
  }

  function NavItem(href, label, current, badge) {
    const cur = current ? `aria-current="page"` : "";
    return `<a data-primitive="NavItem" href="${esc(href)}" data-nav="${esc(href)}" ${cur}>${esc(label)}${CountBadge(badge)}</a>`;
  }

  function CopyId(id) {
    if (!id) return "";
    const short = id.length > 10 ? id.slice(0, 8) + "…" : id;
    return `<button type="button" data-primitive="CopyId" data-copy="${esc(id)}" title="${esc(id)}">${esc(short)}</button>`;
  }

  function engagementLabel(value) {
    const v = String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[\s_]+/g, "-");
    if (["fte", "uop", "permanent", "full-time", "fulltime", "employee", "employment"].includes(v)) return "FTE";
    if (
      ["freelance", "b2b", "jdg", "contract", "contractor", "contracting", "autonomo", "autónomo", "self-employed"].includes(
        v
      )
    ) {
      return "Freelance";
    }
    return "Unknown";
  }

  function engagementOf(it) {
    if (it && it.engagement_label) return it.engagement_label;
    return engagementLabel(it && it.engagement);
  }

  function engagementCell(it) {
    const label = engagementOf(it);
    const quiet = label === "Unknown" ? " muted" : "";
    return `<span data-primitive="EngagementLabel" class="${quiet}">${esc(label)}</span>`;
  }

  function PostingLink(url, label) {
    const text = label || "Posting";
    if (!url) return esc(text);
    return `<a data-primitive="PostingLink" href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>`;
  }

  function InboxTabs(current, pendingCount) {
    return `<nav data-primitive="InboxTabs">${NavItem("/inbox", "Pending", current !== "dismissed", pendingCount)}${NavItem(
      "/inbox?status=dismissed",
      "Dismissed",
      current === "dismissed"
    )}</nav>`;
  }

  function CommandHint(cmd) {
    return `<button type="button" data-primitive="CommandHint" data-copy="${esc(cmd)}" title="${esc(cmd)}">CLI</button>`;
  }

  function IconBtn(label, { attrs = "" } = {}) {
    return `<button type="button" data-primitive="IconBtn" aria-label="${esc(label)}" title="${esc(label)}" ${attrs}>⋯</button>`;
  }

  function RowMenu(it, pending) {
    const id = it && it.id;
    if (!id) return "";
    const cli = pending
      ? `hunt inbox promote ${id} --json`
      : `hunt inbox restore ${id} --json`;
    return `<div class="row-menu-host">
      ${IconBtn("More", { attrs: `data-row-menu="${esc(id)}" aria-haspopup="menu" aria-expanded="false"` })}
      <div data-primitive="RowMenu" role="menu" aria-label="More" hidden data-row-menu-for="${esc(id)}">
        <button type="button" role="menuitem" data-copy="${esc(id)}">Copy ID</button>
        <button type="button" role="menuitem" data-copy="${esc(cli)}">Copy CLI</button>
      </div>
    </div>`;
  }

  function PayMonth(it) {
    const view = (it && it.pay_month) || {};
    const line1 = view.line1 || "—";
    const caption = view.caption || "unknown";
    const title = view.title || "";
    const empty = line1 === "—";
    const cls = empty ? ` class="is-empty"` : "";
    const titleAttr = title ? ` title="${esc(title)}"` : "";
    const amount = empty
      ? `<span class="placeholder">—</span>`
      : `<strong>${esc(line1)}</strong>`;
    return `<span data-primitive="PayMonth"${cls}${titleAttr}>${amount}<span class="caption">${esc(caption)}</span></span>`;
  }

  function StatusPill(status) {
    const s = status || "";
    return `<span data-primitive="StatusPill" class="status-${esc(s)}">${esc(s)}</span>`;
  }

  function DraftBadge() {
    return `<span data-primitive="DraftBadge">Draft — not on CVs</span>`;
  }

  function VerifiedBadge() {
    return `<span data-primitive="VerifiedBadge">Verified</span>`;
  }

  function ProfileNav(current) {
    const items = PROFILE_TABS.map(([key, label]) =>
      NavItem(`/profile?tab=${key}`, label, current === key)
    ).join("");
    return `<nav data-primitive="ProfileNav">${items}</nav>`;
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function setPath(obj, path, value) {
    const parts = path.split(".");
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      const key = parts[i];
      const next = parts[i + 1];
      const asIndex = /^\d+$/.test(next);
      if (cur[key] == null) cur[key] = asIndex ? [] : {};
      cur = cur[key];
    }
    cur[parts[parts.length - 1]] = value;
  }

  function getPath(obj, path) {
    return path.split(".").reduce((acc, key) => (acc == null ? acc : acc[key]), obj);
  }

  function weakEvidence(ev) {
    return !String(ev || "").trim() || String(ev).trim().toLowerCase() === "own work.";
  }

  function isQuantified(text, terms) {
    const body = String(text || "");
    if ((terms || []).some((q) => q && body.includes(q))) return true;
    return /\d/.test(body);
  }

  function canConfirmAchievement(row, terms) {
    if (!String(row.evidence || "").trim()) return false;
    if (isQuantified(row.text, terms) && weakEvidence(row.evidence)) return false;
    return true;
  }

  function monthKey(value) {
    if (!value || value === "present") return value === "present" ? 999999 : null;
    const m = String(value).match(/^(\d{4})-(\d{2})/);
    if (!m) return null;
    return Number(m[1]) * 12 + Number(m[2]);
  }

  function overlappingPositions(positions) {
    const dated = (positions || [])
      .map((p, i) => ({ i, start: monthKey(p.start), end: monthKey(p.end || "present"), label: p.employer || p.title || p.id }))
      .filter((p) => p.start != null && p.end != null);
    const hits = [];
    for (let a = 0; a < dated.length; a++) {
      for (let b = a + 1; b < dated.length; b++) {
        const x = dated[a];
        const y = dated[b];
        if (x.start <= y.end && y.start <= x.end) hits.push(`${x.label} overlaps ${y.label}`);
      }
    }
    return hits;
  }

  function JobStatePill(st) {
    return `<span data-primitive="JobStatePill" class="job-${esc(st)}">${esc(st)}</span>`;
  }

  function PayQuoted(q) {
    if (!q || q.amount == null) {
      return `<span data-primitive="PayUnknown">Pay unknown</span>`;
    }
    return `<span data-primitive="PayQuoted"><span class="caption">Quoted</span><strong>${esc(money(q.amount))} ${esc(q.currency)} / ${esc(q.unit)}</strong></span>`;
  }

  function PayDerived(d) {
    if (!d || !d.fx_as_of) {
      return `<div data-primitive="PayDerived"><span class="caption">Derived unavailable</span>No FX stamp — conversions hidden.</div>`;
    }
    return `<div data-primitive="PayDerived"><span class="caption">Derived · FX ${esc(d.fx_as_of)} · ${esc(d.display_currency)}</span>
      ${esc(money(d.hour))} /h · ${esc(money(d.day))} /d · ${esc(money(d.month))} /mo · ${esc(money(d.year))} /yr</div>`;
  }

  function NetEstimate(derived, { empty = "net —", taxHome = "", detail = false } = {}) {
    if (!derived || !derived.fx_as_of || derived.net_month == null) {
      const body = detail
        ? `<span class="help">Add a tax home to estimate net.</span>`
        : `<span class="placeholder">${esc(empty)}</span>`;
      return `<span data-primitive="NetEstimate" class="is-empty"><span class="caption">Net /mo</span>${body}</span>`;
    }
    const caption = detail
      ? `Net /mo · ${derived.display_currency}`
      : "Net /mo";
    let html = `<span data-primitive="NetEstimate"><span class="caption">${esc(caption)}</span><strong>${esc(money(derived.net_month))} ${esc(derived.display_currency)} /mo</strong>`;
    if (detail) {
      const tax = taxHome ? ` · tax home ${esc(taxHome)}` : "";
      html += `<span class="caption net-meta">FX ${esc(derived.fx_as_of)}${tax}</span>`;
    }
    html += `</span>`;
    return html;
  }

  function EmptyState(title, body, actionsHtml = "") {
    return `<div data-primitive="EmptyState"><h2>${esc(title)}</h2><p>${esc(body)}</p><div class="empty-actions">${actionsHtml}</div></div>`;
  }

  function ErrorBanner(message, retryAttr = "data-retry") {
    return `<div data-primitive="ErrorBanner"><span class="msg">${esc(message)}</span>${Btn("Retry", { attrs: retryAttr })}</div>`;
  }

  function LoadingSkeleton(n = 8, columns) {
    const rows = Array.from({ length: n }, () => `<div data-primitive="LoadingSkeleton"></div>`).join("");
    const bars = `<div class="skel-table">${rows}</div>`;
    if (!columns || !columns.length) return bars;
    const th = columns.map((c) => `<th>${esc(c)}</th>`).join("");
    return `<table data-primitive="DataTable" class="skel-chrome"><thead><tr>${th}</tr></thead></table>${bars}`;
  }

  function FormField(label, control, { help, span2, name, err } = {}) {
    return `<div data-primitive="FormField" class="${span2 ? "span-2" : ""}${err ? " is-invalid" : ""}" data-name="${esc(name || "")}">
      <label class="label">${esc(label)}</label>
      ${control}
      ${err ? `<div class="err">${esc(err)}</div>` : help ? `<div class="help">${esc(help)}</div>` : ""}
    </div>`;
  }

  function input(name, value, attrs = "") {
    return `<input data-primitive="TextInput" name="${esc(name)}" value="${esc(value ?? "")}" ${attrs}>`;
  }
  function select(name, value, options, attrs = "") {
    const opts = options
      .map((o) => {
        const v = typeof o === "string" ? o : o.value;
        const l = typeof o === "string" ? o : o.label;
        return `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(l)}</option>`;
      })
      .join("");
    const nameAttr = name ? `name="${esc(name)}"` : "";
    return `<select data-primitive="Select" ${nameAttr} ${attrs}>${opts}</select>`;
  }
  function textarea(name, value, attrs = "") {
    const nameAttr = name ? `name="${esc(name)}"` : "";
    return `<textarea data-primitive="Textarea" ${nameAttr} ${attrs}>${esc(value ?? "")}</textarea>`;
  }

  function nav(current, badges) {
    const items = [
      ["/ ", "Board", "board"],
      ["/inbox", "Inbox", "inbox"],
      ["/sources", "Sources", "sources"],
      ["/jobs", "Jobs", "jobs"],
    ]
      .map(([href, label, key]) =>
        NavItem(href.trim(), label, current === key, badges[key] || 0)
      )
      .join("");
    const ws = state.meta || {};
    const chipCurrent = current === "profile" ? `aria-current="page"` : "";
    const chip = `<a data-primitive="WorkspaceChip" href="/profile" data-nav="/profile" ${chipCurrent}>${esc(ws.workspace || "workspace")}<span class="profile"> · ${esc(ws.profile_name || "")}</span></a>`;
    const agentNav = NavItem("/settings", "Agent", current === "settings");
    return {
      bar: `<header data-primitive="AppBar">
        <a class="wordmark" href="/">Hunt</a>
        <nav class="appbar-nav appbar-nav-desktop">${items}</nav>
        ${chip}
        ${ThemeToggle()}
        ${agentNav}
      </header>`,
      tabs: `<nav data-primitive="AppTabBar"><div class="appbar-nav">${items}</div></nav>`,
    };
  }

  function toastHtml() {
    if (!state.toast) return "";
    return `<div data-primitive="Toast" role="status">${state.toast}</div>`;
  }

  function showToast(html) {
    state.toast = html;
    render();
    setTimeout(() => {
      state.toast = null;
      const t = $("[data-primitive=Toast]");
      if (t) t.remove();
    }, 2000);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
  }

  /* ---- pages ---- */

  function AuthGate(message) {
    return `<div data-primitive="AuthGate">
      ${ThemeToggle()}
      <h1>Hunt</h1>
      <p class="muted">${esc(message || "This workspace requires a token.")}</p>
      <form id="auth-form">
        ${FormField("Token", `<input data-primitive="TextInput" name="token" type="password" autocomplete="off">`)}
        <div class="form-actions">${Btn("Continue", { variant: "primary", type: "submit" })}</div>
      </form>
    </div>`;
  }

  function shell(current, badges, body) {
    const n = nav(current, badges);
    const dirty = state.quotedDirty && current === "board" && state.route.name === "detail" ? " quoted-dirty" : "";
    const pdirty = state.profileDirty && current === "profile" ? " profile-dirty" : "";
    const adirty = state.agentDirty && current === "settings" ? " agent-dirty" : "";
    return `<div data-primitive="AppShell" class="${(dirty + pdirty + adirty).trim()}">${n.bar}<main class="page">${body}</main>${n.tabs}${toastHtml()}${dialogHtml()}</div>`;
  }

  function dialogHtml() {
    const d = state.dialog;
    if (!d) return "";
    if (d.kind === "promote") {
      const item = d.item;
      const q = quotedOf(item);
      return `<div class="scrim" data-close-dialog>
        <div data-primitive="PromoteDialog" role="dialog" aria-modal="true">
          <h2>Promote to application</h2>
          <div class="dialog-body">
            <p>This is the only way a listing becomes an application. Hunt will not apply for Jane Doe.</p>
            <dl>
              <dt>Company</dt><dd>${esc(item.company)}</dd>
              <dt>Title</dt><dd>${esc(item.title)}</dd>
              <dt>URL</dt><dd>${esc(item.url || "—")}</dd>
              <dt>Source</dt><dd>${esc((item.payload && item.payload.source) || "inbox")}</dd>
              <dt>Pay</dt><dd>${q ? `${esc(q.amount)} ${esc(q.currency)} / ${esc(q.unit)}` : "Pay unknown"}</dd>
            </dl>
            ${CommandHint(`hunt inbox promote ${item.id} --json`)}
          </div>
          <div class="dialog-actions">
            ${Btn("Cancel", { variant: "ghost", attrs: "data-close-dialog" })}
            ${Btn("Promote", { variant: "primary", attrs: `data-confirm-promote="${esc(item.id)}"` })}
          </div>
        </div>
      </div>`;
    }
    if (d.kind === "confirm") {
      const body = d.html ? d.html : esc(d.body || "");
      return `<div class="scrim" data-close-dialog>
        <div data-primitive="ConfirmDialog" role="dialog" aria-modal="true">
          <h2>${esc(d.title)}</h2>
          <div class="dialog-body">${body}</div>
          <div class="dialog-actions">
            ${Btn(d.cancel || "Cancel", { variant: "ghost", attrs: "data-close-dialog" })}
            ${Btn(d.ok, { variant: d.danger ? "danger" : "primary", attrs: `data-confirm-ok="${esc(d.action)}"` })}
          </div>
        </div>
      </div>`;
    }
    if (d.kind === "confirm-verify") {
      return `<div class="scrim" data-close-dialog>
        <div data-primitive="ConfirmVerifyDialog" role="dialog" aria-modal="true">
          <h2>Confirm this fact?</h2>
          <div class="dialog-body">Unverified claims never appear on a CV. Confirm only what Jane Doe can defend in an interview.</div>
          <div class="dialog-actions">
            ${Btn("Cancel", { variant: "ghost", attrs: "data-close-dialog" })}
            ${Btn("Confirm", { variant: "primary", attrs: `data-confirm-verify="${esc(d.kindTarget)}:${esc(d.id)}"` })}
          </div>
        </div>
      </div>`;
    }
    return "";
  }

  function pageHeader(title, extra, actions, cls) {
    return `<div data-primitive="PageHeader" class="${esc(cls || "")}"><h1>${title}</h1>${extra || ""}<div class="header-actions">${actions || ""}</div></div>`;
  }

  function boardFiltersHtml() {
    const chips = OPEN_STATUSES.map((s) => {
      const on = state.boardFilters.has(s);
      return `<button type="button" data-primitive="FilterChip" data-filter="${s}" aria-pressed="${on}">${s}</button>`;
    }).join("");
    const closedOn = state.boardFilters.has("rejected") && state.boardFilters.has("withdrawn");
    const parkedOn = state.boardFilters.has("parked");
    return `<div data-primitive="FilterBar">
      <input class="filter-search" id="filter-search" placeholder="Filter" value="${esc(state.boardQuery)}">
      ${chips}
      <button type="button" data-primitive="FilterChip" data-filter="closed" aria-pressed="${closedOn}">Closed</button>
      <button type="button" data-primitive="FilterChip" data-filter="parked" aria-pressed="${parkedOn}">Parked</button>
    </div>`;
  }

  function jobFiltersHtml() {
    const states = JOB_STATES.map((s) => {
      const on = state.jobStateFilters.has(s);
      return `<button type="button" data-primitive="FilterChip" data-job-state="${s}" aria-pressed="${on}">${s}</button>`;
    }).join("");
    const types = JOB_TYPES.map((t) => {
      const on = state.jobTypeFilters.has(t);
      return `<button type="button" data-primitive="FilterChip" data-job-type="${t}" aria-pressed="${on}">${esc(jobTypeLabel(t))}</button>`;
    }).join("");
    return `<div data-primitive="FilterBar">${states}${types}</div>`;
  }

  function appRowCells(app) {
    const q = quotedOf(app);
    const d = app.comp_derived;
    const pos = app.position;
    const pay = pos && pos.pay_month
      ? PayMonth(pos)
      : q
        ? `<div class="pay-cell">${PayQuoted(q)}${NetEstimate(d)}</div>`
        : PayQuoted(null);
    return {
      company: `<strong>${esc(app.company)}</strong>`,
      title: `<span class="muted">${esc(titleOf(app))}</span>`,
      status: StatusPill(app.status),
      modality: esc(modalityLabel(app.modality) || "—"),
      location: esc((pos && pos.location) || locationOf(app) || "—"),
      pay,
      updated: `<span title="${esc(app.updated_at)}">${esc(relative(app.updated_at))}</span>`,
      id: CopyId(app.id),
    };
  }

  async function renderBoard(root, badges) {
    root.innerHTML = shell(
      "board",
      badges,
      pageHeader("Board", `<span class="page-count"></span>`, Btn("New application", { variant: "primary", href: "/applications/new" }) + CommandHint("hunt applications create --company … --json")) +
        boardFiltersHtml() +
        LoadingSkeleton(8, BOARD_COLS)
    );
    let data;
    try {
      data = await api("/api/applications");
    } catch (err) {
      root.innerHTML = shell("board", badges, ErrorBanner(err.message) + boardFiltersHtml());
      return;
    }
    const apps = data.applications || [];
    const q = state.boardQuery.toLowerCase();
    const filtered = apps.filter((a) => {
      if (state.boardFilters.size && !state.boardFilters.has(a.status)) return false;
      if (!q) return true;
      const hay = `${a.company} ${titleOf(a)} ${a.id}`.toLowerCase();
      return hay.includes(q);
    });
    let body;
    if (!apps.length) {
      body =
        pageHeader("Board", `<span class="page-count">0</span>`, Btn("New application", { variant: "primary", href: "/applications/new" })) +
        EmptyState(
          "No applications yet",
          "Jane Doe’s workspace is empty. Promote a screened listing from Inbox, or create a researching row by hand.",
          Btn("Open inbox", { href: "/inbox" }) + Btn("New application", { variant: "primary", href: "/applications/new" })
        );
    } else if (!filtered.length) {
      body =
        pageHeader("Board", `<span class="page-count">0 / ${apps.length}</span>`, Btn("New application", { variant: "primary", href: "/applications/new" })) +
        boardFiltersHtml() +
        EmptyState(
          "No applications in this view",
          "Adjust filters, create a researching row, or promote a listing from Inbox.",
          Btn("Clear filters", { variant: "ghost", attrs: "data-clear-filters" }) +
            Btn("New application", { variant: "primary", href: "/applications/new" })
        );
    } else {
      const rows = filtered
        .map((a) => {
          const c = appRowCells(a);
          return `<tr data-primitive="Row" data-href="/applications/${esc(a.id)}" tabindex="0">
            <td>${c.company}</td><td>${c.title}</td><td>${c.status}</td><td>${c.modality}</td>
            <td>${c.location}</td><td>${c.pay}</td><td>${c.updated}</td><td>${c.id}</td>
          </tr>`;
        })
        .join("");
      const cards = filtered
        .map((a) => {
          const c = appRowCells(a);
          return `<article data-primitive="Row" class="board-card" data-href="/applications/${esc(a.id)}">
            <div class="row-line1">${c.company}${c.status}</div>
            <div>${c.title}</div>
            <div class="muted">${esc(positionMeta(a))}</div>
            <div class="row-line1">${c.pay}</div>
          </article>`;
        })
        .join("");
      body =
        pageHeader("Board", `<span class="page-count">${filtered.length}</span>`, Btn("New application", { variant: "primary", href: "/applications/new" }) + CommandHint("hunt applications create --company … --json")) +
        boardFiltersHtml() +
        `<table data-primitive="DataTable" class="board-table">
          <thead><tr><th>Company</th><th>Title</th><th>Status</th><th>Modality</th><th>Location</th><th>Pay</th><th>Updated</th><th>Id</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="board-cards">${cards}</div>`;
    }
    root.innerHTML = shell("board", badges, body);
  }

  async function renderNew(root, badges) {
    const body =
      pageHeader("New application", "", CommandHint("hunt applications create --company … --status researching --json")) +
      `<p class="helper-banner">Blank application for research. Screened listings must be promoted from Inbox.</p>
      <div class="section">
      <form id="create-form" class="form-grid">
        ${FormField("Company", input("company", "", "required"), { name: "company" })}
        ${FormField("Posted title", input("title_posted", "", "required"), { name: "title_posted" })}
        ${FormField("URL", input("url", ""), { span2: true })}
        ${FormField("Source", input("source", "manual"))}
        ${FormField("City", input("location_city", ""))}
        ${FormField("Country", input("location_country", ""))}
        ${FormField("Modality", select("modality", "", modalityOptions("")))}
        ${FormField("Engagement", select("engagement", "", engagementOptions("")))}
        ${FormField("Quoted amount", input("comp_amount", "", "type=number step=any"))}
        ${FormField("Currency", input("comp_currency", ""))}
        ${FormField("Unit", select("comp_unit", "", ["", "hour", "day", "month", "year"]))}
        ${FormField("Tax home", input("tax_home_for_net", ""))}
        ${FormField("Comp notes", textarea("comp_notes", ""), { span2: true })}
        <div class="span-2">${Btn("Create researching row", { variant: "primary", type: "submit" })}</div>
      </form>
      </div>`;
    root.innerHTML = shell("board", badges, body);
  }

  function stopLive() {
    if (liveTimer) {
      clearTimeout(liveTimer);
      liveTimer = 0;
    }
  }

  function scheduleLive(fn, ms) {
    stopLive();
    liveTimer = setTimeout(fn, ms || 1200);
  }

  function latestCv(artifacts) {
    const list = ((artifacts && artifacts.artifacts) || []).filter((a) => a.kind === "cv");
    list.sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")));
    return list.length ? list[list.length - 1] : null;
  }

  function tailorJobsOf(jobs) {
    return (jobs || [])
      .filter((j) => j.type === "tailor-cv")
      .slice()
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  }

  function activeTailorJob(jobs) {
    return tailorJobsOf(jobs).find((j) => j.state === "queued" || j.state === "running") || null;
  }

  function PositionFacts(p) {
    if (!p) return "";
    const rows = [];
    const add = (label, html) => {
      if (!html) return;
      rows.push(`<div><dt>${esc(label)}</dt><dd>${html}</dd></div>`);
    };
    add("Company", p.company ? esc(p.company) : "");
    const role = p.role || p.title || "";
    if (role) add("Role", p.url ? PostingLink(p.url, role) : esc(role));
    add("Location", p.location ? esc(p.location) : "");
    if (p.engagement || p.engagement_label) add("Engagement", engagementCell(p));
    if (p.pay_month) add("Pay", PayMonth(p));
    if (p.modality) add("Modality", esc(modalityLabel(p.modality)));
    const source = p.source || p.source_id;
    add("Source", source ? esc(source) : "");
    if (!rows.length) return "";
    return `<dl data-primitive="PositionFacts">${rows.join("")}</dl>`;
  }

  function CvFile(art) {
    if (!art) return "";
    const appId = art.application_id;
    const href = `/api/applications/${encodeURIComponent(appId)}/artifacts/${encodeURIComponent(art.id)}/file`;
    return `<div class="cv-file" data-primitive="CvFile">
      <div class="cv-name">${esc(art.filename)}</div>
      <div class="cv-path" title="${esc(art.path)}">${esc(art.path)}</div>
      <div class="cv-actions">
        ${Btn("Open", { href: `${href}?inline=1`, attrs: 'target="_blank" rel="noopener"' })}
        ${Btn("Download", { href, attrs: `download="${esc(art.filename)}"` })}
        ${Btn("Show in folder", { attrs: `data-reveal-artifact="${esc(art.id)}" data-application="${esc(appId)}"` })}
        ${Btn("Copy path", { variant: "ghost", attrs: `data-copy="${esc(art.path)}"` })}
      </div>
    </div>`;
  }

  function CvPanel(app, artifacts, jobs) {
    const cv = latestCv(artifacts);
    const active = activeTailorJob(jobs);
    const newest = tailorJobsOf(jobs)[0];
    let status = "";
    if (active) {
      const label = active.state === "running" ? "Tailoring CV" : "Queued";
      status = `<div data-primitive="TailorStatus" data-state="${esc(active.state)}">
        <span>${esc(label)}</span>
        ${JobStatePill(active.state)}
        <a href="/jobs/${esc(active.id)}">${esc(jobTypeLabel(active.type))}</a>
      </div>
      <div class="tailor-progress" role="progressbar" aria-label="Tailoring CV"></div>`;
    } else if (newest && newest.state === "failed") {
      status = `<div data-primitive="TailorStatus" data-state="failed"><span>${esc(newest.error || "Tailor failed")}</span></div>`;
    } else if (cv) {
      status = `<div data-primitive="TailorStatus" data-state="ready"><span>Ready</span></div>`;
    } else {
      status = `<div data-primitive="TailorStatus" data-state="empty"><span>No CV yet</span></div>`;
    }
    const label = cv ? "Tailor again" : "Tailor CV";
    const run = active && active.state === "queued"
      ? Btn("Run", { attrs: `data-run-job="${esc(active.id)}"` })
      : "";
    return `<section class="section" data-primitive="CvPanel">
      <h2>CV</h2>
      ${status}
      ${cv ? CvFile(cv) : ""}
      <div class="cv-actions">
        ${Btn(label, { variant: "primary", attrs: `id="enqueue-tailor" ${active ? "disabled" : ""}` })}
        ${run}
        ${CommandHint(`hunt jobs enqueue --type tailor-cv --target ${app.id} --run --json`)}
      </div>
    </section>`;
  }

  function otherFilesHtml(app, artifacts) {
    if (artifacts && artifacts.error) return ErrorBanner(artifacts.error);
    const arts = ((artifacts && artifacts.artifacts) || []).filter((a) => a.kind !== "cv");
    if (!arts.length) return `<p class="muted">No job description or notes yet.</p>`;
    return arts
      .map((a) => {
        const href = `/api/applications/${encodeURIComponent(app.id)}/artifacts/${encodeURIComponent(a.id)}/file`;
        return `<div class="artifact" data-primitive="ArtifactList">
          <span>${esc(a.kind)}</span>
          <a href="${esc(href)}" download="${esc(a.filename)}">${esc(a.filename)}</a>
          <span class="sha">${esc((a.sha256 || "").slice(0, 8))}</span>
        </div>`;
      })
      .join("");
  }

  function appJobsHtml(jobs) {
    const rows = (jobs || []).filter((j) => j.type !== "tailor-cv");
    if (!rows.length) return `<p class="muted">No other jobs for this application.</p>`;
    return rows
      .map(
        (j) => `<a class="job-link" href="/jobs/${esc(j.id)}">${JobStatePill(j.state)} ${esc(jobTypeLabel(j.type))}</a>`
      )
      .join("");
  }

  function eventsHtml(events) {
    if (events && events.error) return ErrorBanner(events.error);
    const evs = ((events && events.events) || []).slice().reverse();
    if (!evs.length) return `<p class="muted">No events.</p>`;
    return `<table data-primitive="EventLog"><thead><tr><th>Time</th><th>Actor</th><th>Verb</th><th>Detail</th></tr></thead><tbody>${evs
      .map(
        (e) => `<tr><td title="${esc(e.at)}">${esc(relative(e.at))}</td><td>${esc(e.actor || "cli")}</td><td>${esc(e.kind)}</td><td class="mono">${esc(e.body || "")}</td></tr>`
      )
      .join("")}</tbody></table>`;
  }

  async function pollDetail(appId) {
    if (state.route.name !== "detail" || state.route.id !== appId) return;
    let artifacts;
    let jobs;
    try {
      const [f, j] = await Promise.all([
        api(`/api/applications/${encodeURIComponent(appId)}/artifacts`),
        api(`/api/jobs?target=${encodeURIComponent(appId)}`),
      ]);
      artifacts = f;
      jobs = j.jobs || [];
    } catch {
      scheduleLive(() => pollDetail(appId), 2000);
      return;
    }
    if (state.route.name !== "detail" || state.route.id !== appId) return;
    const host = document.querySelector("[data-cv-host]");
    const app = state.detailApp;
    if (host && app && app.id === appId) host.innerHTML = CvPanel(app, artifacts, jobs);
    const active = activeTailorJob(jobs);
    if (active) scheduleLive(() => pollDetail(appId), 1200);
  }

  async function renderDetail(root, badges, id) {
    root.innerHTML = shell(
      "board",
      badges,
      pageHeader("Application", "", "") +
        `<div class="detail"><div class="detail-left">${LoadingSkeleton(6)}</div><div class="detail-right">${LoadingSkeleton(3)}</div></div>`
    );
    let app, events, artifacts, jobs;
    try {
      const [a, e, f, j] = await Promise.all([
        api(`/api/applications/${encodeURIComponent(id)}`),
        api(`/api/applications/${encodeURIComponent(id)}/events`).catch((err) => ({ error: err.message, events: [] })),
        api(`/api/applications/${encodeURIComponent(id)}/artifacts`).catch((err) => ({ error: err.message, artifacts: [] })),
        api(`/api/jobs?target=${encodeURIComponent(id)}`).catch(() => ({ jobs: [] })),
      ]);
      app = a.application;
      events = e;
      artifacts = f;
      jobs = j.jobs || [];
    } catch (err) {
      if (err.status === 404) {
        root.innerHTML = shell(
          "board",
          badges,
          EmptyState("Application not found", "No application with that id in this workspace.", Btn("Board", { href: "/" }))
        );
        return;
      }
      root.innerHTML = shell("board", badges, ErrorBanner(err.message));
      return;
    }
    state.detailApp = app;
    const pos = app.position || {
      company: app.company,
      role: titleOf(app),
      url: app.url,
      location: locationOf(app),
      modality: app.modality,
      engagement: app.engagement,
      source: app.source,
    };
    const q = quotedOf(app) || {};
    const quoted = quotedOf(app);
    const statusOpts = STATUSES.map((s) => `<option value="${s}" ${s === app.status ? "selected" : ""}>${s}</option>`).join("");
    const header =
      `<div data-primitive="PageHeader" class="detail-header">
        <div class="detail-header-titles">
          <h1 class="company-title">${esc(app.company)}</h1>
          <p class="role-title">${esc(titleOf(app))}</p>
        </div>
        <div class="detail-header-actions">
          <select data-primitive="StatusSelect" id="status-select">${statusOpts}</select>
          ${app.url ? Btn("Posting", { href: app.url, attrs: 'target="_blank" rel="noopener"' }) : ""}
          ${CopyId(app.id)}
        </div>
      </div>`;
    const paySummary = `<div class="section pay-summary">
      <h2>Pay</h2>
      ${PayQuoted(quoted)}
      ${quoted ? NetEstimate(app.comp_derived, { detail: true, taxHome: app.tax_home_for_net || "" }) : ""}
      ${PayDerived(app.comp_derived)}
    </div>`;
    const quotedOpen = state.quotedDirty ? " open" : "";
    const quotedForm = `<details class="fold"${quotedOpen}>
      <summary>Edit posting details</summary>
      <form id="quoted-form" class="section" data-primitive="QuotedForm">
      <h2>Quoted fields</h2>
      <div class="form-grid">
        ${FormField("Company", input("company", app.company))}
        ${FormField("Source", input("source", app.source))}
        ${FormField("URL", input("url", app.url), { span2: true })}
        ${FormField("Posted title", input("title_posted", app.title_posted))}
        ${FormField("Our title", input("title_ours", app.title_ours))}
        ${FormField("Country", input("location_country", app.location_country))}
        ${FormField("City", input("location_city", app.location_city))}
        ${FormField("Modality", select("modality", app.modality || "", modalityOptions(app.modality || "")))}
        ${FormField("Office days", input("office_days_per_week", app.office_days_per_week, "type=number step=any"))}
        ${FormField("Engagement", select("engagement", app.engagement || "", engagementOptions(app.engagement || "")))}
        ${FormField("Duration months", input("duration_months", app.duration_months, "type=number"))}
        ${FormField("Quoted amount", input("comp_amount", q.amount, "type=number step=any"))}
        ${FormField("Currency", input("comp_currency", q.currency))}
        ${FormField("Unit", select("comp_unit", q.unit || "", ["", "hour", "day", "month", "year"]))}
        ${FormField("Tax home", input("tax_home_for_net", app.tax_home_for_net))}
        ${FormField("Languages", input("languages_required", (app.languages_required || []).join(", ")))}
        ${FormField("Recruiter", input("recruiter", app.recruiter))}
        ${FormField("CV variant", input("cv_variant_id", app.cv_variant_id))}
        ${FormField("Comp notes", textarea("comp_notes", app.comp_notes), { span2: true })}
      </div>
      <div class="form-actions">${Btn("Save quoted", { variant: "primary", type: "submit" })}</div>
    </form></details>`;
    const knocks = (app.knockouts || []).map((k) => `<span class="knockout">${esc(k)}</span>`).join("") || `<span class="muted">None</span>`;
    const body = `${header}
      ${PositionFacts(pos)}
      <div data-cv-host>${CvPanel(app, artifacts, jobs)}</div>
      <div class="detail">
        <div class="detail-left">
          ${paySummary}
          <div class="section"><h2>Knockouts</h2>${knocks}</div>
        </div>
        <div class="detail-right">
          <section class="section"><h2>Other files</h2>
            <div data-primitive="ArtifactList">${otherFilesHtml(app, artifacts)}</div>
            <form id="artifact-form" class="artifact-form">
              ${FormField("File", `<label data-primitive="Btn" class="file-btn">Choose file<input type="file" name="file" required></label>`)}
              ${FormField("Kind", select("kind", "jd", ["jd", "cv", "notes", "other"]))}
              ${Btn("Add file", { type: "submit" })}
            </form>
          </section>
          <section class="section"><h2>Other jobs</h2>${appJobsHtml(jobs)}</section>
        </div>
      </div>
      ${quotedForm}
      <details class="fold"><summary>Events</summary><div class="section">${eventsHtml(events)}</div></details>
      <div class="sticky-save${state.quotedDirty ? " is-dirty" : ""}">${Btn("Save quoted", { variant: "primary", attrs: "data-submit-quoted" })}</div>`;
    root.innerHTML = shell("board", badges, body);
    if (activeTailorJob(jobs)) scheduleLive(() => pollDetail(app.id), 1200);
  }

  function inboxRowActions(it, pending) {
    if (!pending) {
      return `<div class="inbox-actions">
            ${Btn("Restore", { attrs: `data-restore="${esc(it.id)}"` })}
            ${RowMenu(it, false)}
            </div>`;
    }
    return `<div class="inbox-actions">
            ${Btn("Promote", { variant: "primary", attrs: `data-promote="${esc(it.id)}"` })}
            ${Btn("Dismiss", { variant: "danger", attrs: `data-dismiss="${esc(it.id)}"` })}
            ${RowMenu(it, true)}
            </div>`;
  }

  function inboxSourceChipIds(apiSources) {
    const rows = Array.isArray(apiSources) ? apiSources : [];
    const enabled = rows.filter((s) => s && s.id && s.enabled);
    let list = enabled.filter((s) => Number(s.inbox_count) > 0);
    if (!list.length) list = enabled.slice();
    list.sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
    const ids = list.map((s) => s.id);
    if (state.inboxSource && !ids.includes(state.inboxSource)) ids.unshift(state.inboxSource);
    return ids;
  }

  function inboxOrderOptions() {
    const current = `${state.inboxSort}:${state.inboxOrder}`;
    const opts = [
      ["created_at:desc", "Newest"],
      ["created_at:asc", "Oldest"],
      ["company:asc", "Company A–Z"],
      ["company:desc", "Company Z–A"],
      ["role:asc", "Role"],
      ["source_id:asc", "Source"],
      ["gross_month:desc", "EUR /mo"],
    ];
    const known = new Set(opts.map(([value]) => value));
    if (current && !known.has(current)) opts.push([current, "Current"]);
    return opts
      .map(([value, label]) => {
        const sel = value === current ? " selected" : "";
        return `<option value="${esc(value)}"${sel}>${esc(label)}</option>`;
      })
      .join("");
  }

  function inboxEmptyCopy() {
    if (state.inboxKnockout === "pay_below_floor|below_floor") {
      return {
        title: "Nothing below floor in pending",
        body: "Below-floor listings are dropped at screen (drop_on includes pay_below_floor). Pay-unknown rows stay in All — they are not Floor.",
      };
    }
    if (state.inboxTriage === "keep") {
      return {
        title: "No Keep listings yet",
        body: "Keep is empty until a listing is triaged Keep. Screening does not write Keep by itself.",
      };
    }
    if (state.inboxTriage === "unsure") {
      return {
        title: "No Unsure listings yet",
        body: "Unsure is empty until a listing is triaged Unsure.",
      };
    }
    if (state.inboxQuery || state.inboxSource) {
      return {
        title: "No listings in this view",
        body: "Nothing matches this filter. Clear filters to see pending.",
      };
    }
    return null;
  }

  function inboxFiltersHtml(apiSources) {
    const floorOn = state.inboxKnockout === "pay_below_floor|below_floor";
    const keepOn = state.inboxTriage === "keep";
    const unsureOn = state.inboxTriage === "unsure";
    const sourceChips = inboxSourceChipIds(apiSources)
      .map((sid) => {
        const on = state.inboxSource === sid;
        return `<button type="button" data-primitive="FilterChip" data-inbox-source="${esc(sid)}" aria-pressed="${on}">${esc(sid)}</button>`;
      })
      .join("");
    const clear = inboxFiltersActive()
      ? Btn("Clear filters", { variant: "ghost", attrs: "data-clear-filters" })
      : "";
    return `<div data-primitive="FilterBar" class="inbox-filters">
      <input class="filter-search" id="filter-search" type="search" placeholder="Filter (Enter)" title="Company, role, location, source. / focuses. Enter applies. Esc clears." value="${esc(state.inboxQuery)}" autocomplete="off">
      <select data-primitive="Select" class="inbox-order" id="inbox-order" aria-label="Order" data-inbox-order>${inboxOrderOptions()}</select>
      <div class="chip-row">
      <button type="button" data-primitive="FilterChip" data-inbox-knockout="pay_below_floor|below_floor" aria-pressed="${floorOn}">Floor</button>
      <button type="button" data-primitive="FilterChip" data-inbox-triage="keep" aria-pressed="${keepOn}">Keep</button>
      <button type="button" data-primitive="FilterChip" data-inbox-triage="unsure" aria-pressed="${unsureOn}">Unsure</button>
      ${sourceChips}
      ${clear}
      </div>
    </div>`;
  }

  function inboxHeaderExtra(visible, items, filters) {
    const pendingCount = filters && filters.inbox && filters.inbox.pending_count;
    const pendingTotal =
      state.inboxStatus !== "dismissed" && pendingCount != null ? pendingCount : items.length;
    const text = inboxFiltersActive() ? `${visible.length} / ${pendingTotal}` : String(pendingTotal);
    const count = `<span class="page-count">${esc(text)}</span>`;
    if (!filters) return count;
    const cap = (filters.inbox || {}).pending_cap;
    const pending = (filters.inbox || {}).pending_count;
    const capText = cap === 0 ? `${pending}/unlimited` : `${pending}/${cap}`;
    return `${count}<span class="inbox-filter-summary"><a href="/sources">cap ${esc(capText)}</a></span>`;
  }

  function inboxThead() {
    const cells = INBOX_COLS.map((label) => {
      const key = INBOX_SORT_KEYS[label];
      if (!key) return `<th>${esc(label)}</th>`;
      const active = state.inboxSort === key;
      let aria = "none";
      if (active) aria = state.inboxOrder === "desc" ? "descending" : "ascending";
      return `<th data-inbox-sort="${esc(key)}" aria-sort="${aria}" tabindex="0">${esc(label)}</th>`;
    }).join("");
    return `<thead><tr>${cells}</tr></thead>`;
  }

  function inboxVisible(items) {
    const q = (state.inboxQuery || "").toLowerCase();
    if (!q) return items.slice();
    return items.filter((it) => {
      const hay = [it.company, it.role || it.title, it.location, it.source_id]
        .map((x) => String(x || "").toLowerCase())
        .join(" ");
      return hay.includes(q);
    });
  }

  function inboxFiltersActive() {
    return Boolean(
      state.inboxQuery || state.inboxSource || state.inboxKnockout || state.inboxTriage
    );
  }

  async function renderInbox(root, badges) {
    const status = state.inboxStatus === "dismissed" ? "dismissed" : "pending";
    const pending = status === "pending";
    const tabs = InboxTabs(status, badges.inbox || 0);
    root.innerHTML = shell(
      "inbox",
      badges,
      pageHeader("Inbox", "", "") + tabs + LoadingSkeleton(8, INBOX_COLS)
    );
    let data;
    let filters = null;
    let apiSources = [];
    try {
      const params = new URLSearchParams({
        status,
        sort: state.inboxSort,
        order: state.inboxOrder,
      });
      if (state.inboxSource) params.set("source", state.inboxSource);
      if (state.inboxKnockout) params.set("knockout", state.inboxKnockout);
      if (state.inboxTriage) params.set("triage", state.inboxTriage);
      const fetched = await Promise.all([
        api(`/api/inbox?${params.toString()}`),
        api("/api/workspace/filters").catch(() => null),
        api("/api/sources").catch(() => ({ sources: [] })),
      ]);
      data = fetched[0];
      filters = fetched[1];
      apiSources = (fetched[2] && fetched[2].sources) || [];
    } catch (err) {
      root.innerHTML = shell("inbox", badges, ErrorBanner(err.message));
      return;
    }
    const items = data.inbox || [];
    const visible = inboxVisible(items);
    const hint = pending ? "hunt inbox list --json" : "hunt inbox list --status dismissed --json";
    const actions = CommandHint(hint);
    const header = pageHeader("Inbox", inboxHeaderExtra(visible, items, filters), actions) + tabs + inboxFiltersHtml(apiSources);
    if (!visible.length) {
      const filtered = inboxFiltersActive();
      const copy = filtered && inboxEmptyCopy();
      const empty = filtered
        ? EmptyState(
            (copy && copy.title) || "No listings in this view",
            (copy && copy.body) || "Adjust filters or clear them to see listings.",
            Btn("Clear filters", { variant: "primary", attrs: "data-clear-filters" })
          )
        : EmptyState(
            pending ? "Inbox is clear" : "No dismissed listings",
            pending
              ? "No screened listings. Run a source or enqueue screen-inbox."
              : "Dismissed listings live here. Pending stays the default tab.",
            pending ? Btn("Open sources", { href: "/sources" }) : Btn("Pending inbox", { href: "/inbox" })
          );
      root.innerHTML = shell("inbox", badges, header + empty);
      return;
    }
    const rows = visible
      .map((it) => {
        const role = it.role || it.title || "";
        const locTitle = it.location_title || it.location || "";
        return `<tr data-primitive="InboxRow" data-id="${esc(it.id)}">
          <td>${esc(it.company)}</td>
          <td>${PostingLink(it.url, role || "Posting")}</td>
          <td>${esc(it.source_id || "")}</td>
          <td title="${esc(locTitle)}">${esc(locationOf(it))}</td>
          <td>${engagementCell(it)}</td>
          <td>${PayMonth(it)}</td>
          <td title="${esc(it.why_keep || "")}">${esc(it.why_keep || "")}</td>
          <td title="${esc(it.why_risk || "")}">${esc(it.why_risk || "")}</td>
          <td>${inboxRowActions(it, pending)}</td>
        </tr>`;
      })
      .join("");
    const cards = visible
      .map((it) => {
        const role = it.role || it.title || "";
        const meta = positionMeta(it);
        return `<article data-primitive="InboxRow" class="inbox-card">
          <div class="row-line1"><strong>${esc(it.company)}</strong><span class="card-meta">${RowMenu(it, pending)}</span></div>
          <div>${PostingLink(it.url, role || "Posting")}</div>
          <div class="muted">${esc(meta)}</div>
          <div>${PayMonth(it)}</div>
          <div class="muted">${esc(it.why_keep || it.why_risk || "")}</div>
          <div class="row-actions">
            ${pending ? Btn("Promote", { variant: "primary", attrs: `data-promote="${esc(it.id)}"` }) : ""}
            ${pending ? Btn("Dismiss", { variant: "danger", attrs: `data-dismiss="${esc(it.id)}"` }) : ""}
            ${pending ? "" : Btn("Restore", { attrs: `data-restore="${esc(it.id)}"` })}
          </div>
        </article>`;
      })
      .join("");
    root.innerHTML = shell(
      "inbox",
      badges,
      header +
        `<table data-primitive="DataTable" class="inbox-table">
          ${inboxThead()}
          <tbody>${rows}</tbody>
        </table>
        <div class="inbox-cards">${cards}</div>`
    );
  }

  function isSecretConfigKey(key) {
    const k = String(key || "");
    if (k.endsWith("_env")) return false;
    return /password$|token|secret|^api_key$/i.test(k);
  }

  function prettyListOrScalar(value) {
    if (Array.isArray(value)) return value.map((x) => String(x)).join(", ");
    if (value && typeof value === "object" && typeof value.join === "function") {
      return Array.from(value).map((x) => String(x)).join(", ");
    }
    return String(value ?? "");
  }

  function sourceConfigLines(source) {
    const cfg = (source && source.config) || {};
    const kind = source && source.kind;
    const allow =
      kind === "imap_alerts"
        ? ["folder", "host_env", "user_env", "password_env", "from_contains", "subject_contains", "limit"]
        : ["profile", "query", "path", "url", "max_items", "paginate"];
    const lines = [];
    for (const key of allow) {
      if (!Object.prototype.hasOwnProperty.call(cfg, key)) continue;
      if (isSecretConfigKey(key)) continue;
      const val = cfg[key];
      if (key === "query" && val && typeof val === "object" && !Array.isArray(val)) {
        for (const [qk, qv] of Object.entries(val)) {
          if (isSecretConfigKey(qk)) continue;
          lines.push(`query.${qk}=${prettyListOrScalar(qv)}`);
        }
        continue;
      }
      if (key === "paginate" && val && typeof val === "object" && !Array.isArray(val)) {
        for (const pk of ["max_pages", "max_items", "param"]) {
          if (val[pk] == null || val[pk] === "") continue;
          lines.push(`paginate.${pk}=${val[pk]}`);
        }
        continue;
      }
      lines.push(`${key}=${prettyListOrScalar(val)}`);
    }
    return lines;
  }

  function sourceConfigHtml(source) {
    const lines = sourceConfigLines(source);
    if (!lines.length) return "";
    const body = lines.map((l) => `<div class="source-config-line">${esc(l)}</div>`).join("");
    return `<details class="source-config"><summary>config (read-only)</summary>${body}</details>`;
  }

  function filtersStripHtml(filters) {
    if (!filters) return "";
    const kn = filters.knockouts || {};
    const parts = [];
    const floor = filters.floor;
    const floorText =
      floor && floor.amount != null
        ? `${floor.amount} ${floor.currency || ""} / ${floor.unit || ""}`.trim()
        : "no comp_floor";
    parts.push(`<div><span class="muted">floor</span> ${esc(floorText)}</div>`);
    const drop = kn.drop_on || [];
    parts.push(`<div><span class="muted">drop_on</span> ${esc(drop.length ? drop.join(", ") : "—")}</div>`);
    const inbox = filters.inbox || {};
    const capText =
      inbox.pending_cap === 0
        ? `unlimited (${inbox.pending_count} pending)`
        : `${inbox.pending_count} / ${inbox.pending_cap}`;
    parts.push(`<div><span class="muted">pending cap</span> ${esc(capText)}</div>`);
    const listKeys = [
      "title_include",
      "title_exclude",
      "experience_block",
      "modality_block",
      "engagement_allow",
      "languages_block",
    ];
    let any = false;
    for (const k of listKeys) {
      const vals = kn[k] || [];
      if (vals.length) {
        any = true;
        parts.push(`<div><span class="muted">${esc(k)}</span> ${esc(vals.join(", "))}</div>`);
      }
    }
    if (!any) parts.push(`<div>no knockouts in config.yaml</div>`);
    if (filters.poll && filters.poll.help) {
      parts.push(`<div class="muted">${esc(filters.poll.help)}</div>`);
    }
    return `<div data-primitive="FiltersStrip">${parts.join("")}${CommandHint("edit $HUNT_DATA/config.yaml")}</div>`;
  }

  async function renderSources(root, badges) {
    root.innerHTML = shell("sources", badges, pageHeader("Sources", "", "") + LoadingSkeleton(4, SOURCE_COLS));
    let data;
    let filters = null;
    try {
      const fetched = await Promise.all([
        api("/api/sources"),
        api("/api/workspace/filters").catch(() => null),
      ]);
      data = fetched[0];
      filters = fetched[1];
    } catch (err) {
      root.innerHTML = shell("sources", badges, ErrorBanner(err.message));
      return;
    }
    const sources = data.sources || [];
    const strip = filtersStripHtml(filters);
    if (!sources.length) {
      root.innerHTML = shell(
        "sources",
        badges,
        pageHeader("Sources", "", CommandHint("hunt sources list --json")) +
          strip +
          EmptyState(
            "No sources configured",
            "Add them in config.yaml (v1 has no source editor).",
            ""
          )
      );
      return;
    }
    const rows = sources
      .map(
        (s) => `<tr data-primitive="SourceRow">
          <td>${esc(s.name)}${sourceConfigHtml(s)}</td>
          <td>${esc(s.kind)}</td>
          <td>${s.enabled ? "yes" : "no"}</td>
          <td title="${esc(s.last_run_at || "")}">${esc(s.last_run_at ? relative(s.last_run_at) : "—")}</td>
          <td class="danger-text">${esc(s.last_error || "")}</td>
          <td>${esc(s.listing_count)}</td>
          <td>${esc(s.inbox_count)}</td>
          <td>${Btn("Run now", { attrs: `data-run-source="${esc(s.id)}" ${s.enabled ? "" : "disabled"}` })} ${CommandHint(`hunt sources run ${s.id} --json`)}</td>
          <td>${CopyId(s.id)}</td>
        </tr>`
      )
      .join("");
    const cards = sources
      .map(
        (s) => `<article data-primitive="Row" class="source-card">
          <div class="row-line1"><strong>${esc(s.name)}</strong><span class="muted">${s.enabled ? "enabled" : "disabled"}</span></div>
          <div>${esc(s.kind)}</div>
          <div class="faint" title="${esc(s.last_run_at || "")}">${esc(s.last_run_at ? relative(s.last_run_at) : "never run")}</div>
          <div class="muted">${esc(s.listing_count)} listings · ${esc(s.inbox_count)} inbox</div>
          ${s.last_error ? `<div class="danger-text">${esc(s.last_error)}</div>` : ""}
          ${sourceConfigHtml(s)}
          <div class="row-actions">
            ${Btn("Run now", { attrs: `data-run-source="${esc(s.id)}" ${s.enabled ? "" : "disabled"}` })}
          </div>
        </article>`
      )
      .join("");
    root.innerHTML = shell(
      "sources",
      badges,
      pageHeader("Sources", `<span class="page-count">${sources.length}</span>`, CommandHint("hunt sources list --json")) +
        strip +
        `<table data-primitive="DataTable" class="sources-table">
          <thead><tr><th>Name</th><th>Adapter</th><th>Enabled</th><th>Last run</th><th>Last error</th><th>Listings</th><th>Inbox</th><th></th><th>Id</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="sources-cards">${cards}</div>`
    );
  }

  function jobPositionText(job) {
    const subject = (job && job.subject) || {};
    if (subject.kind === "application" && subject.company) {
      return subject.role ? `${subject.company} — ${subject.role}` : subject.company;
    }
    return subject.label || subject.company || job.target_id || "—";
  }

  function jobPositionCell(job) {
    const subject = (job && job.subject) || {};
    const text = esc(jobPositionText(job));
    if (subject.kind !== "application") return text;
    const meta = [subject.location, subject.engagement_label].filter(Boolean).join(" · ");
    return `<div>${text}</div>${meta ? `<div class="muted">${esc(meta)}</div>` : ""}`;
  }

  function jobPayCell(job) {
    const subject = (job && job.subject) || {};
    if (!subject.pay_month) return `<span class="muted">—</span>`;
    return PayMonth(subject);
  }

  function jobOutcome(job) {
    const result = (job && job.result) || {};
    if (!job || job.state !== "done") return "";
    if (job.type === "tailor-cv") {
      if (result.artifact) return CvFile(result.artifact);
      if (result.path) return `<p class="cv-path">${esc(result.path)}</p>`;
      return "";
    }
    if (job.type === "source-poll") {
      return `<p>${esc(result.new ?? 0)} new listings. ${esc(result.listings ?? 0)} fetched.</p>`;
    }
    if (job.type === "screen-inbox") {
      return `<p>Screened ${esc(result.screened ?? 0)}. Added ${esc(result.inbox_added ?? 0)} to the inbox. Dropped ${esc(result.dropped ?? 0)}.</p>`;
    }
    if (job.type === "triage-inbox") {
      return `<p>Kept ${esc(result.kept ?? 0)}. Unsure ${esc(result.unsure ?? 0)}. Dismissed ${esc(result.dismissed ?? 0)}.</p>`;
    }
    return "";
  }

  function jobLiveHtml(job) {
    const active = job.state === "queued" || job.state === "running";
    const label = job.state === "running" ? "Running" : job.state === "queued" ? "Queued" : job.state === "done" ? "Done" : "Failed";
    const progress = active
      ? `<div class="tailor-progress" role="progressbar" aria-label="${esc(label)}"></div>`
      : "";
    const run = job.state === "queued"
      ? Btn("Run", { variant: "primary", attrs: `data-run-job="${esc(job.id)}"` })
      : "";
    const error = job.error ? `<p class="danger-text">${esc(job.error)}</p>` : "";
    const when = `<dl class="job-times">
      <div><dt>Created</dt><dd title="${esc(job.created_at || "")}">${esc(job.created_at ? relative(job.created_at) : "—")}</dd></div>
      <div><dt>Started</dt><dd>${esc(job.started_at ? relative(job.started_at) : "—")}</dd></div>
      <div><dt>Finished</dt><dd>${esc(job.finished_at ? relative(job.finished_at) : "—")}</dd></div>
    </dl>`;
    return `<div data-primitive="TailorStatus" data-state="${esc(job.state)}"><span>${esc(label)}</span>${JobStatePill(job.state)}</div>
      ${progress}
      ${error}
      ${jobOutcome(job)}
      <div class="cv-actions">${run}</div>
      ${when}`;
  }

  async function pollJob(jobId) {
    if (state.route.name !== "job" || state.route.id !== jobId) return;
    let job;
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      job = data.job;
    } catch {
      scheduleLive(() => pollJob(jobId), 2000);
      return;
    }
    if (state.route.name !== "job" || state.route.id !== jobId) return;
    const host = document.querySelector("[data-job-live]");
    if (host) host.innerHTML = jobLiveHtml(job);
    if (job.state === "queued" || job.state === "running") scheduleLive(() => pollJob(jobId), 1200);
  }

  async function renderJobs(root, badges) {
    root.innerHTML = shell("jobs", badges, pageHeader("Jobs", "", "") + jobFiltersHtml() + LoadingSkeleton(8, JOB_COLS));
    let data;
    try {
      data = await api("/api/jobs");
    } catch (err) {
      root.innerHTML = shell("jobs", badges, ErrorBanner(err.message));
      return;
    }
    const all = data.jobs || [];
    const jobs = all.filter(
      (j) => state.jobStateFilters.has(j.state) && state.jobTypeFilters.has(j.type)
    );
    const typeOptions = JOB_TYPES.map((t) => ({ value: t, label: jobTypeLabel(t) }));
    const enqueue = `<form id="enqueue-form" class="enqueue-form">
      ${select("type", "screen-inbox", typeOptions)}
      <input data-primitive="TextInput" name="target_id" placeholder="target id">
      ${Btn("Enqueue", { variant: "primary", type: "submit" })}
      ${CommandHint("hunt jobs enqueue --type screen-inbox --json")}
    </form>`;
    if (!all.length) {
      root.innerHTML = shell(
        "jobs",
        badges,
        pageHeader("Jobs", `<span class="page-count">0</span>`, enqueue) +
          jobFiltersHtml() +
          EmptyState(
            "No jobs",
            "Run a source, screen the inbox, or tailor a CV from an application.",
            Btn("Open sources", { href: "/sources" })
          )
      );
      return;
    }
    if (!jobs.length) {
      root.innerHTML = shell(
        "jobs",
        badges,
        pageHeader("Jobs", `<span class="page-count">0 / ${all.length}</span>`, enqueue) +
          jobFiltersHtml() +
          EmptyState("No jobs in this view", "Adjust state or type filters, or enqueue a job.", "")
      );
      return;
    }
    const rows = jobs
      .map(
        (j) => `<tr data-primitive="JobRow" data-href="/jobs/${esc(j.id)}" tabindex="0">
          <td>${esc(jobTypeLabel(j.type))}</td>
          <td>${jobPositionCell(j)}</td>
          <td>${jobPayCell(j)}</td>
          <td>${JobStatePill(j.state)}</td>
          <td title="${esc(j.created_at)}">${esc(relative(j.created_at))}</td>
          <td class="danger-text">${esc(j.error || "")}</td>
        </tr>`
      )
      .join("");
    const cards = jobs
      .map(
        (j) => `<article data-primitive="Row" class="job-card" data-href="/jobs/${esc(j.id)}">
          <div class="row-line1"><strong>${esc(jobPositionText(j))}</strong>${JobStatePill(j.state)}</div>
          <div class="muted">${esc(jobTypeLabel(j.type))}</div>
          <div>${jobPayCell(j)}</div>
          ${j.error ? `<div class="danger-text">${esc(j.error)}</div>` : ""}
        </article>`
      )
      .join("");
    root.innerHTML = shell(
      "jobs",
      badges,
      pageHeader("Jobs", `<span class="page-count">${jobs.length}</span>`, enqueue) +
        jobFiltersHtml() +
        `<table data-primitive="DataTable" class="jobs-table">
          <thead><tr>${JOB_COLS.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="jobs-cards">${cards}</div>`
    );
  }

  async function renderJob(root, badges, id) {
    root.innerHTML = shell("jobs", badges, pageHeader("Job", "", "") + LoadingSkeleton(4));
    let job;
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(id)}`);
      job = data.job;
    } catch (err) {
      if (err.status === 404) {
        root.innerHTML = shell(
          "jobs",
          badges,
          EmptyState("Job not found", "No job with that id in this workspace.", Btn("Jobs", { href: "/jobs" }))
        );
        return;
      }
      root.innerHTML = shell("jobs", badges, ErrorBanner(err.message));
      return;
    }
    const subject = job.subject || {};
    const label = jobTypeLabel(job.type);
    const heading = subject.kind === "application" && subject.company ? subject.company : label;
    const role = subject.kind === "application" ? subject.role : "";
    const facts = subject.kind === "application" ? PositionFacts(subject) : "";
    const openApp = subject.kind === "application" && subject.id
      ? Btn("Open application", { href: `/applications/${subject.id}` })
      : subject.kind === "source"
        ? Btn("Sources", { href: "/sources" })
        : subject.kind === "inbox"
          ? Btn("Inbox", { href: "/inbox" })
          : "";
    const header = `<div data-primitive="PageHeader" class="detail-header">
      <div class="detail-header-titles">
        <p class="kicker">${esc(label)}</p>
        <h1 class="company-title">${esc(heading)}</h1>
        ${role ? `<p class="role-title">${esc(role)}</p>` : ""}
      </div>
      <div class="detail-header-actions">
        ${openApp}
        ${Btn("All jobs", { href: "/jobs" })}
        ${CopyId(job.id)}
      </div>
    </div>`;
    const body = `${header}${facts}<section class="section" data-job-live>${jobLiveHtml(job)}</section>`;
    root.innerHTML = shell("jobs", badges, body);
    if (job.state === "queued" || job.state === "running") scheduleLive(() => pollJob(job.id), 1200);
  }

  function knowledgeConflictHtml() {
    const c = state.profileConflict;
    if (!c) return "";
    const diff = c.showDiff
      ? `<div class="conflict-diff">
          <div class="section"><h2>Your draft</h2><pre>${esc(c.local || "")}</pre></div>
          <div class="section"><h2>On disk</h2><pre>${esc(c.server || "")}</pre></div>
        </div>`
      : "";
    return `<div data-primitive="KnowledgeConflict">
      <span class="msg">${esc(c.message || "This fact changed elsewhere. Your draft is not saved.")}</span>
      <div class="conflict-actions">
        ${Btn("Reload", { attrs: "data-conflict-reload" })}
        ${Btn("Review diff", { variant: "ghost", attrs: "data-conflict-diff" })}
        ${Btn("Cancel", { variant: "ghost", attrs: "data-conflict-cancel" })}
      </div>
      ${diff}
    </div>`;
  }

  function rowList(path, rows, fields, addLabel) {
    const items = (rows || [])
      .map((row, i) => {
        const controls = fields
          .map((f) => {
            if (f.type === "select") {
              return select("", row[f.key] || "", f.options, `data-path="${path}.${i}.${f.key}"`);
            }
            return input("", row[f.key] || "", `data-path="${path}.${i}.${f.key}" placeholder="${esc(f.placeholder || f.key)}"`);
          })
          .join("");
        return `<div class="row-list-row">${controls}${Btn("Remove", { variant: "ghost", attrs: `data-remove-path="${path}.${i}"` })}</div>`;
      })
      .join("");
    return `<div class="row-list">${items}${Btn(addLabel, { variant: "ghost", attrs: `data-add-path="${path}"` })}</div>`;
  }

  function ScopeFactsEditor(facts, path) {
    const rows = (facts || [])
      .map(
        (fact, i) => `<div class="scope-row">
          ${textarea("", fact, `data-path="${path}.${i}" class="scope-input"`)}
          ${Btn("Remove", { variant: "ghost", attrs: `data-remove-path="${path}.${i}"` })}
        </div>`
      )
      .join("");
    return `<div data-primitive="ScopeFactsEditor">
      ${rows}
      ${Btn("Add fact", { variant: "ghost", attrs: `data-add-path="${path}"` })}
      <div class="help">Hard boundaries. Interviewers must not catch an over-claim.</div>
    </div>`;
  }

  function IntegrityPanel(integ) {
    const phrases = (integ.forbidden_phrases || []).map((p) => `<li>${esc(p)}</li>`).join("") || "<li class=\"muted\">None</li>";
    const traps = (integ.line_traps || [])
      .map((t) => `<li>never put ${esc(t.term)} on a line with ${esc(t.never_with)}</li>`)
      .join("") || "<li class=\"muted\">None</li>";
    const quants = (integ.quantifier_terms || []).map((q) => `<li>${esc(q)}</li>`).join("") || "<li class=\"muted\">None</li>";
    return `<div data-primitive="IntegrityPanel" class="section">
      <p class="fact-caption">Honesty rules for this workspace. Hunt source does not hardcode employers. To change a rule, edit $HUNT_DATA/knowledge/integrity.yaml — agents must not weaken entries.</p>
      <h2>Forbidden phrases</h2>
      <ul>${phrases}</ul>
      <h2>Line traps</h2>
      <ul>${traps}</ul>
      <h2>Quantifier terms</h2>
      <ul>${quants}</ul>
    </div>`;
  }

  function profileHeader(draft, actions) {
    const name = (draft.profile && draft.profile.name) || "Profile";
    const headline = (draft.profile && draft.profile.headlines && draft.profile.headlines.generic) || "";
    const positions = draft.positions || [];
    const achievements = draft.achievements || [];
    let drafts = 0;
    let verified = 0;
    for (const row of positions.concat(achievements)) {
      if (row && row.verified) verified += 1;
      else drafts += 1;
    }
    return `<div data-primitive="PageHeader" class="profile-header">
      <h1>${esc(name)}</h1>
      <p class="profile-kicker">${esc(headline)}</p>
      <span class="profile-counts">Draft ${drafts} · Verified ${verified}</span>
      <div class="header-actions">${actions || ""}</div>
      ${ProfileNav(state.route.tab)}
    </div>`;
  }

  function renderProfileTab(draft) {
    const profile = draft.profile || {};
    const headlines = profile.headlines || {};
    const headlineFields = Object.keys(headlines).length
      ? Object.keys(headlines)
          .map((key) => FormField(`Headline (${key})`, input("", headlines[key] || "", `data-path="profile.headlines.${key}"`), { span2: true }))
          .join("")
      : FormField("Headline (generic)", input("", "", `data-path="profile.headlines.generic"`), { span2: true });
    const langs = rowList("profile.languages", profile.languages || [], [
      { key: "name", placeholder: "Language" },
      { key: "level", placeholder: "level" },
    ], "Add language");
    const edu = rowList("profile.education", profile.education || [], [
      { key: "degree", placeholder: "Degree" },
      { key: "institution", placeholder: "Institution" },
      { key: "year", placeholder: "Year" },
    ], "Add education");
    const contact = profile.contact || {};
    return `<form id="profile-form" class="section profile-page">
      <div class="form-grid">
        ${FormField("Name", input("", profile.name || "", `data-path="profile.name"`))}
        ${headlineFields}
        ${FormField("Location", input("", profile.location || "", `data-path="profile.location"`))}
        ${FormField("Citizenship", input("", profile.citizenship || "", `data-path="profile.citizenship"`))}
        ${FormField("Languages", langs, { span2: true })}
        ${FormField("Email", input("", contact.email || "", `type="email" data-path="profile.contact.email" autocomplete="off"`))}
        ${FormField("Phone", input("", contact.phone || "", `data-path="profile.contact.phone" autocomplete="off"`))}
        ${FormField("LinkedIn", input("", contact.linkedin || "", `data-path="profile.contact.linkedin"`))}
        ${FormField("Education", edu, { span2: true })}
      </div>
      <div class="form-actions">${Btn("Save profile", { variant: "primary", type: "submit" })}</div>
    </form>`;
  }

  function renderPositionsTab(draft) {
    const positions = (draft.positions || []).slice().sort((a, b) => String(b.start || "").localeCompare(String(a.start || "")));
    const overlaps = overlappingPositions(draft.positions || []);
    const achIds = (draft.achievements || []).map((a) => a.id).filter(Boolean);
    if (!positions.length) {
      return EmptyState("No positions yet", "Add the current role first.", Btn("Add position", { variant: "primary", attrs: "data-add-position" }));
    }
    const cards = positions
      .map((p) => {
        const idx = draft.positions.indexOf(p);
        const present = !p.end || p.end === "present";
        const facts = p.scope_facts || [];
        const defaults = p.default_achievements || [];
        const chips = defaults
          .map((id) => `<span class="chip"><a href="/profile?tab=achievements#ach-${esc(id)}">${esc(id)}</a>${Btn("×", { variant: "ghost", attrs: `data-remove-path="positions.${idx}.default_achievements.${defaults.indexOf(id)}"` })}</span>`)
          .join("");
        const unused = achIds.filter((id) => !defaults.includes(id));
        const addSel = unused.length
          ? `<select data-primitive="Select" data-add-achievement="${idx}"><option value="">Add achievement…</option>${unused.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join("")}</select>`
          : "";
        const warn = !p.verified && !facts.length ? `<p class="overlap-warn">Add scope facts before confirming.</p>` : "";
        const employerErr = !String(p.employer || "").trim() ? `<div class="err">Employer is required.</div>` : "";
        const confirmBtn = p.verified
          ? Btn("Mark as draft", { variant: "ghost", attrs: `data-unverify="position:${esc(p.id || "")}:${idx}"` })
          : Btn("Confirm", {
              variant: "primary",
              attrs: `data-open-confirm="position:${esc(p.id || "")}" ${!p.id || !facts.length || !String(p.employer || "").trim() ? "disabled" : ""}`,
            });
        return `<article class="fact-card${p.verified ? "" : " is-draft"}" data-position-index="${idx}">
          <div class="fact-card-head">
            <h2>${esc(p.title || "New position")} · ${esc(p.employer || "")}</h2>
            ${p.verified ? VerifiedBadge() : DraftBadge()}
            ${p.id ? CopyId(p.id) : ""}
          </div>
          ${warn}
          <div class="form-grid">
            ${FormField("Title", input("", p.title || "", `data-path="positions.${idx}.title"`))}
            ${FormField("Employer", input("", p.employer || "", `data-path="positions.${idx}.employer"`) + employerErr)}
            ${FormField("Location", input("", p.location || "", `data-path="positions.${idx}.location"`))}
            ${FormField("Start", input("", p.start || "", `data-path="positions.${idx}.start" placeholder="YYYY-MM"`))}
            ${FormField("End", input("", present ? "present" : p.end || "", `data-path="positions.${idx}.end" ${present ? "disabled" : ""}`))}
            ${FormField("Current role", `<label><input type="checkbox" data-present-index="${idx}" ${present ? "checked" : ""}> present</label>`)}
            ${FormField("Scope facts", ScopeFactsEditor(facts, `positions.${idx}.scope_facts`), { span2: true })}
            ${FormField("Default achievements", `<div class="chip-row">${chips}${addSel}</div>`, { span2: true })}
          </div>
          <div class="fact-actions">
            ${Btn("Save position", { variant: "primary", attrs: `data-save-position="${idx}"` })}
            ${confirmBtn}
            ${p.id ? CommandHint(`hunt positions update ${p.id} --json`) : ""}
          </div>
        </article>`;
      })
      .join("");
    const overlapHtml = overlaps.length ? `<p class="overlap-warn">${esc(overlaps.join(" · "))}</p>` : "";
    return `${overlapHtml}${cards}<p>${Btn("Add position", { attrs: "data-add-position" })}</p>`;
  }

  function renderAchievementsTab(draft) {
    const achievements = draft.achievements || [];
    const positions = draft.positions || [];
    const terms = (draft.integrity && draft.integrity.quantifier_terms) || ["%", "x faster", "fold"];
    if (!achievements.length) {
      return EmptyState(
        "No achievements",
        "Draft bullets here; they will not render until you confirm.",
        Btn("Add achievement", { variant: "primary", attrs: "data-add-achievement-row" })
      );
    }
    const assigned = new Set();
    const groups = [];
    for (const pos of positions) {
      const ids = pos.default_achievements || [];
      const rows = ids.map((id) => achievements.find((a) => a.id === id)).filter(Boolean);
      rows.forEach((a) => assigned.add(a.id));
      groups.push({ title: `${pos.employer || pos.id} · ${pos.title || ""}`, rows });
    }
    const orphans = achievements.filter((a) => !assigned.has(a.id));
    if (orphans.length) groups.push({ title: "Unassigned", rows: orphans });

    const cards = groups
      .map((g) => {
        const body = g.rows
          .map((a) => {
            const idx = achievements.indexOf(a);
            const confirmDisabled = !a.id || !canConfirmAchievement(a, terms);
            const evidenceErr = !String(a.evidence || "").trim()
              ? "Evidence is required to confirm."
              : isQuantified(a.text, terms) && weakEvidence(a.evidence)
                ? "Quantified claims need evidence beyond “Own work.”"
                : "";
            const confirmBtn = a.verified
              ? Btn("Mark as draft", { variant: "ghost", attrs: `data-unverify="achievement:${esc(a.id)}:${idx}"` })
              : Btn("Confirm", {
                  variant: "primary",
                  attrs: `data-open-confirm="achievement:${esc(a.id || "")}" ${confirmDisabled ? "disabled" : ""}`,
                });
            return `<article class="fact-card${a.verified ? "" : " is-draft"}" id="ach-${esc(a.id || idx)}">
              <div class="fact-card-head">
                <h2>${esc(a.id || "New achievement")}</h2>
                ${a.verified ? VerifiedBadge() : DraftBadge()}
                ${a.id ? CopyId(a.id) : ""}
              </div>
              ${a.verified ? "" : `<p class="fact-caption">Excluded from CVs until you confirm.</p>`}
              <div class="form-grid">
                ${FormField("Text", textarea("", a.text || "", `data-path="achievements.${idx}.text"`), { span2: true })}
                ${FormField("Tags", input("", (a.tags || []).join(", "), `data-path="achievements.${idx}.tagsText"`), { help: "Comma-separated. Existing vocabulary is reused when possible." })}
                ${FormField("Evidence", textarea("", a.evidence || "", `data-path="achievements.${idx}.evidence"`) + (evidenceErr ? `<div class="err">${esc(evidenceErr)}</div>` : ""), { span2: true, help: "A repo, doc, review, or named manager reference." })}
              </div>
              <div class="fact-actions">
                ${Btn("Save achievement", { variant: "primary", attrs: `data-save-achievement="${idx}"` })}
                ${confirmBtn}
                ${a.id ? CommandHint(`hunt achievements update ${a.id} --json`) : ""}
              </div>
            </article>`;
          })
          .join("");
        return `<section class="profile-page"><h2 class="fact-caption">${esc(g.title)}</h2>${body}</section>`;
      })
      .join("");
    return `${cards}<p>${Btn("Add achievement", { attrs: "data-add-achievement-row" })}</p>`;
  }

  function renderSkillsTab(draft) {
    const skills = draft.skills || {};
    const groups = skills.skill_groups || [];
    const posIds = (draft.positions || []).map((p) => p.id).filter(Boolean);
    const scopeOpts = [""].concat(posIds);
    if (!groups.length) {
      return EmptyState("No skills yet", "Add a group, then the tools you can whiteboard.", Btn("Add group", { attrs: "data-add-skill-group" }));
    }
    const body = groups
      .map((g, gi) => {
        const rows = (g.skills || [])
          .map((s, si) => {
            return `<div class="row-list-row">
              ${input("", s.name || "", `data-path="skills.skill_groups.${gi}.skills.${si}.name" placeholder="Skill"`)}
              ${select("", s.level || "working", SKILL_LEVELS, `data-path="skills.skill_groups.${gi}.skills.${si}.level"`)}
              ${select("", s.employer_scope || "", scopeOpts, `data-path="skills.skill_groups.${gi}.skills.${si}.employer_scope"`)}
              ${Btn("Remove", { variant: "ghost", attrs: `data-remove-path="skills.skill_groups.${gi}.skills.${si}"` })}
            </div>`;
          })
          .join("");
        return `<article class="fact-card">
          <div class="fact-card-head">
            ${FormField("Group name", input("", g.name || "", `data-path="skills.skill_groups.${gi}.name"`))}
          </div>
          <p class="fact-caption">production = operated in a real job and can whiteboard it. Employer scope omits the skill from generic lines.</p>
          <div class="row-list">${rows}${Btn("Add skill", { variant: "ghost", attrs: `data-add-skill="${gi}"` })}</div>
        </article>`;
      })
      .join("");
    const claims = (skills.forbidden_claims || []).map((c) => `<li>${esc(c)}</li>`).join("") || `<li class="muted">None</li>`;
    return `${body}<p>${Btn("Add group", { attrs: "data-add-skill-group" })} ${Btn("Save skills", { variant: "primary", attrs: "data-save-skills" })} ${CommandHint("hunt skills update --json")}</p>
      <div class="section"><h2>Forbidden claims</h2>
        <p class="fact-caption">Read-only. Editing forbidden claims is out of this surface — too easy to weaken.</p>
        <ul class="readonly-list">${claims}</ul>
      </div>`;
  }

  function renderProjectsTab(draft) {
    const projects = draft.projects || [];
    const certs = draft.certifications || [];
    const cards = projects.length
      ? projects
          .map((p, i) => {
            const bullets = (p.bullets || [])
              .map(
                (b, bi) => `<div class="scope-row">${textarea("", b, `data-path="projects.${i}.bullets.${bi}"`)}${Btn("Remove", { variant: "ghost", attrs: `data-remove-path="projects.${i}.bullets.${bi}"` })}</div>`
              )
              .join("");
            return `<article class="fact-card">
              <div class="fact-card-head">
                <h2>${esc(p.name || "New project")}</h2>
                ${p.id ? CopyId(p.id) : ""}
              </div>
              <p class="fact-caption">Personal-project scope. Never describe as professional production experience.</p>
              <div class="form-grid">
                ${FormField("Name", input("", p.name || "", `data-path="projects.${i}.name"`))}
                ${FormField("Note", input("", p.note || "", `data-path="projects.${i}.note"`))}
                ${FormField("Bullets", `<div data-primitive="ScopeFactsEditor">${bullets}${Btn("Add bullet", { variant: "ghost", attrs: `data-add-path="projects.${i}.bullets"` })}</div>`, { span2: true })}
              </div>
              <div class="fact-actions">
                ${Btn("Save project", { variant: "primary", attrs: `data-save-project="${i}"` })}
                ${p.id ? CommandHint(`hunt projects update ${p.id} --json`) : ""}
              </div>
            </article>`;
          })
          .join("")
      : EmptyState("No projects yet", "Personal labs belong here, not in employment history.", Btn("Add project", { attrs: "data-add-project" }));
    const certRows = rowList("certifications", certs, [
      { key: "name", placeholder: "Certification" },
      { key: "year", placeholder: "Year" },
    ], "Add certification");
    return `${cards}<p>${Btn("Add project", { attrs: "data-add-project" })}</p>
      <div class="section">
        <h2>Certifications</h2>
        ${certRows}
        <div class="form-actions">${Btn("Save certifications", { variant: "primary", attrs: "data-save-certifications" })}</div>
      </div>`;
  }

  async function loadKnowledge() {
    const [profile, positions, achievements, skills, projects, integrity] = await Promise.all([
      api("/api/profile"),
      api("/api/positions"),
      api("/api/achievements"),
      api("/api/skills"),
      api("/api/projects"),
      api("/api/integrity"),
    ]);
    const server = {
      profile: profile.profile || {},
      positions: positions.positions || [],
      achievements: achievements.achievements || [],
      skills: skills.skills || {},
      projects: projects.projects || [],
      certifications: projects.certifications || [],
      integrity: integrity.integrity || {},
    };
    for (const a of server.achievements) {
      a.tagsText = (a.tags || []).join(", ");
    }
    state.knowledge = {
      server: clone(server),
      draft: clone(server),
      revisions: {
        profile: profile.revision,
        positions: positions.revision,
        achievements: achievements.revision,
        skills: skills.revision,
        projects: projects.revision,
        integrity: integrity.revision,
      },
    };
  }

  async function renderProfile(root, badges) {
    const loading =
      profileHeader({ profile: { name: state.meta && state.meta.profile_name, headlines: {} }, positions: [], achievements: [] }, CommandHint("hunt profile get --json")) +
      `<div class="profile-page">${LoadingSkeleton(3)}</div>`;
    if (!state.knowledge) {
      root.innerHTML = shell("profile", badges, loading);
      try {
        await loadKnowledge();
      } catch (err) {
        const emptyKb = /knowledge/i.test(err.message || "");
        root.innerHTML = shell(
          "profile",
          badges,
          emptyKb
            ? EmptyState("No profile yet", "Copy example-workspace and replace Jane Doe.", "")
            : ErrorBanner(err.message)
        );
        return;
      }
    }
    const draft = state.knowledge.draft;
    const tab = state.route.tab || "positions";
    const cli =
      tab === "profile"
        ? "hunt profile get --json"
        : tab === "positions"
          ? "hunt positions list --json"
          : tab === "achievements"
            ? "hunt achievements list --json"
            : tab === "skills"
              ? "hunt skills get --json"
              : tab === "projects"
                ? "hunt projects list --json"
                : "hunt integrity get --json";
    let tabBody;
    if (tab === "profile") tabBody = renderProfileTab(draft);
    else if (tab === "positions") tabBody = renderPositionsTab(draft);
    else if (tab === "achievements") tabBody = renderAchievementsTab(draft);
    else if (tab === "skills") tabBody = renderSkillsTab(draft);
    else if (tab === "projects") tabBody = renderProjectsTab(draft);
    else tabBody = IntegrityPanel(draft.integrity || {});
    const sticky = `<div class="sticky-save${state.profileDirty ? " is-dirty" : ""}">${Btn("Save", { variant: "primary", attrs: "data-save-profile-tab" })}</div>`;
    root.innerHTML = shell(
      "profile",
      badges,
      profileHeader(draft, CommandHint(cli)) + knowledgeConflictHtml() + `<div class="profile-page">${tabBody}</div>` + sticky
    );
  }

  function isConflict(err) {
    return Boolean(err && err.status === 409);
  }

  async function noteConflict(err, localObj) {
    const kept = state.knowledge ? clone(state.knowledge.draft) : null;
    const keptRev = state.knowledge ? Object.assign({}, state.knowledge.revisions) : null;
    let server = "";
    try {
      const [profile, positions, achievements, skills, projects, integrity] = await Promise.all([
        api("/api/profile"),
        api("/api/positions"),
        api("/api/achievements"),
        api("/api/skills"),
        api("/api/projects"),
        api("/api/integrity"),
      ]);
      server = JSON.stringify(
        {
          profile: profile.profile,
          positions: positions.positions,
          achievements: achievements.achievements,
          skills: skills.skills,
          projects: projects.projects,
          certifications: projects.certifications,
        },
        null,
        2
      );
      state.knowledge.server = {
        profile: profile.profile || {},
        positions: positions.positions || [],
        achievements: achievements.achievements || [],
        skills: skills.skills || {},
        projects: projects.projects || [],
        certifications: projects.certifications || [],
        integrity: integrity.integrity || {},
      };
      if (kept) state.knowledge.draft = kept;
      if (keptRev) state.knowledge.revisions = keptRev;
    } catch {
      /* keep local draft */
    }
    state.profileConflict = {
      message: (err.data && err.data.error) || err.message || "This fact changed elsewhere. Your draft is not saved.",
      local: typeof localObj === "string" ? localObj : JSON.stringify(localObj, null, 2),
      server,
      showDiff: false,
    };
  }

  async function saveProfile() {
    const p = state.knowledge.draft.profile;
    const body = {
      name: p.name,
      headlines: p.headlines,
      location: p.location,
      citizenship: p.citizenship,
      languages: p.languages || [],
      contact: p.contact || {},
      education: p.education || [],
    };
    try {
      const res = await api("/api/profile", {
        method: "PATCH",
        body: JSON.stringify(body),
        ifMatch: state.knowledge.revisions.profile,
      });
      state.knowledge.revisions.profile = res.revision;
      state.knowledge.server.profile = clone(res.profile);
      state.knowledge.draft.profile = clone(res.profile);
      state.profileDirty = false;
      showToast("Saved");
    } catch (err) {
      if (isConflict(err)) await noteConflict(err, body);
      else alert(err.message);
      render();
    }
  }

  function positionPayload(p) {
    const body = {
      title: p.title,
      employer: p.employer,
      location: p.location,
      start: p.start,
      end: p.end || "present",
      scope_facts: p.scope_facts || [],
      default_achievements: p.default_achievements || [],
    };
    if (p.client) body.client = p.client;
    return body;
  }

  async function savePosition(idx) {
    const p = state.knowledge.draft.positions[idx];
    if (!p) return;
    if (!String(p.employer || "").trim() || !String(p.title || "").trim() || !String(p.start || "").trim()) {
      alert("Position needs title, employer, and start.");
      return;
    }
    const body = positionPayload(p);
    try {
      let res;
      if (!p.id) {
        res = await api("/api/positions", {
          method: "POST",
          body: JSON.stringify(body),
          ifMatch: state.knowledge.revisions.positions,
        });
        state.knowledge.draft.positions[idx] = clone(res.position);
      } else {
        res = await api(`/api/positions/${encodeURIComponent(p.id)}`, {
          method: "PATCH",
          body: JSON.stringify(body),
          ifMatch: state.knowledge.revisions.positions,
        });
        state.knowledge.draft.positions[idx] = Object.assign(p, res.position);
      }
      state.knowledge.revisions.positions = res.revision;
      state.profileDirty = false;
      showToast("Saved");
    } catch (err) {
      if (isConflict(err)) await noteConflict(err, body);
      else alert(err.message);
      render();
    }
  }

  async function saveAchievement(idx) {
    const a = state.knowledge.draft.achievements[idx];
    if (!a) return;
    const tags = splitTags(a.tagsText != null ? a.tagsText : (a.tags || []).join(", "));
    const body = { text: a.text, tags, evidence: a.evidence };
    try {
      let res;
      if (!a.id) {
        if (!body.text || !body.evidence) {
          alert("Achievement needs text and evidence.");
          return;
        }
        res = await api("/api/achievements", {
          method: "POST",
          body: JSON.stringify(body),
          ifMatch: state.knowledge.revisions.achievements,
        });
        const row = clone(res.achievement);
        row.tagsText = (row.tags || []).join(", ");
        state.knowledge.draft.achievements[idx] = row;
      } else {
        res = await api(`/api/achievements/${encodeURIComponent(a.id)}`, {
          method: "PATCH",
          body: JSON.stringify(body),
          ifMatch: state.knowledge.revisions.achievements,
        });
        Object.assign(a, res.achievement);
        a.tagsText = (a.tags || []).join(", ");
      }
      state.knowledge.revisions.achievements = res.revision;
      state.profileDirty = false;
      showToast("Saved");
    } catch (err) {
      if (isConflict(err)) await noteConflict(err, body);
      else alert(err.message);
      render();
    }
  }

  function splitTags(text) {
    return String(text || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  async function saveSkills() {
    const groups = state.knowledge.draft.skills.skill_groups || [];
    const body = { skill_groups: groups };
    try {
      const res = await api("/api/skills", {
        method: "PATCH",
        body: JSON.stringify(body),
        ifMatch: state.knowledge.revisions.skills,
      });
      state.knowledge.revisions.skills = res.revision;
      state.knowledge.draft.skills = clone(res.skills);
      state.profileDirty = false;
      showToast("Saved");
    } catch (err) {
      if (isConflict(err)) await noteConflict(err, body);
      else alert(err.message);
      render();
    }
  }

  async function saveProject(idx) {
    const p = state.knowledge.draft.projects[idx];
    if (!p) return;
    const body = { name: p.name, bullets: p.bullets || [] };
    if (p.note) body.note = p.note;
    try {
      let res;
      if (!p.id) {
        if (!body.name) {
          alert("Project needs a name.");
          return;
        }
        res = await api("/api/projects", {
          method: "POST",
          body: JSON.stringify(body),
          ifMatch: state.knowledge.revisions.projects,
        });
        state.knowledge.draft.projects[idx] = clone(res.project);
      } else {
        res = await api(`/api/projects/${encodeURIComponent(p.id)}`, {
          method: "PATCH",
          body: JSON.stringify(body),
          ifMatch: state.knowledge.revisions.projects,
        });
        Object.assign(p, res.project);
      }
      state.knowledge.revisions.projects = res.revision;
      state.profileDirty = false;
      showToast("Saved");
    } catch (err) {
      if (isConflict(err)) await noteConflict(err, body);
      else alert(err.message);
      render();
    }
  }

  async function saveCertifications() {
    const body = { certifications: state.knowledge.draft.certifications || [] };
    try {
      const res = await api("/api/certifications", {
        method: "PATCH",
        body: JSON.stringify(body),
        ifMatch: state.knowledge.revisions.projects,
      });
      state.knowledge.revisions.projects = res.revision;
      state.knowledge.draft.certifications = clone(res.certifications || []);
      state.profileDirty = false;
      showToast("Saved");
    } catch (err) {
      if (isConflict(err)) await noteConflict(err, body);
      else alert(err.message);
      render();
    }
  }

  async function saveCurrentProfileTab() {
    const tab = state.route.tab;
    if (tab === "profile") return saveProfile();
    if (tab === "positions") {
      for (let i = 0; i < (state.knowledge.draft.positions || []).length; i++) {
        await savePosition(i);
      }
      return;
    }
    if (tab === "achievements") {
      for (let i = 0; i < (state.knowledge.draft.achievements || []).length; i++) {
        await saveAchievement(i);
      }
      return;
    }
    if (tab === "skills") return saveSkills();
    if (tab === "projects") {
      for (let i = 0; i < (state.knowledge.draft.projects || []).length; i++) {
        await saveProject(i);
      }
      await saveCertifications();
    }
  }

  function emptyForPath(path) {
    if (path.endsWith(".languages")) return { name: "", level: "" };
    if (path.endsWith(".education")) return { degree: "", institution: "", year: "" };
    if (path === "certifications" || path.endsWith(".certifications")) return { name: "", year: "" };
    return "";
  }

  function addAtPath(path, value) {
    const parts = path.split(".");
    let cur = state.knowledge.draft;
    for (let i = 0; i < parts.length - 1; i++) {
      if (cur[parts[i]] == null) cur[parts[i]] = /^\d+$/.test(parts[i + 1]) ? [] : {};
      cur = cur[parts[i]];
    }
    const last = parts[parts.length - 1];
    if (!Array.isArray(cur[last])) cur[last] = [];
    cur[last].push(value);
  }

  function removeAtPath(path) {
    const parts = path.split(".");
    const idx = Number(parts.pop());
    let cur = state.knowledge.draft;
    for (const part of parts) cur = cur[part];
    if (Array.isArray(cur)) cur.splice(idx, 1);
  }

  function harnessLabel(name) {
    const row = HARNESS_CHIPS.find((h) => h[0] === name);
    return row ? row[1] : name || "Auto";
  }

  function modelPathOf(model) {
    const url = (model && model.base_url) || "";
    if (url.includes("api.x.ai")) return "spacexai";
    try {
      const host = new URL(url).hostname;
      if (host === "127.0.0.1" || host === "localhost" || host === "0.0.0.0") return "local";
    } catch {
      /* ignore */
    }
    return "custom";
  }

  function stashPendingKey() {
    const el = document.querySelector("input[name=api_key]");
    if (el) pendingKey = el.value;
  }

  function applyAgentPayload(payload) {
    state.agent = payload;
    state.agentDoctor = payload.doctor || null;
    const model = (payload && payload.model) || {};
    const path = modelPathOf(model);
    state.agentForm = {
      harness: payload.harness || "auto",
      path,
      base_url: model.base_url || SPACEXAI.base_url,
      api_key_env: model.api_key_env || (path === "local" ? "" : SPACEXAI.api_key_env),
      model: model.model || (path === "local" ? LOCAL_MODEL.model : SPACEXAI.model),
      errors: {},
    };
  }

  function autoHelp() {
    const detected = state.agent && state.agent.harness_detected;
    if (detected) return `Auto chose ${harnessLabel(detected)} (first installed).`;
    return "No harness found. Install OpenCode (curl -fsSL https://opencode.ai/install | bash), or pick Claude / Cursor / Codex if you already run them.";
  }

  function doctorState() {
    if (state.agentBusy) return "connecting";
    const doc = state.agentDoctor;
    if (!doc) return "disconnected";
    return doc.ok ? "connected" : "fail";
  }

  function AgentStatus() {
    const st = doctorState();
    const form = state.agentForm || {};
    const model = form.model || "";
    let host = "";
    try {
      host = new URL(form.base_url || "").hostname || "";
    } catch {
      host = form.base_url || "";
    }
    const harness = harnessLabel(form.harness === "auto" ? (state.agent && state.agent.harness_detected) || "auto" : form.harness);
    let title = "Not connected";
    let body = "Hunt does not run a chat. Pick a harness you already use, or paste a SpaceXAI key.";
    if (st === "connecting") {
      title = "Checking…";
      body = "Hunt does not run a chat. Connect a harness you already use, or paste a key.";
    } else if (st === "connected") {
      title = "Connected";
      body = `${harness} · ${model} · ${host}`;
    } else if (st === "fail") {
      title = "Doctor failed";
      const failed = ((state.agentDoctor && state.agentDoctor.checks) || []).find((c) => !c.ok);
      body = failed ? failed.label || failed.detail || "Doctor failed" : "Doctor failed";
    }
    return `<div data-primitive="AgentStatus" data-state="${esc(st)}">
      <h2>${esc(title)}</h2>
      <p>${esc(body)}</p>
    </div>`;
  }

  function DoctorList() {
    if (state.agentBusy) {
      return `<div data-primitive="DoctorList">${LoadingSkeleton(4)}</div>`;
    }
    const doc = state.agentDoctor;
    if (!doc) return "";
    const rows = (doc.checks || []).map((row) => {
      const ok = row.ok !== false;
      const note = row.severity === "note";
      const flag = note ? "note" : ok ? "ok" : "fail";
      return `<div class="doctor-row" data-ok="${ok}" data-severity="${esc(row.severity || "")}">
        <span class="doctor-flag">${esc(flag)}</span>
        <span>${esc(row.label || row.detail || row.id)}</span>
      </div>`;
    }).join("");
    return `<div data-primitive="DoctorList">${rows}</div>`;
  }

  function SecretField() {
    const form = state.agentForm || {};
    const env = form.api_key_env || "XAI_API_KEY";
    const saved = Boolean(state.agent && state.agent.api_key_set) && !state.agentReplaceKey;
    if (saved) {
      return `<div data-primitive="SecretField" data-set="true">
        <span class="secret-name">${esc(env)} saved</span>
        <button type="button" data-primitive="Btn" class="ghost" data-replace-key>Replace</button>
        <button type="button" data-primitive="Btn" class="ghost" data-remove-key>Remove key</button>
      </div>`;
    }
    const err = form.errors && form.errors.api_key;
    return `<div data-primitive="SecretField" data-set="false">
      ${FormField(
        "API key",
        `<input data-primitive="TextInput" name="api_key" type="password" autocomplete="off" placeholder="xai-…" value="${esc(pendingKey)}">`,
        { name: "api_key", err, help: state.agentReplaceKey ? "Paste a replacement key. Cancel to keep the saved one." : "" }
      )}
      ${state.agentReplaceKey ? Btn("Cancel", { variant: "ghost", attrs: "data-cancel-replace-key" }) : ""}
    </div>`;
  }

  function InstallPreview() {
    const writes = (state.agent && state.agent.preview && state.agent.preview.writes) || [];
    const harness = (state.agentForm && state.agentForm.harness) || "auto";
    if (harness === "auto") {
      return `<div data-primitive="InstallPreview"><p class="help">${esc("Auto selects a runner. Pick Claude, Cursor, Codex, or OpenCode to write files.")}</p></div>`;
    }
    if (!writes.length) {
      return `<div data-primitive="InstallPreview"><p class="help">${esc("No files to preview.")}</p></div>`;
    }
    const rows = writes.map((w) => {
      const present = w.exists ? "already there" : "will write";
      return `<li><code>${esc(w.path)}</code> <span class="preview-flag">${esc(present)}</span></li>`;
    }).join("");
    return `<div data-primitive="InstallPreview">
      <p class="help">Open this Hunt workspace in the harness, not the Hunt source repo.</p>
      <ul>${rows}</ul>
    </div>`;
  }

  function modelHelp(path) {
    if (path === "spacexai") return "Get a key at console.x.ai. Hunt does not sell a model.";
    if (path === "local") {
      const url = (state.agentForm && state.agentForm.base_url) || "http://127.0.0.1:8080/v1";
      return `Doctor will call GET ${url.replace(/\/$/, "")}/models. A small local model (Gemma E4B class) proves wiring. Screening quality may still want Grok or Claude.`;
    }
    return "Any OpenAI-compat /v1. Key stays in secrets.env under the env name you set.";
  }

  function renderAgentPage(root, badges) {
    const form = state.agentForm || {};
    const busy = state.agentBusy;
    const disabled = busy ? "disabled" : "";
    const st = doctorState();
    const harness = form.harness || "auto";
    const path = form.path || "spacexai";
    const harnessChips = HARNESS_CHIPS.map(([id, label]) =>
      `<button type="button" data-primitive="FilterChip" data-agent-harness="${id}" aria-pressed="${harness === id}" ${disabled}>${esc(label)}</button>`
    ).join("");
    const pathChips = MODEL_PATHS.map(([id, label]) =>
      `<button type="button" data-primitive="FilterChip" data-model-path="${id}" aria-pressed="${path === id}" ${disabled}>${esc(label)}</button>`
    ).join("");
    let harnessHelp = HARNESS_HELP[harness] || "";
    if (harness === "auto") harnessHelp = autoHelp();
    const installCli = harness === "auto"
      ? "hunt agent doctor --json"
      : `hunt agent install --harness ${harness} --json`;
    const banner = state.agentError
      ? ErrorBanner(state.agentError, "data-agent-retry")
      : "";
    const installDisabled = busy || harness === "auto" ? "disabled" : "";
    const localNote = path === "local"
      ? `<p class="help">Local model — wiring is enough. Screening quality may still want Grok or Claude.</p>`
      : "";
    const sticky = `<div class="sticky-save${state.agentDirty ? " is-dirty" : ""}">${Btn("Save", { variant: "primary", attrs: `data-save-agent ${busy ? "disabled" : ""}` })}</div>`;
    const checkLabel = busy ? "Checking…" : "Check";
    const body = `
      ${pageHeader(
        "Agent",
        `<p class="kicker">Hunt does not run a chat. Connect a harness you already use, or paste a key.</p>`,
        `${Btn(checkLabel, { variant: "primary", attrs: `data-agent-check ${disabled}` })} ${CommandHint("hunt agent doctor --json")}`,
        "agent-header"
      )}
      <div class="agent-page">
        <div class="agent-span">${AgentStatus()}</div>
        ${banner ? `<div class="agent-span">${banner}</div>` : ""}
        <div class="agent-span">
        <section data-primitive="AgentSection" class="section">
          <h2>Appearance</h2>
          <p class="help">This browser only. Hunt does not store theme in the workspace.</p>
          ${ThemePicker()}
        </section>
        </div>
        <section data-primitive="AgentSection" class="section">
          <h2>Harness</h2>
          <p class="help">${esc(harnessHelp)}</p>
          <div data-primitive="HarnessPicker">${harnessChips}</div>
          ${harness === "paperclip" ? `<p class="help">Paperclip is optional. Hunt users do not need it. Pick this only if you already run Paperclip.</p>` : ""}
          ${harness === "auto" ? `${CommandHint("curl -fsSL https://opencode.ai/install | bash")}` : ""}
          ${InstallPreview()}
          <div class="form-actions">
            ${Btn("Install", { variant: "primary", attrs: `data-agent-install ${installDisabled}` })}
            ${CommandHint(installCli)}
          </div>
        </section>
        <section data-primitive="AgentSection" class="section">
          <h2>Model</h2>
          <p class="help">${esc(modelHelp(path))}</p>
          <div data-primitive="HarnessPicker" class="model-paths">${pathChips}</div>
          <form id="agent-model-form" class="form-grid">
            ${FormField("Base URL", input("base_url", form.base_url, `placeholder="${path === "local" ? "http://127.0.0.1:8080/v1" : "https://api.x.ai/v1"}" data-agent-field="base_url" ${disabled}`), { name: "base_url", err: form.errors && form.errors.base_url })}
            ${FormField("Env name", input("api_key_env", form.api_key_env, `placeholder="${path === "local" ? "optional" : "XAI_API_KEY"}" data-agent-field="api_key_env" ${disabled}`), { name: "api_key_env", err: form.errors && form.errors.api_key_env, help: "Name stored in config.yaml, not the key." })}
            ${FormField("Model", input("model", form.model, `placeholder="model id the server lists" data-agent-field="model" ${disabled}`), { name: "model", err: form.errors && form.errors.model })}
            <div class="span-2">${SecretField()}</div>
          </form>
          ${localNote}
          <p class="faint no-sub">Hunt does not sell a model subscription. You bring a key you already pay for, or a local server.</p>
          <div class="form-actions">
            ${Btn("Save", { variant: "primary", attrs: `data-save-agent ${disabled}` })}
            ${CommandHint("hunt agent config --json")}
          </div>
        </section>
        <div class="agent-span">${DoctorList()}</div>
        ${st === "connected" ? `<div class="agent-span">${CommandHint("hunt agent run operator")}<span class="help"> Hunt does not start a chat in this browser.</span></div>` : ""}
      </div>
      ${sticky}`;
    root.innerHTML = shell("settings", badges, body);
  }

  async function loadAgent(harness) {
    const q = harness && harness !== "auto" ? `?harness=${encodeURIComponent(harness)}` : "";
    const payload = await api(`/api/agent${q}`);
    applyAgentPayload(payload);
    if (harness) state.agentForm.harness = harness;
    state.agentError = null;
  }

  async function loadPreview(harness) {
    const q = harness && harness !== "auto" ? `?harness=${encodeURIComponent(harness)}` : "";
    const payload = await api(`/api/agent${q}`);
    if (state.agent) {
      state.agent.preview = payload.preview;
      state.agent.harness_detected = payload.harness_detected;
    } else {
      applyAgentPayload(payload);
    }
  }

  function validateAgentForm() {
    const errors = {};
    const f = state.agentForm || {};
    const url = (f.base_url || "").trim();
    if (!url) errors.base_url = "Need a base URL.";
    else {
      try {
        const parsed = new URL(url);
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
          errors.base_url = "Need an http(s) URL.";
        } else if (!parsed.hostname) {
          errors.base_url = "Need an http(s) URL.";
        }
      } catch {
        errors.base_url = "Need an http(s) URL.";
      }
    }
    stashPendingKey();
    const key = pendingKey;
    if (key && /^https?:\/\//i.test(key.trim())) {
      errors.api_key = "That looks like a URL. Put it in Base URL.";
    }
    const needKey = f.path === "spacexai" || f.path === "custom";
    const haveSaved = Boolean(state.agent && state.agent.api_key_set) && !state.agentReplaceKey;
    if (needKey && !haveSaved && !key) {
      errors.api_key = "Paste a key, or switch to Local if the server needs none.";
    }
    if (!(f.model || "").trim()) {
      errors.model = "Need a model id (for SpaceXAI, grok-4.5).";
    }
    f.errors = errors;
    return errors;
  }

  async function saveAgent() {
    const errors = validateAgentForm();
    if (Object.keys(errors).length) {
      render();
      return;
    }
    const f = state.agentForm;
    state.agentBusy = true;
    state.agentError = null;
    render();
    try {
      stashPendingKey();
      const keyToSave = pendingKey;
      let saved = await api("/api/agent", {
        method: "PATCH",
        body: JSON.stringify({
          harness: f.harness,
          model: {
            base_url: f.base_url.trim(),
            api_key_env: (f.api_key_env || "").trim(),
            model: f.model.trim(),
          },
        }),
      });
      if (keyToSave) {
        const envName = (f.api_key_env || "").trim() || "XAI_API_KEY";
        saved = await api("/api/agent/secret", {
          method: "PUT",
          body: JSON.stringify({ env: envName, value: keyToSave }),
        });
        pendingKey = "";
        state.agentReplaceKey = false;
        showToast("Key saved");
      } else {
        showToast("Saved");
      }
      applyAgentPayload(saved);
      state.agentDirty = false;
    } catch (err) {
      state.agentError = err.message || "Cannot run doctor.";
    } finally {
      state.agentBusy = false;
      render();
    }
  }

  async function runAgentDoctor() {
    stashPendingKey();
    const draft = state.agentForm ? Object.assign({}, state.agentForm) : null;
    const dirty = state.agentDirty;
    const replacing = state.agentReplaceKey;
    state.agentBusy = true;
    state.agentError = null;
    render();
    try {
      const payload = await api("/api/agent/doctor", { method: "POST", body: "{}" });
      applyAgentPayload(payload);
      state.agentDoctor = payload.doctor || payload;
      if (draft) {
        state.agentForm = draft;
        state.agentDirty = dirty;
        state.agentReplaceKey = replacing;
      }
    } catch (err) {
      state.agentError = err.message === "Cannot reach Hunt HTTP." ? err.message : "Cannot run doctor.";
      if (draft) {
        state.agentForm = draft;
        state.agentDirty = dirty;
        state.agentReplaceKey = replacing;
      }
    } finally {
      state.agentBusy = false;
      render();
    }
  }

  async function installAgent() {
    const harness = state.agentForm && state.agentForm.harness;
    if (!harness || harness === "auto") return;
    state.agentBusy = true;
    state.agentError = null;
    state.dialog = null;
    render();
    try {
      const payload = await api("/api/agent/install", {
        method: "POST",
        body: JSON.stringify({ harness }),
      });
      applyAgentPayload(payload);
      showToast(`Installed for ${harnessLabel(harness)}`);
    } catch (err) {
      state.agentError = err.message || "Install failed.";
    } finally {
      state.agentBusy = false;
      render();
    }
  }

  async function renderSettings(root, badges) {
    try {
      if (!state.agentForm) {
        await loadAgent();
      }
    } catch (err) {
      root.innerHTML = shell("settings", badges, ErrorBanner(err.message));
      return;
    }
    renderAgentPage(root, badges);
  }

  async function badges() {
    const out = { board: 0, inbox: 0, sources: 0, jobs: 0 };
    try {
      const [inbox, jobs] = await Promise.all([
        api("/api/inbox?status=pending"),
        api("/api/jobs"),
      ]);
      out.inbox = (inbox.inbox || []).length;
      out.jobs = (jobs.jobs || []).filter((j) => j.state === "queued" || j.state === "running").length;
    } catch {
      /* badges are best-effort */
    }
    return out;
  }

  async function render() {
    stopLive();
    closeThemeMenu();
    closeRowMenu();
    const root = document.getElementById("app");
    const prev = state.route;
    state.route = parseRoute();
    if (state.route.name === "inbox") {
      state.inboxStatus = state.route.status === "dismissed" ? "dismissed" : "pending";
    }
    if (prev.name !== state.route.name || prev.id !== state.route.id) {
      state.quotedDirty = false;
    }
    if (prev.name === "profile" && state.route.name !== "profile") {
      state.profileDirty = false;
      state.knowledge = null;
      state.profileConflict = null;
    }
    if (prev.name === "settings" && state.route.name !== "settings") {
      state.agentDirty = false;
      state.agent = null;
      state.agentDoctor = null;
      state.agentForm = null;
      state.agentBusy = false;
      state.agentReplaceKey = false;
      state.agentError = null;
      pendingKey = "";
    }
    try {
      state.meta = await api("/api/meta");
    } catch (err) {
      if (err.offline) {
        root.innerHTML = ErrorBanner("Cannot reach Hunt HTTP.");
        return;
      }
      throw err;
    }
    if (state.meta.auth_required && !token()) {
      root.innerHTML = AuthGate();
      return;
    }
    const b = await badges().catch((err) => {
      if (err.status === 401) throw err;
      return { inbox: 0, jobs: 0 };
    });
    const r = state.route;
    try {
      if (r.name === "board") return await renderBoard(root, b);
      if (r.name === "new") return await renderNew(root, b);
      if (r.name === "detail") return await renderDetail(root, b, r.id);
      if (r.name === "inbox") return await renderInbox(root, b);
      if (r.name === "sources") return await renderSources(root, b);
      if (r.name === "jobs") return await renderJobs(root, b);
      if (r.name === "job") return await renderJob(root, b, r.id);
      if (r.name === "profile") return await renderProfile(root, b);
      if (r.name === "settings") return await renderSettings(root, b);
      root.innerHTML = shell(
        "board",
        b,
        EmptyState("Not found", "That path is not a Hunt surface.", Btn("Board", { href: "/" }))
      );
    } catch (err) {
      if (err.status === 401) {
        root.innerHTML = AuthGate("Invalid or missing token.");
        return;
      }
      root.innerHTML = ErrorBanner(err.message);
    }
  }

  function formData(form) {
    const fd = new FormData(form);
    const out = {};
    for (const [k, v] of fd.entries()) {
      out[k] = typeof v === "string" ? v : v;
    }
    return out;
  }

  function cleanFields(obj) {
    const out = {};
    for (const [k, v] of Object.entries(obj)) {
      if (v === "" || v == null) {
        if (["comp_amount", "comp_currency", "comp_unit", "office_days_per_week", "duration_months"].includes(k)) {
          out[k] = k.includes("amount") || k.includes("days") || k.includes("duration") ? null : "";
        } else if (k !== "company") {
          out[k] = k === "languages_required" ? [] : v === "" ? null : v;
        }
        continue;
      }
      if (k === "comp_amount" || k === "office_days_per_week") out[k] = Number(v);
      else if (k === "duration_months") out[k] = parseInt(v, 10);
      else if (k === "languages_required") out[k] = v;
      else out[k] = v;
    }
    return out;
  }

  document.addEventListener("click", async (ev) => {
    const themeToggle = ev.target.closest("[data-primitive=ThemeToggle]");
    if (themeToggle) {
      ev.preventDefault();
      openThemeMenu(themeToggle);
      return;
    }
    const rowMenuBtn = ev.target.closest("[data-primitive=IconBtn][data-row-menu]");
    if (rowMenuBtn) {
      ev.preventDefault();
      openRowMenu(rowMenuBtn);
      return;
    }
    const themeOption = ev.target.closest("[data-theme-option]");
    if (themeOption) {
      ev.preventDefault();
      setThemePref(themeOption.getAttribute("data-theme-option"));
      closeThemeMenu(true);
      return;
    }
    const themeChip = ev.target.closest("[data-theme-pref]");
    if (themeChip) {
      ev.preventDefault();
      setThemePref(themeChip.getAttribute("data-theme-pref"));
      return;
    }
    if (themeMenuOpen && !ev.target.closest("[data-primitive=ThemeMenu]")) {
      closeThemeMenu();
    }
    if (rowMenuOpen && !ev.target.closest("[data-primitive=RowMenu]")) {
      closeRowMenu();
    }
    const a = ev.target.closest("a[href], a[data-nav]");
    if (a) {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      const target = a.getAttribute("target");
      if (target && target !== "_self") return;
      if (a.hasAttribute("download")) return;
      const raw = a.getAttribute("data-nav") || a.getAttribute("href");
      if (!raw || raw.startsWith("mailto:") || raw.startsWith("javascript:")) return;
      let url;
      try {
        url = new URL(raw, location.href);
      } catch {
        return;
      }
      if (url.origin !== location.origin) return;
      ev.preventDefault();
      go(`${url.pathname}${url.search}${url.hash}`);
      return;
    }
    const copy = ev.target.closest("[data-copy]");
    if (copy) {
      ev.preventDefault();
      await copyText(copy.getAttribute("data-copy"));
      const prev = copy.textContent;
      copy.textContent = copy.dataset.primitive === "CommandHint" ? "Copied" : "Copied";
      setTimeout(() => {
        copy.textContent = prev;
      }, 1000);
      if (copy.closest("[data-primitive=RowMenu]")) closeRowMenu();
      return;
    }
    const row = ev.target.closest("[data-href]");
    if (row && !ev.target.closest("button, a, input, select")) {
      go(row.getAttribute("data-href"));
      return;
    }
    const retry = ev.target.closest("[data-retry]");
    if (retry) {
      render();
      return;
    }
    const chip = ev.target.closest("[data-filter]");
    if (chip) {
      const f = chip.getAttribute("data-filter");
      if (f === "closed") {
        if (state.boardFilters.has("rejected")) {
          state.boardFilters.delete("rejected");
          state.boardFilters.delete("withdrawn");
        } else {
          state.boardFilters.add("rejected");
          state.boardFilters.add("withdrawn");
        }
      } else if (f === "parked") {
        if (state.boardFilters.has("parked")) state.boardFilters.delete("parked");
        else state.boardFilters.add("parked");
      } else if (OPEN_STATUSES.includes(f)) {
        if (state.boardFilters.has(f)) state.boardFilters.delete(f);
        else state.boardFilters.add(f);
      }
      render();
      return;
    }
    if (ev.target.closest("[data-clear-filters]")) {
      if (state.route.name === "inbox") {
        state.inboxQuery = "";
        state.inboxSource = "";
        state.inboxKnockout = "";
        state.inboxTriage = "";
      } else {
        state.boardFilters = new Set(OPEN_STATUSES);
        state.boardQuery = "";
      }
      render();
      return;
    }
    const jobStateChip = ev.target.closest("[data-job-state]");
    if (jobStateChip) {
      const s = jobStateChip.getAttribute("data-job-state");
      if (state.jobStateFilters.has(s)) state.jobStateFilters.delete(s);
      else state.jobStateFilters.add(s);
      render();
      return;
    }
    const jobTypeChip = ev.target.closest("[data-job-type]");
    if (jobTypeChip) {
      const t = jobTypeChip.getAttribute("data-job-type");
      if (state.jobTypeFilters.has(t)) state.jobTypeFilters.delete(t);
      else state.jobTypeFilters.add(t);
      render();
      return;
    }
    const inboxKnockout = ev.target.closest("[data-inbox-knockout]");
    if (inboxKnockout) {
      const value = inboxKnockout.getAttribute("data-inbox-knockout") || "";
      state.inboxKnockout = state.inboxKnockout === value ? "" : value;
      render();
      return;
    }
    const inboxTriage = ev.target.closest("[data-inbox-triage]");
    if (inboxTriage) {
      const value = inboxTriage.getAttribute("data-inbox-triage") || "";
      state.inboxTriage = state.inboxTriage === value ? "" : value;
      render();
      return;
    }
    const inboxSort = ev.target.closest("[data-inbox-sort]");
    if (inboxSort) {
      const key = inboxSort.getAttribute("data-inbox-sort") || "";
      if (state.inboxSort === key) {
        state.inboxOrder = state.inboxOrder === "desc" ? "asc" : "desc";
      } else {
        state.inboxSort = key;
        state.inboxOrder = key === "created_at" || key === "net_month" ? "desc" : "asc";
      }
      render();
      return;
    }
    const inboxSource = ev.target.closest("[data-inbox-source]");
    if (inboxSource) {
      const value = inboxSource.getAttribute("data-inbox-source") || "";
      state.inboxSource = state.inboxSource === value ? "" : value;
      render();
      return;
    }
    if (ev.target.closest("[data-close-dialog]") && ev.target.hasAttribute("data-close-dialog")) {
      state.dialog = null;
      render();
      return;
    }
    const promote = ev.target.closest("[data-promote]");
    if (promote) {
      const id = promote.getAttribute("data-promote");
      const items = (await api("/api/inbox?status=pending")).inbox || [];
      const item = items.find((i) => i.id === id);
      if (item) {
        state.dialog = { kind: "promote", item };
        render();
      }
      return;
    }
    const confirmPromote = ev.target.closest("[data-confirm-promote]");
    if (confirmPromote) {
      const id = confirmPromote.getAttribute("data-confirm-promote");
      try {
        const res = await api(`/api/inbox/${encodeURIComponent(id)}/promote`, {
          method: "POST",
          body: "{}",
        });
        state.dialog = null;
        const appId = res.application.id;
        showToast(`Promoted → ${appId}`);
        go(`/applications/${appId}`);
      } catch (err) {
        state.dialog = null;
        alert(err.message);
        render();
      }
      return;
    }
    const dismiss = ev.target.closest("[data-dismiss]");
    if (dismiss) {
      const id = dismiss.getAttribute("data-dismiss");
      state.dialog = {
        kind: "confirm",
        title: "Dismiss listing",
        body: "Dismiss this listing? It will not become an application.",
        ok: "Dismiss",
        danger: true,
        action: `dismiss:${id}`,
      };
      render();
      return;
    }
    const restoreBtn = ev.target.closest("[data-restore]");
    if (restoreBtn) {
      const id = restoreBtn.getAttribute("data-restore");
      state.dialog = {
        kind: "confirm",
        title: "Restore listing",
        body: "Restore this listing to pending?",
        ok: "Restore",
        action: `restore:${id}`,
      };
      render();
      return;
    }
    const ok = ev.target.closest("[data-confirm-ok]");
    if (ok) {
      const action = ok.getAttribute("data-confirm-ok");
      state.dialog = null;
      if (action.startsWith("dismiss:")) {
        const id = action.slice(8);
        try {
          await api(`/api/inbox/${encodeURIComponent(id)}/dismiss`, { method: "POST" });
          showToast("Dismissed");
        } catch (err) {
          alert(err.message);
        }
        render();
      } else if (action.startsWith("restore:")) {
        const id = action.slice(8);
        try {
          await api(`/api/inbox/${encodeURIComponent(id)}/restore`, { method: "POST" });
          showToast("Restored");
        } catch (err) {
          alert(err.message);
        }
        render();
      } else if (action.startsWith("sent:")) {
        const id = action.slice(5);
        try {
          await api(`/api/applications/${encodeURIComponent(id)}`, {
            method: "PATCH",
            body: JSON.stringify({ status: "sent" }),
          });
        } catch (err) {
          alert(err.message);
        }
        render();
      } else if (action === "agent-install") {
        await installAgent();
      } else if (action.startsWith("agent-remove-key:")) {
        const env = action.slice("agent-remove-key:".length);
        state.agentBusy = true;
        render();
        try {
          const payload = await api(`/api/agent/secret?env=${encodeURIComponent(env)}`, {
            method: "DELETE",
          });
          pendingKey = "";
          state.agentReplaceKey = false;
          applyAgentPayload(payload);
          showToast("Key removed");
        } catch (err) {
          state.agentError = err.message;
        } finally {
          state.agentBusy = false;
          render();
        }
      }
      return;
    }
    const run = ev.target.closest("[data-run-source]");
    if (run) {
      const id = run.getAttribute("data-run-source");
      try {
        const res = await api(`/api/sources/${encodeURIComponent(id)}/run`, { method: "POST" });
        showToast(`Job ${res.job.id} queued`);
      } catch (err) {
        alert(err.message);
      }
      render();
      return;
    }
    const tailor = ev.target.closest("#enqueue-tailor");
    if (tailor) {
      const id = state.route.id;
      try {
        await api("/api/jobs", {
          method: "POST",
          body: JSON.stringify({ type: "tailor-cv", target_id: id, run: true }),
        });
        showToast("Tailoring CV");
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    const runJob = ev.target.closest("[data-run-job]");
    if (runJob) {
      const jobId = runJob.getAttribute("data-run-job");
      try {
        await api(`/api/jobs/${encodeURIComponent(jobId)}/run`, { method: "POST" });
        showToast("Running");
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    const reveal = ev.target.closest("[data-reveal-artifact]");
    if (reveal) {
      const artId = reveal.getAttribute("data-reveal-artifact");
      const appId = reveal.getAttribute("data-application") || state.route.id;
      try {
        await api(
          `/api/applications/${encodeURIComponent(appId)}/artifacts/${encodeURIComponent(artId)}/reveal`,
          { method: "POST" }
        );
        showToast("Opened the folder");
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    if (ev.target.closest("[data-submit-quoted]")) {
      const form = document.getElementById("quoted-form");
      if (form) form.requestSubmit();
    }
    if (ev.target.closest("[data-save-profile-tab]")) {
      await saveCurrentProfileTab();
      return;
    }
    if (ev.target.closest("[data-save-agent]")) {
      ev.preventDefault();
      await saveAgent();
      return;
    }
    if (ev.target.closest("[data-agent-check]")) {
      ev.preventDefault();
      await runAgentDoctor();
      return;
    }
    if (ev.target.closest("[data-agent-retry]")) {
      ev.preventDefault();
      await runAgentDoctor();
      return;
    }
    const harnessChip = ev.target.closest("[data-agent-harness]");
    if (harnessChip && !harnessChip.disabled) {
      const id = harnessChip.getAttribute("data-agent-harness");
      stashPendingKey();
      if (state.agentForm) state.agentForm.harness = id;
      state.agentDirty = true;
      try {
        await loadPreview(id);
      } catch (err) {
        state.agentError = err.message;
      }
      render();
      return;
    }
    const pathChip = ev.target.closest("[data-model-path]");
    if (pathChip && !pathChip.disabled && state.agentForm) {
      const id = pathChip.getAttribute("data-model-path");
      stashPendingKey();
      state.agentForm.path = id;
      if (id === "spacexai") {
        state.agentForm.base_url = SPACEXAI.base_url;
        state.agentForm.api_key_env = SPACEXAI.api_key_env;
        if (!state.agentForm.model || state.agentForm.model === LOCAL_MODEL.model) {
          state.agentForm.model = SPACEXAI.model;
        }
      } else if (id === "local") {
        state.agentForm.base_url = LOCAL_MODEL.base_url;
        state.agentForm.api_key_env = "";
        if (!state.agentForm.model || state.agentForm.model === SPACEXAI.model) {
          state.agentForm.model = LOCAL_MODEL.model;
        }
      }
      state.agentDirty = true;
      render();
      return;
    }
    if (ev.target.closest("[data-agent-install]")) {
      const harness = state.agentForm && state.agentForm.harness;
      if (!harness || harness === "auto") return;
      const writes = (state.agent && state.agent.preview && state.agent.preview.writes) || [];
      const items = writes.map((w) => `<li><code>${esc(w.path)}</code></li>`).join("");
      state.dialog = {
        kind: "confirm",
        title: `Write Hunt into ${harnessLabel(harness)}?`,
        html: `<p>Hunt will not start a chat in this browser.</p><ul class="install-confirm-paths">${items}</ul>`,
        ok: "Install",
        cancel: "Back",
        action: "agent-install",
      };
      render();
      return;
    }
    if (ev.target.closest("[data-replace-key]")) {
      state.agentReplaceKey = true;
      pendingKey = "";
      render();
      return;
    }
    if (ev.target.closest("[data-cancel-replace-key]")) {
      state.agentReplaceKey = false;
      pendingKey = "";
      render();
      return;
    }
    if (ev.target.closest("[data-remove-key]")) {
      const env = (state.agentForm && state.agentForm.api_key_env) || "XAI_API_KEY";
      state.dialog = {
        kind: "confirm",
        title: "Remove key",
        body: `Remove the saved key from secrets.env? Hunt will not be able to call the hosted model.`,
        ok: "Remove key",
        danger: true,
        action: `agent-remove-key:${env}`,
      };
      render();
      return;
    }
    if (ev.target.closest("[data-conflict-reload]")) {
      state.profileConflict = null;
      state.profileDirty = false;
      state.knowledge = null;
      render();
      return;
    }
    if (ev.target.closest("[data-conflict-diff]")) {
      if (state.profileConflict) state.profileConflict.showDiff = true;
      render();
      return;
    }
    if (ev.target.closest("[data-conflict-cancel]")) {
      state.profileConflict = null;
      render();
      return;
    }
    const addPath = ev.target.closest("[data-add-path]");
    if (addPath && state.knowledge) {
      const path = addPath.getAttribute("data-add-path");
      addAtPath(path, emptyForPath(path));
      state.profileDirty = true;
      render();
      return;
    }
    const removePath = ev.target.closest("[data-remove-path]");
    if (removePath && state.knowledge) {
      removeAtPath(removePath.getAttribute("data-remove-path"));
      state.profileDirty = true;
      render();
      return;
    }
    if (ev.target.closest("[data-add-position]") && state.knowledge) {
      state.knowledge.draft.positions = state.knowledge.draft.positions || [];
      state.knowledge.draft.positions.unshift({
        id: "",
        title: "",
        employer: "",
        location: "",
        start: "",
        end: "present",
        scope_facts: [],
        default_achievements: [],
        verified: false,
      });
      state.profileDirty = true;
      render();
      return;
    }
    if (ev.target.closest("[data-add-achievement-row]") && state.knowledge) {
      state.knowledge.draft.achievements = state.knowledge.draft.achievements || [];
      state.knowledge.draft.achievements.push({
        id: "",
        text: "",
        tags: [],
        tagsText: "",
        evidence: "",
        verified: false,
      });
      state.profileDirty = true;
      go("/profile?tab=achievements");
      return;
    }
    if (ev.target.closest("[data-add-project]") && state.knowledge) {
      state.knowledge.draft.projects = state.knowledge.draft.projects || [];
      state.knowledge.draft.projects.push({ id: "", name: "", bullets: [""], note: "" });
      state.profileDirty = true;
      render();
      return;
    }
    if (ev.target.closest("[data-add-skill-group]") && state.knowledge) {
      const skills = state.knowledge.draft.skills || (state.knowledge.draft.skills = {});
      skills.skill_groups = skills.skill_groups || [];
      skills.skill_groups.push({ name: "", skills: [] });
      state.profileDirty = true;
      render();
      return;
    }
    const addSkill = ev.target.closest("[data-add-skill]");
    if (addSkill && state.knowledge) {
      const gi = Number(addSkill.getAttribute("data-add-skill"));
      const groups = state.knowledge.draft.skills.skill_groups || [];
      groups[gi].skills = groups[gi].skills || [];
      groups[gi].skills.push({ name: "", level: "working", employer_scope: "" });
      state.profileDirty = true;
      render();
      return;
    }
    const savePos = ev.target.closest("[data-save-position]");
    if (savePos) {
      await savePosition(Number(savePos.getAttribute("data-save-position")));
      return;
    }
    const saveAch = ev.target.closest("[data-save-achievement]");
    if (saveAch) {
      await saveAchievement(Number(saveAch.getAttribute("data-save-achievement")));
      return;
    }
    const saveProj = ev.target.closest("[data-save-project]");
    if (saveProj) {
      await saveProject(Number(saveProj.getAttribute("data-save-project")));
      return;
    }
    if (ev.target.closest("[data-save-skills]")) {
      await saveSkills();
      return;
    }
    if (ev.target.closest("[data-save-certifications]")) {
      await saveCertifications();
      return;
    }
    const openConfirm = ev.target.closest("[data-open-confirm]");
    if (openConfirm) {
      const [kindTarget, id] = (openConfirm.getAttribute("data-open-confirm") || "").split(":");
      if (!id) return;
      state.dialog = { kind: "confirm-verify", kindTarget, id };
      render();
      return;
    }
    const confirmVerify = ev.target.closest("[data-confirm-verify]");
    if (confirmVerify) {
      const raw = confirmVerify.getAttribute("data-confirm-verify") || "";
      const splitAt = raw.indexOf(":");
      const kindTarget = raw.slice(0, splitAt);
      const id = raw.slice(splitAt + 1);
      state.dialog = null;
      const file = kindTarget === "position" ? "positions" : "achievements";
      try {
        const res = await api(`/api/${file}/${encodeURIComponent(id)}/confirm`, {
          method: "POST",
          ifMatch: state.knowledge.revisions[file],
        });
        state.knowledge.revisions[file] = res.revision;
        const key = kindTarget === "position" ? "position" : "achievement";
        const listKey = file;
        const row = res[key];
        const list = state.knowledge.draft[listKey] || [];
        const idx = list.findIndex((item) => item.id === id);
        if (idx >= 0) {
          if (kindTarget === "achievement") row.tagsText = (row.tags || []).join(", ");
          list[idx] = Object.assign(list[idx], row);
        }
        showToast("Confirmed");
      } catch (err) {
        if (isConflict(err)) await noteConflict(err, { id, confirm: true });
        else alert(err.message);
      }
      render();
      return;
    }
    const unverify = ev.target.closest("[data-unverify]");
    if (unverify) {
      const raw = unverify.getAttribute("data-unverify") || "";
      const [kindTarget, id] = raw.split(":");
      if (!id) return;
      const file = kindTarget === "position" ? "positions" : "achievements";
      try {
        const res = await api(`/api/${file}/${encodeURIComponent(id)}`, {
          method: "PATCH",
          body: JSON.stringify({ verified: false }),
          ifMatch: state.knowledge.revisions[file],
        });
        state.knowledge.revisions[file] = res.revision;
        const key = kindTarget === "position" ? "position" : "achievement";
        const list = state.knowledge.draft[file] || [];
        const idx = list.findIndex((item) => item.id === id);
        if (idx >= 0) Object.assign(list[idx], res[key]);
        showToast("Marked as draft");
      } catch (err) {
        if (isConflict(err)) await noteConflict(err, { id, verified: false });
        else alert(err.message);
      }
      render();
    }
  });

  document.addEventListener("submit", async (ev) => {
    const form = ev.target;
    if (form.id === "auth-form") {
      ev.preventDefault();
      state.token = form.token.value.trim();
      sessionStorage.setItem(TOKEN_KEY, state.token);
      render();
      return;
    }
    if (form.id === "create-form") {
      ev.preventDefault();
      const fields = cleanFields(formData(form));
      delete fields.listing_id;
      try {
        const res = await api("/api/applications", {
          method: "POST",
          body: JSON.stringify(fields),
        });
        go(`/applications/${res.application.id}`);
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    if (form.id === "quoted-form") {
      ev.preventDefault();
      const fields = cleanFields(formData(form));
      try {
        await api(`/api/applications/${encodeURIComponent(state.route.id)}`, {
          method: "PATCH",
          body: JSON.stringify(fields),
        });
        state.quotedDirty = false;
        showToast("Saved");
        render();
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    if (form.id === "artifact-form") {
      ev.preventDefault();
      const fd = new FormData(form);
      try {
        await api(`/api/applications/${encodeURIComponent(state.route.id)}/artifacts`, {
          method: "POST",
          body: fd,
        });
        showToast("Artifact added");
        render();
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    if (form.id === "enqueue-form") {
      ev.preventDefault();
      const fields = formData(form);
      const payload = { type: fields.type };
      if (fields.target_id) payload.target_id = fields.target_id;
      try {
        const res = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
        showToast(`Job ${res.job.id} queued`);
        render();
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    if (form.id === "profile-form") {
      ev.preventDefault();
      await saveProfile();
    }
    if (form.id === "agent-model-form") {
      ev.preventDefault();
      await saveAgent();
    }
  });

  document.addEventListener("change", (ev) => {
    if (ev.target.matches(".file-btn input[type=file]")) {
      const label = ev.target.closest(".file-btn");
      const input = ev.target;
      const name = input.files && input.files[0] ? input.files[0].name : "Choose file";
      label.textContent = name;
      label.appendChild(input);
      return;
    }
    if (ev.target.closest("#quoted-form")) {
      state.quotedDirty = true;
      const save = document.querySelector(".sticky-save");
      if (save) save.classList.add("is-dirty");
      const shellEl = document.querySelector("[data-primitive=AppShell]");
      if (shellEl) shellEl.classList.add("quoted-dirty");
    }
    if (ev.target.matches("[data-present-index]") && state.knowledge) {
      const idx = Number(ev.target.getAttribute("data-present-index"));
      const pos = state.knowledge.draft.positions[idx];
      if (pos) pos.end = ev.target.checked ? "present" : "";
      state.profileDirty = true;
      render();
      return;
    }
    const addAch = ev.target.closest("[data-add-achievement]");
    if (addAch && addAch.value && state.knowledge) {
      const idx = Number(addAch.getAttribute("data-add-achievement"));
      const pos = state.knowledge.draft.positions[idx];
      pos.default_achievements = pos.default_achievements || [];
      if (!pos.default_achievements.includes(addAch.value)) pos.default_achievements.push(addAch.value);
      state.profileDirty = true;
      render();
      return;
    }
    if (ev.target.hasAttribute("data-path") && state.knowledge) {
      setPath(state.knowledge.draft, ev.target.getAttribute("data-path"), ev.target.value);
      state.profileDirty = true;
      const save = document.querySelector(".sticky-save");
      if (save) save.classList.add("is-dirty");
      const shellEl = document.querySelector("[data-primitive=AppShell]");
      if (shellEl) shellEl.classList.add("profile-dirty");
    }
    if (ev.target.hasAttribute("data-agent-field") && state.agentForm) {
      const field = ev.target.getAttribute("data-agent-field");
      state.agentForm[field] = ev.target.value;
      state.agentDirty = true;
      const save = document.querySelector(".sticky-save");
      if (save) save.classList.add("is-dirty");
      const shellEl = document.querySelector("[data-primitive=AppShell]");
      if (shellEl) shellEl.classList.add("agent-dirty");
    }
    if (ev.target.id === "status-select") {
      const value = ev.target.value;
      const id = state.route.id;
      if (value === "sent") {
        ev.target.value = ev.target.getAttribute("data-prev") || ev.target.querySelector("option[selected]")?.value;
        state.dialog = {
          kind: "confirm",
          title: "Mark as sent",
          body: "Hunt does not submit applications. Confirm you already sent this to the employer outside Hunt.",
          ok: "I already sent it",
          action: `sent:${id}`,
        };
        render();
        return;
      }
      api(`/api/applications/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ status: value }),
      })
        .then(() => render())
        .catch((err) => alert(err.message));
    }
  });

  document.addEventListener("input", (ev) => {
    if (ev.target.id === "filter-search") {
      if (state.route.name === "inbox") {
        ev.target.classList.toggle("is-pending", ev.target.value !== state.inboxQuery);
      } else {
        state.boardQuery = ev.target.value;
      }
    }
    if (ev.target.closest("#quoted-form")) {
      state.quotedDirty = true;
      const save = document.querySelector(".sticky-save");
      if (save) save.classList.add("is-dirty");
      const shellEl = document.querySelector("[data-primitive=AppShell]");
      if (shellEl) shellEl.classList.add("quoted-dirty");
    }
    if (ev.target.hasAttribute("data-path") && state.knowledge) {
      setPath(state.knowledge.draft, ev.target.getAttribute("data-path"), ev.target.value);
      state.profileDirty = true;
      const save = document.querySelector(".sticky-save");
      if (save) save.classList.add("is-dirty");
      const shellEl = document.querySelector("[data-primitive=AppShell]");
      if (shellEl) shellEl.classList.add("profile-dirty");
    }
    if (ev.target.hasAttribute("data-agent-field") && state.agentForm) {
      const field = ev.target.getAttribute("data-agent-field");
      state.agentForm[field] = ev.target.value;
      state.agentDirty = true;
      const save = document.querySelector(".sticky-save");
      if (save) save.classList.add("is-dirty");
      const shellEl = document.querySelector("[data-primitive=AppShell]");
      if (shellEl) shellEl.classList.add("agent-dirty");
    }
    if (ev.target.name === "api_key") {
      pendingKey = ev.target.value;
      state.agentDirty = true;
      const save = document.querySelector(".sticky-save");
      if (save) save.classList.add("is-dirty");
      const shellEl = document.querySelector("[data-primitive=AppShell]");
      if (shellEl) shellEl.classList.add("agent-dirty");
    }
  });

  document.addEventListener("keydown", (ev) => {
    const rowMenu = ev.target.closest("[data-primitive=RowMenu]");
    if (rowMenu && !rowMenu.hidden) {
      const items = [...rowMenu.querySelectorAll("[role=menuitem]")];
      const current = ev.target.closest("[role=menuitem]");
      const idx = items.indexOf(current);
      if (ev.key === "Escape") {
        ev.preventDefault();
        closeRowMenu(true);
        return;
      }
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") {
        ev.preventDefault();
        const next = items[(Math.max(idx, 0) + 1) % items.length];
        if (next) next.focus();
        return;
      }
      if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") {
        ev.preventDefault();
        const prev = items[(idx <= 0 ? items.length : idx) - 1];
        if (prev) prev.focus();
        return;
      }
    }
    const themeMenu = ev.target.closest("[data-primitive=ThemeMenu]");
    if (themeMenu && !themeMenu.hidden) {
      const items = [...themeMenu.querySelectorAll("[data-theme-option]")];
      const current = ev.target.closest("[data-theme-option]");
      const idx = items.indexOf(current);
      if (ev.key === "Escape") {
        ev.preventDefault();
        closeThemeMenu(true);
        return;
      }
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") {
        ev.preventDefault();
        const next = items[(Math.max(idx, 0) + 1) % items.length];
        if (next) next.focus();
        return;
      }
      if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") {
        ev.preventDefault();
        const prev = items[(idx <= 0 ? items.length : idx) - 1];
        if (prev) prev.focus();
        return;
      }
      if (ev.key === "Home") {
        ev.preventDefault();
        if (items[0]) items[0].focus();
        return;
      }
      if (ev.key === "End") {
        ev.preventDefault();
        if (items.length) items[items.length - 1].focus();
        return;
      }
      if (ev.key === "Enter" || ev.key === " ") {
        if (current) {
          ev.preventDefault();
          setThemePref(current.getAttribute("data-theme-option"));
          closeThemeMenu(true);
        }
        return;
      }
    }
    if (ev.key === "Escape" && themeMenuOpen) {
      ev.preventDefault();
      closeThemeMenu(true);
      return;
    }
    if (ev.key === "Escape" && rowMenuOpen) {
      ev.preventDefault();
      closeRowMenu(true);
      return;
    }
    if (ev.key === "Escape" && state.dialog) {
      state.dialog = null;
      render();
      return;
    }
    const tag = ev.target.tagName;
    if (ev.key === "Enter" && ev.target.id === "filter-search") {
      ev.preventDefault();
      if (state.route.name === "inbox") state.inboxQuery = ev.target.value;
      else state.boardQuery = ev.target.value;
      render().then(restoreFilterSearchCaret);
      return;
    }
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      if (ev.key === "Escape" && ev.target.id === "filter-search") {
        ev.preventDefault();
        ev.target.value = "";
        if (state.route.name === "inbox") state.inboxQuery = "";
        else state.boardQuery = "";
        render().then(restoreFilterSearchCaret);
        return;
      }
      if (ev.key === "/" && ev.target.id !== "filter-search") return;
      if (ev.key !== "Escape") return;
    }
    if (ev.key === "/" && tag !== "INPUT") {
      ev.preventDefault();
      const f = document.getElementById("filter-search");
      if (f) f.focus();
      return;
    }
    if (ev.key === "g") {
      state.gPending = true;
      return;
    }
    if (state.gPending) {
      state.gPending = false;
      if (ev.key === "b") go("/");
      if (ev.key === "i") go("/inbox");
      if (ev.key === "s") go("/sources");
      if (ev.key === "j") go("/jobs");
      if (ev.key === "p") go("/profile");
      if (ev.key === "a") go("/settings");
      return;
    }
    if (ev.key === "n" && state.route.name === "board") {
      go("/applications/new");
      return;
    }
    if (ev.key === "Enter") {
      const row = document.activeElement?.closest("[data-href]");
      if (row) {
        go(row.getAttribute("data-href"));
        return;
      }
    }
    if ((ev.key === "Enter" || ev.key === " ") && ev.target.closest("[data-inbox-sort]")) {
      ev.preventDefault();
      ev.target.closest("[data-inbox-sort]").click();
    }
  });

  function restoreFilterSearchCaret() {
    const f = document.getElementById("filter-search");
    if (!f) return;
    f.focus();
    const n = f.value.length;
    try {
      f.setSelectionRange(n, n);
    } catch {
      /* type=search may reject setSelectionRange */
    }
  }

  document.addEventListener("change", (ev) => {
    const orderSel = ev.target.closest("[data-inbox-order]");
    if (orderSel && orderSel.tagName === "SELECT") {
      const [key, dir] = (orderSel.value || "created_at:desc").split(":");
      state.inboxSort = key;
      state.inboxOrder = dir === "asc" ? "asc" : "desc";
      render();
    }
  });

  window.addEventListener("popstate", render);
  window.addEventListener("focus", () => {
    if (state.route.name) render();
  });

  applyTheme(themePref);
  const schemeMq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  if (schemeMq) {
    const onScheme = () => {
      if (themePref === "system") {
        applyTheme(themePref);
        syncThemeControls();
      }
    };
    if (schemeMq.addEventListener) schemeMq.addEventListener("change", onScheme);
    else if (schemeMq.addListener) schemeMq.addListener(onScheme);
  }
  window.addEventListener("storage", (ev) => {
    if (ev.key !== THEME_KEY) return;
    themePref = readThemePref();
    applyTheme(themePref);
    syncThemeControls();
  });

  render();
})();
