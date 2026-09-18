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
  const JOB_TYPES = ["source-poll", "screen-inbox", "tailor-cv"];
  const JOB_STATES = ["queued", "running", "done", "failed"];
  const TOKEN_KEY = "hunt_token";
  const BOARD_COLS = ["Company", "Title", "Status", "Modality", "Location", "Pay", "Updated", "Id"];
  const INBOX_COLS = ["Company", "Role", "Location", "Engagement", "Net /mo", "Why keep", "Why risk", "Age", "Actions", "Id"];
  const SOURCE_COLS = ["Name", "Adapter", "Enabled", "Last run", "Last error", "Listings", "Inbox", "", "Id"];
  const JOB_COLS = ["Id", "Type", "Target", "State", "Created", "Started", "Finished", "Error"];
  const PROFILE_TABS = [
    ["profile", "Profile"],
    ["positions", "Positions"],
    ["achievements", "Achievements"],
    ["skills", "Skills"],
    ["projects", "Projects"],
    ["integrity", "Integrity"],
  ];
  const SKILL_LEVELS = ["production", "working", "limited", "homelab"];

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
    jobStateFilters: new Set(JOB_STATES),
    jobTypeFilters: new Set(JOB_TYPES),
    quotedDirty: false,
    profileDirty: false,
    knowledge: null,
    profileConflict: null,
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
    if (replace) history.replaceState({}, "", href);
    else history.pushState({}, "", href);
    state.dialog = null;
    state.quotedDirty = false;
    render();
  }

  function parseRoute() {
    const path = location.pathname.replace(/\/+$/, "") || "/";
    const params = new URLSearchParams(location.search);
    if (path === "/") return { name: "board" };
    if (path === "/inbox") return { name: "inbox" };
    if (path === "/sources") return { name: "sources" };
    if (path === "/jobs") return { name: "jobs" };
    if (path === "/applications/new") return { name: "new" };
    if (path === "/profile") {
      const allowed = PROFILE_TABS.map((t) => t[0]);
      const tab = params.get("tab") || "positions";
      return { name: "profile", tab: allowed.includes(tab) ? tab : "positions" };
    }
    const m = path.match(/^\/applications\/([^/]+)$/);
    if (m) return { name: "detail", id: decodeURIComponent(m[1]) };
    return { name: "notfound" };
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
    return app.title_ours || app.title_posted || "";
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
    return `<a data-primitive="NavItem" href="${href}" ${cur}>${esc(label)}${CountBadge(badge)}</a>`;
  }

  function CopyId(id) {
    if (!id) return "";
    const short = id.length > 10 ? id.slice(0, 8) + "…" : id;
    return `<button type="button" data-primitive="CopyId" data-copy="${esc(id)}" title="${esc(id)}">${esc(short)}</button>`;
  }

  function CommandHint(cmd) {
    return `<button type="button" data-primitive="CommandHint" data-copy="${esc(cmd)}" title="${esc(cmd)}">CLI</button>`;
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

  function FormField(label, control, { help, span2, name } = {}) {
    return `<div data-primitive="FormField" class="${span2 ? "span-2" : ""}" data-name="${esc(name || "")}">
      <label class="label">${esc(label)}</label>
      ${control}
      ${help ? `<div class="help">${esc(help)}</div>` : ""}
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
    const chip = `<a data-primitive="WorkspaceChip" href="/profile" ${chipCurrent}>${esc(ws.workspace || "workspace")}<span class="profile"> · ${esc(ws.profile_name || "")}</span></a>`;
    return {
      bar: `<header data-primitive="AppBar">
        <a class="wordmark" href="/">Hunt</a>
        <nav class="appbar-nav appbar-nav-desktop">${items}</nav>
        ${chip}
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
    return `<div data-primitive="AppShell" class="${(dirty + pdirty).trim()}">${n.bar}<main class="page">${body}</main>${n.tabs}${toastHtml()}${dialogHtml()}</div>`;
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
      return `<div class="scrim" data-close-dialog>
        <div data-primitive="ConfirmDialog" role="dialog" aria-modal="true">
          <h2>${esc(d.title)}</h2>
          <div class="dialog-body">${esc(d.body)}</div>
          <div class="dialog-actions">
            ${Btn("Cancel", { variant: "ghost", attrs: "data-close-dialog" })}
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

  function pageHeader(title, extra, actions) {
    return `<div data-primitive="PageHeader"><h1>${title}</h1>${extra || ""}<div class="header-actions">${actions || ""}</div></div>`;
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
      return `<button type="button" data-primitive="FilterChip" data-job-type="${t}" aria-pressed="${on}">${t}</button>`;
    }).join("");
    return `<div data-primitive="FilterBar">${states}${types}</div>`;
  }

  function appRowCells(app) {
    const q = quotedOf(app);
    const d = app.comp_derived;
    const pay = q
      ? `<div class="pay-cell">${PayQuoted(q)}${NetEstimate(d)}</div>`
      : PayQuoted(null);
    return {
      company: `<strong>${esc(app.company)}</strong>`,
      title: `<span class="muted">${esc(titleOf(app))}</span>`,
      status: StatusPill(app.status),
      modality: esc(app.modality || ""),
      location: esc(locationOf(app)),
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
        ${FormField("Modality", select("modality", "", ["", "remote", "hybrid", "onsite"]))}
        ${FormField("Engagement", select("engagement", "", ["", "b2b", "fte", "uop", "unknown"]))}
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
    const q = quotedOf(app) || {};
    const activeTailor = jobs.some((j) => j.type === "tailor-cv" && (j.state === "queued" || j.state === "running"));
    const statusOpts = STATUSES.map((s) => `<option value="${s}" ${s === app.status ? "selected" : ""}>${s}</option>`).join("");
    const header =
      `<div data-primitive="PageHeader" class="detail-header">
        <div class="detail-header-titles">
          <h1 class="company-title">${esc(app.company)}</h1>
          <p class="role-title">${esc(titleOf(app))}</p>
        </div>
        <div class="detail-header-actions">
          <select data-primitive="StatusSelect" id="status-select">${statusOpts}</select>
          ${CopyId(app.id)}
          ${CommandHint(`hunt applications update ${app.id} --status ${app.status} --json`)}
        </div>
      </div>`;
    const quoted = quotedOf(app);
    const paySummary = `<div class="section pay-summary">
      ${PayQuoted(quoted)}
      ${quoted ? NetEstimate(app.comp_derived, { detail: true, taxHome: app.tax_home_for_net || "" }) : ""}
      ${PayDerived(app.comp_derived)}
    </div>`;
    const quotedForm = `<form id="quoted-form" class="section" data-primitive="QuotedForm">
      <h2>Quoted fields</h2>
      <div class="form-grid">
        ${FormField("Company", input("company", app.company))}
        ${FormField("Source", input("source", app.source))}
        ${FormField("URL", input("url", app.url), { span2: true })}
        ${FormField("Posted title", input("title_posted", app.title_posted))}
        ${FormField("Our title", input("title_ours", app.title_ours))}
        ${FormField("Country", input("location_country", app.location_country))}
        ${FormField("City", input("location_city", app.location_city))}
        ${FormField("Modality", select("modality", app.modality || "", ["", "remote", "hybrid", "onsite"]))}
        ${FormField("Office days", input("office_days_per_week", app.office_days_per_week, "type=number step=any"))}
        ${FormField("Engagement", select("engagement", app.engagement || "", ["", "b2b", "fte", "uop", "unknown"]))}
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
    </form>`;
    const knocks = (app.knockouts || []).map((k) => `<span class="knockout">${esc(k)}</span>`).join("") || `<span class="muted">None</span>`;
    const urlLine = app.url
      ? `<p><a href="${esc(app.url)}" target="_blank" rel="noopener">${esc(app.url)}</a></p>`
      : "";
    const arts = artifacts.artifacts || [];
    const artHtml = artifacts.error
      ? ErrorBanner(artifacts.error)
      : arts.length
        ? arts
            .map(
              (a) => `<div class="artifact" data-primitive="ArtifactList">
                <span>${esc(a.kind)}</span>
                <a href="/api/applications/${esc(app.id)}/artifacts/${esc(a.id)}/file">${esc(a.filename)}</a>
                <span class="sha">${esc((a.sha256 || "").slice(0, 8))}</span>
              </div>`
            )
            .join("")
        : `<p class="muted">No files yet. Add a JD or enqueue tailor-cv.</p>`;
    const evs = (events.events || []).slice().reverse();
    const evHtml = events.error
      ? ErrorBanner(events.error)
      : evs.length
        ? `<table data-primitive="EventLog"><thead><tr><th>Time</th><th>Actor</th><th>Verb</th><th>Detail</th></tr></thead><tbody>${evs
            .map(
              (e) => `<tr><td title="${esc(e.at)}">${esc(relative(e.at))}</td><td>${esc(e.actor || "cli")}</td><td>${esc(e.kind)}</td><td class="mono">${esc(e.body || "")}</td></tr>`
            )
            .join("")}</tbody></table>`
        : `<p class="muted">No events.</p>`;
    const jobRows = jobs
      .map(
        (j) => `<div data-primitive="JobRow">${JobStatePill(j.state)} ${esc(j.type)} ${CopyId(j.id)} <span class="faint">${esc(j.error || "")}</span></div>`
      )
      .join("");
    const right = `
      <section class="section"><h2>Artifacts</h2>
        <div data-primitive="ArtifactList">${artHtml}</div>
        <form id="artifact-form" class="artifact-form">
          ${FormField("File", `<label data-primitive="Btn" class="file-btn">Choose file<input type="file" name="file" required></label>`)}
          ${FormField("Kind", select("kind", "jd", ["jd", "cv", "notes", "other"]))}
          ${Btn("Add file", { type: "submit" })}
        </form>
      </section>
      <section class="section"><h2>Jobs</h2>
        ${jobRows || `<p class="muted">No jobs for this application.</p>`}
        ${Btn("Enqueue tailor-cv", { attrs: `id="enqueue-tailor" ${activeTailor ? "disabled" : ""}` })}
        ${CommandHint(`hunt jobs enqueue --type tailor-cv --target ${app.id} --json`)}
      </section>
      <section class="section"><h2>Events</h2>${evHtml}</section>
    `;
    const body = `${header}<div class="detail">
      <div class="detail-left">${paySummary}${quotedForm}<div class="section"><h2>Knockouts</h2>${knocks}${urlLine}</div></div>
      <div class="detail-right">${right}</div>
    </div>
    <div class="sticky-save${state.quotedDirty ? " is-dirty" : ""}">${Btn("Save quoted", { variant: "primary", attrs: "data-submit-quoted" })}</div>`;
    root.innerHTML = shell("board", badges, body);
  }

  async function renderInbox(root, badges) {
    root.innerHTML = shell(
      "inbox",
      badges,
      pageHeader("Inbox", "", "") + LoadingSkeleton(8, INBOX_COLS)
    );
    let data;
    try {
      data = await api(`/api/inbox?status=${encodeURIComponent(state.inboxStatus)}`);
    } catch (err) {
      root.innerHTML = shell("inbox", badges, ErrorBanner(err.message));
      return;
    }
    const items = data.inbox || [];
    const actions = CommandHint("hunt inbox list --json");
    if (!items.length) {
      root.innerHTML = shell(
        "inbox",
        badges,
        pageHeader("Inbox", `<span class="page-count">0</span>`, actions) +
          EmptyState(
            "Inbox is clear",
            "No screened listings. Run a source or enqueue screen-inbox.",
            Btn("Open sources", { href: "/sources" })
          )
      );
      return;
    }
    const rows = items
      .map((it) => {
        const q = quotedOf(it);
        const d = it.comp_derived;
        const pay = q
          ? `<div class="pay-cell">${PayQuoted(q)}${NetEstimate(d)}</div>`
          : NetEstimate(d);
        const role = it.role || it.title || "";
        return `<tr data-primitive="InboxRow" data-id="${esc(it.id)}">
          <td>${esc(it.company)}</td>
          <td>${esc(role)}</td>
          <td>${esc(locationOf(it))}</td>
          <td>${esc(it.engagement || "")}</td>
          <td>${pay}</td>
          <td>${esc(it.why_keep || "")}</td>
          <td>${esc(it.why_risk || "")}</td>
          <td title="${esc(it.created_at)}">${esc(relative(it.created_at))}</td>
          <td>
            <div class="inbox-actions">
            ${Btn("Promote", { variant: "primary", attrs: `data-promote="${esc(it.id)}"` })}
            ${Btn("Dismiss", { variant: "danger", attrs: `data-dismiss="${esc(it.id)}"` })}
            ${CommandHint(`hunt inbox promote ${it.id} --json`)}
            </div>
          </td>
          <td>${CopyId(it.id)}</td>
        </tr>`;
      })
      .join("");
    const cards = items
      .map((it) => {
        const q = quotedOf(it);
        const d = it.comp_derived;
        const pay = q
          ? `<div class="pay-cell">${PayQuoted(q)}${NetEstimate(d)}</div>`
          : NetEstimate(d);
        const role = it.role || it.title || "";
        const meta = [locationOf(it), it.engagement].filter(Boolean).join(" · ");
        return `<article data-primitive="InboxRow" class="inbox-card">
          <div class="row-line1"><strong>${esc(it.company)}</strong><span class="card-meta">${CopyId(it.id)}${CommandHint(`hunt inbox promote ${it.id} --json`)}</span></div>
          <div>${esc(role)}</div>
          <div class="muted">${esc(meta)}</div>
          <div>${pay}</div>
          <div class="muted">${esc(it.why_keep || it.why_risk || "")}</div>
          <div class="row-actions">
            ${Btn("Promote", { variant: "primary", attrs: `data-promote="${esc(it.id)}"` })}
            ${Btn("Dismiss", { variant: "danger", attrs: `data-dismiss="${esc(it.id)}"` })}
          </div>
        </article>`;
      })
      .join("");
    root.innerHTML = shell(
      "inbox",
      badges,
      pageHeader("Inbox", `<span class="page-count">${items.length}</span>`, actions) +
        `<table data-primitive="DataTable" class="inbox-table">
          <thead><tr><th>Company</th><th>Role</th><th>Location</th><th>Engagement</th><th>Net /mo</th><th>Why keep</th><th>Why risk</th><th>Age</th><th>Actions</th><th>Id</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="inbox-cards">${cards}</div>`
    );
  }

  async function renderSources(root, badges) {
    root.innerHTML = shell("sources", badges, pageHeader("Sources", "", "") + LoadingSkeleton(4, SOURCE_COLS));
    let data;
    try {
      data = await api("/api/sources");
    } catch (err) {
      root.innerHTML = shell("sources", badges, ErrorBanner(err.message));
      return;
    }
    const sources = data.sources || [];
    if (!sources.length) {
      root.innerHTML = shell(
        "sources",
        badges,
        pageHeader("Sources", "", CommandHint("hunt sources list --json")) +
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
          <td>${esc(s.name)}</td>
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
          ${s.last_error ? `<div class="danger-text">${esc(s.last_error)}</div>` : ""}
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
        `<table data-primitive="DataTable" class="sources-table">
          <thead><tr><th>Name</th><th>Adapter</th><th>Enabled</th><th>Last run</th><th>Last error</th><th>Listings</th><th>Inbox</th><th></th><th>Id</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="sources-cards">${cards}</div>`
    );
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
    const enqueue = `<form id="enqueue-form" class="enqueue-form">
      ${select("type", "screen-inbox", JOB_TYPES)}
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
            "Run a source, screen the inbox, or enqueue tailor-cv from an application.",
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
        (j) => `<tr data-primitive="JobRow">
          <td>${CopyId(j.id)}</td>
          <td>${esc(j.type)}</td>
          <td>${esc(j.target_id || "—")}</td>
          <td>${JobStatePill(j.state)}</td>
          <td title="${esc(j.created_at)}">${esc(relative(j.created_at))}</td>
          <td>${esc(j.started_at ? relative(j.started_at) : "—")}</td>
          <td>${esc(j.finished_at ? relative(j.finished_at) : "—")}</td>
          <td class="danger-text">${esc(j.error || "")}</td>
        </tr>`
      )
      .join("");
    const cards = jobs
      .map(
        (j) => `<article data-primitive="Row" class="job-card">
          <div class="row-line1"><strong>${esc(j.type)}</strong>${JobStatePill(j.state)}</div>
          <div class="muted">${esc(j.target_id || "—")}</div>
          <div>${CopyId(j.id)}</div>
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
          <thead><tr><th>Id</th><th>Type</th><th>Target</th><th>State</th><th>Created</th><th>Started</th><th>Finished</th><th>Error</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="jobs-cards">${cards}</div>`
    );
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
    const root = document.getElementById("app");
    const prev = state.route;
    state.route = parseRoute();
    if (prev.name !== state.route.name || prev.id !== state.route.id) {
      state.quotedDirty = false;
    }
    if (prev.name === "profile" && state.route.name !== "profile") {
      state.profileDirty = false;
      state.knowledge = null;
      state.profileConflict = null;
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
      if (r.name === "profile") return await renderProfile(root, b);
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
    const a = ev.target.closest("a[href]");
    if (a && a.origin === location.origin && !a.target) {
      ev.preventDefault();
      go(a.getAttribute("href"));
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
      state.boardFilters = new Set(OPEN_STATUSES);
      state.boardQuery = "";
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
        const res = await api("/api/jobs", {
          method: "POST",
          body: JSON.stringify({ type: "tailor-cv", target_id: id }),
        });
        showToast(`Job ${res.job.id} queued`);
      } catch (err) {
        alert(err.message);
      }
      render();
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
      state.boardQuery = ev.target.value;
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
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && state.dialog) {
      state.dialog = null;
      render();
      return;
    }
    const tag = ev.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      if (ev.key === "/" && ev.target.id !== "filter-search") return;
      if (ev.key !== "Escape") return;
    }
    if (ev.key === "/" && tag !== "INPUT") {
      ev.preventDefault();
      const f = document.getElementById("filter-search");
      if (f) f.focus();
      return;
    }
    if (ev.key === "Enter" && ev.target.id === "filter-search") {
      ev.preventDefault();
      state.boardQuery = ev.target.value;
      render();
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
      return;
    }
    if (ev.key === "n" && state.route.name === "board") {
      go("/applications/new");
      return;
    }
    if (ev.key === "Enter") {
      const row = document.activeElement?.closest("[data-href]");
      if (row) go(row.getAttribute("data-href"));
    }
  });

  window.addEventListener("popstate", render);
  window.addEventListener("focus", () => {
    if (state.route.name) render();
  });

  render();
})();
