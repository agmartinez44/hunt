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
  const TOKEN_KEY = "hunt_token";

  const state = {
    meta: null,
    route: { name: "board", id: null },
    token: sessionStorage.getItem(TOKEN_KEY) || "",
    toast: null,
    dialog: null,
    gPending: false,
    boardFilters: new Set(OPEN_STATUSES),
    boardQuery: "",
    inboxStatus: "pending",
    jobState: "",
    jobType: "",
    quotedDirty: false,
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
    render();
  }

  function parseRoute() {
    const path = location.pathname.replace(/\/+$/, "") || "/";
    if (path === "/") return { name: "board" };
    if (path === "/inbox") return { name: "inbox" };
    if (path === "/sources") return { name: "sources" };
    if (path === "/jobs") return { name: "jobs" };
    if (path === "/applications/new") return { name: "new" };
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
    return [app.location_city, app.location_country].filter(Boolean).join(", ");
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

  function JobStatePill(st) {
    return `<span data-primitive="JobStatePill" class="job-${esc(st)}">${esc(st)}</span>`;
  }

  function PayQuoted(q) {
    if (!q || q.amount == null) {
      return `<span data-primitive="PayUnknown"><span class="caption">Pay</span>Pay unknown</span>`;
    }
    return `<span data-primitive="PayQuoted"><span class="caption">Quoted</span><strong>${esc(money(q.amount))} ${esc(q.currency)} / ${esc(q.unit)}</strong></span>`;
  }

  function PayDerived(d) {
    if (!d || !d.fx_as_of) {
      return `<div data-primitive="PayDerived"><span class="caption">Derived unavailable</span>No FX stamp — conversions hidden.</div>`;
    }
    return `<div data-primitive="PayDerived"><span class="caption">Derived · FX ${esc(d.fx_as_of)} · ${esc(d.display_currency)}</span>
      ${esc(money(d.hour))} /h · ${esc(money(d.day))} /d · ${esc(money(d.month))} /mo · ${esc(money(d.year))} /yr
      ${d.net_month != null ? ` · net ${esc(money(d.net_month))} /mo` : ""}</div>`;
  }

  function FloorBadge(derived) {
    if (!derived || derived.clears_floor == null) {
      return `<span data-primitive="FloorBadge" class="floor-unknown">pay unknown</span>`;
    }
    if (derived.clears_floor) {
      return `<span data-primitive="FloorBadge" class="floor-clears">clears</span>`;
    }
    return `<span data-primitive="FloorBadge" class="floor-below">below floor</span>`;
  }

  function EmptyState(title, body, actionsHtml = "") {
    return `<div data-primitive="EmptyState"><h2>${esc(title)}</h2><p>${esc(body)}</p><div class="empty-actions">${actionsHtml}</div></div>`;
  }

  function ErrorBanner(message, retryAttr = "data-retry") {
    return `<div data-primitive="ErrorBanner"><span class="msg">${esc(message)}</span>${Btn("Retry", { attrs: retryAttr })}</div>`;
  }

  function LoadingSkeleton(n = 8) {
    const rows = Array.from({ length: n }, () => `<div data-primitive="LoadingSkeleton"></div>`).join("");
    return `<div class="skel-table">${rows}</div>`;
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
  function select(name, value, options) {
    const opts = options
      .map((o) => {
        const v = typeof o === "string" ? o : o.value;
        const l = typeof o === "string" ? o : o.label;
        return `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(l)}</option>`;
      })
      .join("");
    return `<select data-primitive="Select" name="${esc(name)}">${opts}</select>`;
  }
  function textarea(name, value) {
    return `<textarea data-primitive="Textarea" name="${esc(name)}">${esc(value ?? "")}</textarea>`;
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
    const chip = `<span data-primitive="WorkspaceChip">${esc(ws.workspace || "workspace")}<span class="profile"> · ${esc(ws.profile_name || "")}</span></span>`;
    return `
      <header data-primitive="AppBar">
        <a class="wordmark" href="/">Hunt</a>
        <nav class="appbar-nav appbar-nav-desktop">${items}</nav>
        ${chip}
      </header>
      <nav data-primitive="AppTabBar"><div class="appbar-nav">${items}</div></nav>
    `;
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
        <div style="margin-top:12px">${Btn("Continue", { variant: "primary", type: "submit" })}</div>
      </form>
    </div>`;
  }

  function shell(current, badges, body) {
    return `<div data-primitive="AppShell">${nav(current, badges)}<main class="page">${body}</main>${toastHtml()}${dialogHtml()}</div>`;
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

  function appRowCells(app) {
    const q = quotedOf(app);
    const d = app.comp_derived;
    const pay = q
      ? `<div class="pay-cell">${PayQuoted(q)}${d && d.fx_as_of ? `<div class="faint">${esc(money(d.month))} ${esc(d.display_currency)} /mo</div>` : ""}</div>`
      : PayQuoted(null);
    return {
      company: esc(app.company),
      title: esc(titleOf(app)),
      status: StatusPill(app.status),
      modality: esc(app.modality || ""),
      location: esc(locationOf(app)),
      pay,
      floor: FloorBadge(d),
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
        LoadingSkeleton()
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
            <td>${c.location}</td><td>${c.pay}</td><td>${c.floor}</td><td>${c.updated}</td><td>${c.id}</td>
          </tr>`;
        })
        .join("");
      const cards = filtered
        .map((a) => {
          const c = appRowCells(a);
          return `<article data-primitive="Row" class="board-card" data-href="/applications/${esc(a.id)}">
            <div class="row-line1"><strong>${c.company}</strong>${c.status}</div>
            <div>${c.title}</div>
            <div class="row-line1">${PayQuoted(quotedOf(a))}${c.floor}</div>
          </article>`;
        })
        .join("");
      body =
        pageHeader("Board", `<span class="page-count">${filtered.length}</span>`, Btn("New application", { variant: "primary", href: "/applications/new" }) + CommandHint("hunt applications create --company … --json")) +
        boardFiltersHtml() +
        `<table data-primitive="DataTable" class="board-table">
          <thead><tr><th>Company</th><th>Title</th><th>Status</th><th>Modality</th><th>Location</th><th>Pay</th><th>Floor</th><th>Updated</th><th>Id</th></tr></thead>
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
      </form>`;
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
      `<div data-primitive="PageHeader">
        <div>
          <h1 class="company-title">${esc(app.company)}</h1>
          <p class="role-title">${esc(titleOf(app))}</p>
        </div>
        <select data-primitive="Select" id="status-select">${statusOpts}</select>
        ${CopyId(app.id)}
        ${CommandHint(`hunt applications update ${app.id} --status ${app.status} --json`)}
      </div>`;
    const quotedForm = `<form id="quoted-form" class="section" data-primitive="QuotedForm">
      <h2>Quoted</h2>
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
      <div style="margin-top:12px">${Btn("Save quoted", { variant: "primary", type: "submit" })}</div>
    </form>`;
    const derived = `<div class="section"><h2>Derived</h2>${PayDerived(app.comp_derived)}${app.tax_home_for_net ? `<p class="faint">tax_home_for_net ${esc(app.tax_home_for_net)}</p>` : ""}<div style="margin-top:8px">${FloorBadge(app.comp_derived)}</div></div>`;
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
        <form id="artifact-form" style="margin-top:12px">
          <input type="file" name="file" required>
          ${select("kind", "jd", ["jd", "cv", "notes", "other"])}
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
      <div class="detail-left">${quotedForm}${derived}<div class="section"><h2>Knockouts</h2>${knocks}${urlLine}</div></div>
      <div class="detail-right">${right}</div>
    </div>
    <div class="sticky-save">${Btn("Save quoted", { variant: "primary", attrs: "data-submit-quoted" })}</div>`;
    root.innerHTML = shell("board", badges, body);
  }

  async function renderInbox(root, badges) {
    root.innerHTML = shell(
      "inbox",
      badges,
      pageHeader("Inbox", "", "") + LoadingSkeleton()
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
        const knocks = (it.knockouts || []).map((k) => `<span class="knockout">${esc(k)}</span>`).join("");
        const src = (it.payload && (it.payload.adapter || it.payload.source)) || "";
        return `<tr data-primitive="InboxRow" data-id="${esc(it.id)}">
          <td>${esc(it.company)}</td>
          <td>${esc(it.title)}</td>
          <td>${esc(src)}</td>
          <td>${PayQuoted(q)}</td>
          <td>${esc(it.why_keep || "")}</td>
          <td>${esc(it.why_risk || "")}</td>
          <td>${knocks}</td>
          <td title="${esc(it.created_at)}">${esc(relative(it.created_at))}</td>
          <td>
            ${Btn("Promote", { variant: "primary", attrs: `data-promote="${esc(it.id)}"` })}
            ${Btn("Dismiss", { variant: "danger", attrs: `data-dismiss="${esc(it.id)}"` })}
            ${CommandHint(`hunt inbox promote ${it.id} --json`)}
          </td>
          <td>${CopyId(it.id)}</td>
        </tr>`;
      })
      .join("");
    const cards = items
      .map((it) => {
        const q = quotedOf(it);
        return `<article data-primitive="InboxRow" class="inbox-card">
          <div class="row-line1"><strong>${esc(it.company)}</strong>${CopyId(it.id)}</div>
          <div>${esc(it.title)}</div>
          <div>${PayQuoted(q)}</div>
          <div class="muted">${esc(it.why_keep || "")}</div>
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
          <thead><tr><th>Company</th><th>Title</th><th>Source</th><th>Pay</th><th>Why keep</th><th>Why risk</th><th>Knockouts</th><th>Age</th><th>Actions</th><th>Id</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="inbox-cards">${cards}</div>`
    );
  }

  async function renderSources(root, badges) {
    root.innerHTML = shell("sources", badges, pageHeader("Sources", "", "") + LoadingSkeleton(4));
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
    root.innerHTML = shell(
      "sources",
      badges,
      pageHeader("Sources", `<span class="page-count">${sources.length}</span>`, CommandHint("hunt sources list --json")) +
        `<table data-primitive="DataTable" class="sources-table">
          <thead><tr><th>Name</th><th>Adapter</th><th>Enabled</th><th>Last run</th><th>Last error</th><th>Listings</th><th>Inbox</th><th></th><th>Id</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`
    );
  }

  async function renderJobs(root, badges) {
    root.innerHTML = shell("jobs", badges, pageHeader("Jobs", "", "") + LoadingSkeleton());
    const qs = new URLSearchParams();
    if (state.jobState) qs.set("state", state.jobState);
    if (state.jobType) qs.set("type", state.jobType);
    let data;
    try {
      data = await api(`/api/jobs${qs.toString() ? "?" + qs : ""}`);
    } catch (err) {
      root.innerHTML = shell("jobs", badges, ErrorBanner(err.message));
      return;
    }
    const jobs = data.jobs || [];
    const enqueue = `<form id="enqueue-form" class="header-actions">
      ${select("type", "screen-inbox", JOB_TYPES)}
      <input data-primitive="TextInput" name="target_id" placeholder="target id">
      ${Btn("Enqueue", { variant: "primary", type: "submit" })}
      ${CommandHint("hunt jobs enqueue --type screen-inbox --json")}
    </form>`;
    if (!jobs.length) {
      root.innerHTML = shell(
        "jobs",
        badges,
        pageHeader("Jobs", `<span class="page-count">0</span>`, enqueue) +
          EmptyState(
            "No jobs",
            "Run a source, screen the inbox, or enqueue tailor-cv from an application.",
            Btn("Open sources", { href: "/sources" })
          )
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
    root.innerHTML = shell(
      "jobs",
      badges,
      pageHeader("Jobs", `<span class="page-count">${jobs.length}</span>`, enqueue) +
        `<table data-primitive="DataTable" class="jobs-table">
          <thead><tr><th>Id</th><th>Type</th><th>Target</th><th>State</th><th>Created</th><th>Started</th><th>Finished</th><th>Error</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`
    );
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
    state.route = parseRoute();
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
    }
  });

  document.addEventListener("change", (ev) => {
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
    if (ev.target.closest("#quoted-form")) state.quotedDirty = true;
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
