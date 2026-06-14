/* pageserve-server — shared front-end core: theme, auth, API client, app shell.
 * Loaded by every page. Exposes the global `PS`. */
(function () {
  "use strict";

  // ── Tailwind runtime config (must run before Tailwind scans the DOM) ──────
  if (window.tailwind) {
    window.tailwind.config = {
      darkMode: "class",
      theme: {
        extend: {
          fontFamily: { sans: ["Inter", "system-ui", "ui-sans-serif", "sans-serif"] },
          colors: {
            brand: {
              50: "#fef2f2", 100: "#fee2e2", 200: "#fecaca", 300: "#fca5a5",
              400: "#f87171", 500: "#ef4444", 600: "#dc2626", 700: "#b91c1c",
              800: "#991b1b", 900: "#7f1d1d", 950: "#450a0a",
            },
          },
          boxShadow: { soft: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.06)" },
        },
      },
    };
  }

  const ICONS = {
    dashboard: '<path d="M3 13h8V3H3v10Zm0 8h8v-6H3v6Zm10 0h8V11h-8v10Zm0-18v6h8V3h-8Z"/>',
    projects: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
    playground: '<path d="M5 3l14 9-14 9V3Z"/>',
    keys: '<path d="M14 7a4 4 0 1 0-3.9 5h.9l2 2 2-2 2 2 2-2-2-2 1-1a4 4 0 0 0-4-2Z"/><circle cx="9" cy="11" r="1.6"/>',
    users: '<path d="M16 14a4 4 0 1 0-4-4 4 4 0 0 0 4 4Zm-8 0a3 3 0 1 0-3-3 3 3 0 0 0 3 3Zm0 2c-3 0-6 1.5-6 4v2h8M16 16c-3.5 0-8 1.8-8 4.5V22h16v-1.5c0-2.7-4.5-4.5-8-4.5Z"/>',
    audit: '<path d="M5 3h10l4 4v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm9 1v4h4M8 13h8M8 17h8M8 9h3"/>',
    docs: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14Z"/><path d="M8 7h8M8 11h6"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14Z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m7 14 5-5-5-5m5 5H9"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    upload: '<path d="M12 16V4m0 0 4 4m-4-4L8 8M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    trash: '<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m2 0v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V7"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16m0 5v-5h5"/>',
    plus: '<path d="M12 5v14m-7-7h14"/>',
    chevron: '<path d="m9 18 6-6-6-6"/>',
    spark: '<path d="M12 3v4m0 10v4M5 12H1m22 0h-4M6.3 6.3 3.5 3.5m17 17-2.8-2.8M6.3 17.7l-2.8 2.8m17-17-2.8 2.8"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
    alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h16.9a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 4h.01"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/>',
  };

  function svg(name, cls) {
    return `<svg class="${cls || "w-5 h-5"}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
  }

  const PS = {
    token: null,
    user: null,
    _refreshTimer: null,

    // ── Theme ───────────────────────────────────────────────────────────────
    initTheme() {
      const saved = localStorage.getItem("ps-theme");
      const dark = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.classList.toggle("dark", dark);
    },
    toggleTheme() {
      const dark = !document.documentElement.classList.contains("dark");
      document.documentElement.classList.toggle("dark", dark);
      localStorage.setItem("ps-theme", dark ? "dark" : "light");
      this._syncThemeIcon();
    },
    _syncThemeIcon() {
      const el = document.getElementById("theme-icon");
      if (el) el.innerHTML = document.documentElement.classList.contains("dark") ? svg("sun") : svg("moon");
    },

    // ── Query string state ────────────────────────────────────────────────
    qs() { return new URLSearchParams(location.search); },
    setQuery(params) {
      const u = new URL(location.href);
      Object.entries(params).forEach(([k, v]) => {
        if (v === null || v === undefined || v === "") u.searchParams.delete(k);
        else u.searchParams.set(k, v);
      });
      history.replaceState(null, "", u);
    },

    // ── Auth ────────────────────────────────────────────────────────────────
    async login(email, password) {
      const res = await this.api("POST", "/auth/login", { email, password }, false);
      this.token = res.access_token;
      this.user = res.user;
      sessionStorage.setItem("ps-refresh", res.refresh_token);
      sessionStorage.setItem("ps-user", JSON.stringify(res.user));
      this._schedule(res.expires_in);
      return res.user;
    },
    async refresh() {
      const rt = sessionStorage.getItem("ps-refresh");
      if (!rt) throw new Error("no refresh token");
      const res = await this.api("POST", "/auth/refresh", { refresh_token: rt }, false);
      this.token = res.access_token;
      if (!this.user) {
        this.user = await this.api("GET", "/auth/me");
        sessionStorage.setItem("ps-user", JSON.stringify(this.user));
      }
      this._schedule(res.expires_in);
    },
    _schedule(expiresIn) {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = setTimeout(() => this.refresh().catch(() => this.logout()), Math.max(10, expiresIn - 300) * 1000);
    },
    logout() {
      const rt = sessionStorage.getItem("ps-refresh");
      if (rt && this.token) this.api("POST", "/auth/logout", { refresh_token: rt }).catch(() => {});
      this.token = null; this.user = null;
      sessionStorage.removeItem("ps-refresh");
      sessionStorage.removeItem("ps-user");
      clearTimeout(this._refreshTimer);
      location.href = "/ui/login.html";
    },
    /** Ensure a valid session; redirect to login if not. Returns the user. */
    async ensureAuth() {
      try { await this.refresh(); return this.user; }
      catch { location.href = "/ui/login.html"; throw new Error("unauthenticated"); }
    },

    // ── API client ───────────────────────────────────────────────────────────
    async api(method, path, body = null, auth = true, _retry = true) {
      const headers = {};
      const isForm = body instanceof FormData;
      if (body && !isForm) headers["Content-Type"] = "application/json";
      if (auth && this.token) headers["Authorization"] = `Bearer ${this.token}`;
      const res = await fetch(path, { method, headers, body: body ? (isForm ? body : JSON.stringify(body)) : undefined });
      if (res.status === 401 && auth && _retry) {
        try { await this.refresh(); } catch { this.logout(); throw new Error("Session expired — please sign in again"); }
        return this.api(method, path, body, auth, false);
      }
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(typeof e.detail === "string" ? e.detail : res.statusText);
      }
      return res.status === 204 ? null : res.json();
    },

    /** Consume an SSE endpoint with the Bearer token (EventSource can't send headers).
     *  Calls onMessage(parsedData) per `data:` line. Returns when the stream ends.
     *  Pass opts.signal (AbortSignal) to stop early. */
    async streamSSE(method, path, body, onMessage, opts = {}) {
      const headers = { Authorization: `Bearer ${this.token}` };
      if (body) headers["Content-Type"] = "application/json";
      const res = await fetch(path, {
        method, headers, body: body ? JSON.stringify(body) : undefined, signal: opts.signal,
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Stream failed");
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try { onMessage(JSON.parse(line.slice(6))); } catch { /* ignore keep-alive */ }
          }
        }
      }
    },

    // ── UI helpers ────────────────────────────────────────────────────────────
    toast(msg, type = "info") {
      let host = document.getElementById("ps-toasts");
      if (!host) {
        host = document.createElement("div");
        host.id = "ps-toasts";
        host.className = "fixed top-4 right-4 z-[300] flex flex-col gap-2.5";
        document.body.appendChild(host);
      }
      const meta = {
        info: { icon: "info", color: "text-zinc-500 dark:text-zinc-400", bar: "#a1a1aa" },
        success: { icon: "check", color: "text-emerald-500", bar: "#10b981" },
        error: { icon: "alert", color: "text-red-500", bar: "#ef4444" },
      }[type] || { icon: "info", color: "text-zinc-500", bar: "#a1a1aa" };

      const t = document.createElement("div");
      t.className = "ps-toast-in flex items-start gap-3 w-80 max-w-[calc(100vw-2rem)] bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-xl shadow-lg px-4 py-3 text-sm";
      t.style.borderLeft = `3px solid ${meta.bar}`;
      t.innerHTML = `<span class="shrink-0 mt-0.5 ${meta.color}">${svg(meta.icon, "w-5 h-5")}</span>
        <span class="ps-toast-msg flex-1 text-zinc-700 dark:text-zinc-200 leading-snug break-words"></span>
        <button class="ps-toast-x shrink-0 -mr-1 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors">${svg("x", "w-4 h-4")}</button>`;
      t.querySelector(".ps-toast-msg").textContent = msg;

      let timer;
      const dismiss = () => { clearTimeout(timer); t.classList.add("ps-toast-out"); setTimeout(() => t.remove(), 280); };
      t.querySelector(".ps-toast-x").addEventListener("click", dismiss);
      host.appendChild(t);
      timer = setTimeout(dismiss, 3600);
    },

    /** Styled replacement for window.confirm — returns a Promise<boolean>. */
    confirm(opts = {}) {
      const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const { title = "Are you sure?", message = "", confirmText = "Confirm", cancelText = "Cancel", danger = false } = opts;
      return new Promise((resolve) => {
        const host = document.createElement("div");
        host.className = "ps-modal-backdrop fixed inset-0 bg-black/40 dark:bg-black/60 flex items-center justify-center z-[310] p-4";
        const accent = danger
          ? "bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-400"
          : "bg-brand-50 text-brand-600 dark:bg-brand-500/10 dark:text-brand-400";
        host.innerHTML = `
          <div class="ps-modal-panel ps-card rounded-2xl p-6 w-full max-w-sm shadow-xl">
            <div class="flex items-start gap-3">
              <div class="w-10 h-10 shrink-0 rounded-xl flex items-center justify-center ${accent}">${svg(danger ? "alert" : "info", "w-5 h-5")}</div>
              <div class="min-w-0 pt-0.5">
                <h3 class="font-semibold tracking-tight">${esc(title)}</h3>
                ${message ? `<p class="text-sm text-zinc-500 mt-1 leading-relaxed">${esc(message)}</p>` : ""}
              </div>
            </div>
            <div class="flex gap-2 justify-end mt-5">
              <button data-cancel class="ps-btn ps-btn-ghost">${esc(cancelText)}</button>
              <button data-ok class="ps-btn ${danger ? "ps-btn-danger" : "ps-btn-primary"}">${esc(confirmText)}</button>
            </div>
          </div>`;
        document.body.appendChild(host);
        const onKey = (e) => { if (e.key === "Escape") done(false); else if (e.key === "Enter") done(true); };
        const done = (val) => {
          document.removeEventListener("keydown", onKey);
          host.classList.add("ps-toast-out");
          setTimeout(() => host.remove(), 200);
          resolve(val);
        };
        host.querySelector("[data-ok]").addEventListener("click", () => done(true));
        host.querySelector("[data-cancel]").addEventListener("click", () => done(false));
        host.addEventListener("click", (e) => { if (e.target === host) done(false); });
        document.addEventListener("keydown", onKey);
        host.querySelector("[data-ok]").focus();
      });
    },

    /** Reveal-once modal for a sensitive value (e.g. a temp password). Resolves when dismissed. */
    showSecret(opts = {}) {
      const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const { title = "Saved", label = "Value", value = "", note = "" } = opts;
      return new Promise((resolve) => {
        const host = document.createElement("div");
        host.className = "ps-modal-backdrop fixed inset-0 bg-black/40 dark:bg-black/60 flex items-center justify-center z-[310] p-4";
        host.innerHTML = `
          <div class="ps-modal-panel ps-card rounded-2xl p-6 w-full max-w-md shadow-xl">
            <h3 class="font-semibold tracking-tight">${esc(title)}</h3>
            ${note ? `<p class="text-sm text-amber-600 dark:text-amber-400 mt-1">${esc(note)}</p>` : ""}
            <div class="text-xs text-zinc-500 uppercase tracking-wide mt-4 mb-1">${esc(label)}</div>
            <div class="flex items-center gap-2">
              <code class="ps-secret-val flex-1 bg-zinc-100 dark:bg-zinc-950 rounded-xl px-3 py-2 text-sm break-all font-mono"></code>
              <button data-copy class="ps-btn ps-btn-ghost !px-3">Copy</button>
            </div>
            <button data-close class="ps-btn ps-btn-primary w-full mt-5">Done</button>
          </div>`;
        host.querySelector(".ps-secret-val").textContent = value;
        document.body.appendChild(host);
        const done = () => { host.classList.add("ps-toast-out"); setTimeout(() => host.remove(), 200); resolve(); };
        host.querySelector("[data-copy]").addEventListener("click", () => this.copyText(value));
        host.querySelector("[data-close]").addEventListener("click", done);
        host.querySelector("[data-close]").focus();
      });
    },
    fmtTime(t) { return t ? new Date(t).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : ""; },
    fmtRelative(t) {
      if (!t) return "";
      const s = (Date.now() - new Date(t)) / 1000;
      if (s < 60) return "just now";
      if (s < 3600) { const m = Math.floor(s / 60); return `${m} min${m > 1 ? "s" : ""} ago`; }
      if (s < 86400) { const h = Math.floor(s / 3600); return `${h} hour${h > 1 ? "s" : ""} ago`; }
      const d = Math.floor(s / 86400); return `${d} day${d > 1 ? "s" : ""} ago`;
    },
    statusBadge(s) {
      const map = {
        completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
        indexing: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
        pending: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
        failed: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400",
      };
      return map[s] || "bg-zinc-100 text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300";
    },
    renderCitations(text) {
      if (!text) return "";
      const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      return esc(text)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/\n/g, "<br>")
        .replace(/\[\[([^:\]]+):(\d+)\]\]/g, (_m, _d, p) =>
          `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300 rounded text-xs font-mono mx-0.5 align-baseline">tr.${p}</span>`);
    },
    icon: svg,

    // ── App shell (sidebar + topbar) ──────────────────────────────────────────
    NAV: [
      { key: "dashboard", label: "Overview", href: "/ui/index.html" },
      { key: "projects", label: "Projects", href: "/ui/projects.html" },
      { key: "playground", label: "Playground", href: "/ui/playground.html" },
      { key: "keys", label: "API Keys", href: "/ui/keys.html" },
      { key: "docs", label: "Documentation", href: "/ui/docs.html" },
      { key: "users", label: "Users", href: "/ui/users.html", admin: true },
      { key: "audit", label: "Audit Log", href: "/ui/audit.html", admin: true },
    ],
    mountShell(active, title, subtitle) {
      const isAdmin = this.user?.role === "admin";
      const links = this.NAV.filter((n) => !n.admin || isAdmin).map((n) => {
        const on = n.key === active;
        const cls = on
          ? "bg-brand-50 dark:bg-brand-500/10 text-brand-700 dark:text-brand-300 font-semibold"
          : "text-zinc-500 hover:bg-zinc-100/80 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-100 font-medium";
        return `<a href="${n.href}" class="ps-nav-item group relative flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-150 ${cls}">
          ${on ? '<span class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-r-full bg-brand-600 dark:bg-brand-400"></span>' : ""}
          <span class="${on ? "text-brand-600 dark:text-brand-400" : "opacity-80 group-hover:opacity-100 group-hover:scale-110 transition-transform"}">${svg(n.key, "w-5 h-5 shrink-0")}</span><span>${n.label}</span></a>`;
      }).join("");

      const sidebar = document.getElementById("sidebar");
      if (sidebar) {
        sidebar.className = "w-64 shrink-0 bg-white/70 dark:bg-zinc-900/60 backdrop-blur-xl border-r border-zinc-200 dark:border-zinc-800/80 flex flex-col h-screen";
        sidebar.innerHTML = `
          <div class="h-16 flex items-center gap-2.5 px-5">
            <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 text-white flex items-center justify-center shadow-[0_4px_14px_-4px_rgb(220_38_38/.55)]">${svg("spark", "w-5 h-5")}</div>
            <div><div class="font-semibold text-sm leading-tight tracking-tight">PageServe</div><div class="text-[11px] text-zinc-400">self-hosted RAG</div></div>
          </div>
          <nav class="flex-1 px-3 py-2 space-y-1 overflow-y-auto">${links}</nav>
          <div class="p-3 mt-auto">
            <div class="flex items-center gap-1 p-1.5 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white/60 dark:bg-zinc-900/60 ${active === "settings" ? "ring-1 ring-brand-500/40" : ""}">
              <a href="/ui/settings.html" class="flex items-center gap-2.5 flex-1 min-w-0 px-1.5 py-1 rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-800/60 transition-colors" title="Account settings">
                <div class="w-8 h-8 rounded-full bg-gradient-to-br from-brand-100 to-brand-200 dark:from-brand-500/30 dark:to-brand-600/20 text-brand-700 dark:text-brand-200 flex items-center justify-center text-sm font-semibold shrink-0">${(this.user?.full_name || this.user?.email || "A")[0].toUpperCase()}</div>
                <div class="flex-1 min-w-0"><div class="text-sm font-medium truncate">${this.user?.full_name || this.user?.email || ""}</div><div class="text-[11px] text-zinc-400 capitalize">${this.user?.role || ""}</div></div>
              </a>
              <button id="ps-logout" class="ps-icon-btn !w-8 !h-8 hover:!text-red-500 shrink-0" title="Sign out">${svg("logout", "w-[18px] h-[18px]")}</button>
            </div>
          </div>`;
      }

      const topbar = document.getElementById("topbar");
      if (topbar) {
        topbar.className = "h-16 shrink-0 bg-white/70 dark:bg-zinc-950/70 backdrop-blur-xl border-b border-zinc-200 dark:border-zinc-800/80 flex items-center gap-4 px-6 sticky top-0 z-20";
        topbar.innerHTML = `
          <div class="min-w-0">
            <h1 class="font-semibold text-base leading-tight tracking-tight" id="page-title">${title || ""}</h1>
            ${subtitle ? `<p class="text-xs text-zinc-400 leading-tight mt-0.5">${subtitle}</p>` : ""}
          </div>
          <div id="topbar-extra" class="flex-1 flex items-center gap-3"></div>
          <button id="theme-toggle" class="ps-icon-btn" title="Toggle theme"><span id="theme-icon"></span></button>`;
      }

      document.getElementById("ps-logout")?.addEventListener("click", () => this.logout());
      document.getElementById("theme-toggle")?.addEventListener("click", () => this.toggleTheme());
      this._syncThemeIcon();
    },

    /** Read the user cached from a previous login/refresh (this tab's session). */
    _cachedUser() {
      try { return JSON.parse(sessionStorage.getItem("ps-user") || "null"); }
      catch { return null; }
    },

    /** One-call boot for protected pages: theme + auth guard + shell.
     * Renders the shell immediately from the cached user so navigating between
     * tabs feels instant, then validates the session in the background. */
    async boot(active, title, subtitle) {
      this.initTheme();
      const cached = this._cachedUser();
      if (cached) {
        this.user = cached;
        this.mountShell(active, title, subtitle);
      }
      await this.ensureAuth();
      if (!cached) this.mountShell(active, title, subtitle);
      return this.user;
    },

    /** Copy arbitrary text to the clipboard with a toast. */
    async copyText(text) {
      try { await navigator.clipboard.writeText(text || ""); this.toast("Copied to clipboard", "success"); }
      catch { this.toast("Copy failed", "error"); }
    },
    /** Copy the code inside the nearest <pre> to a copy button, with a check animation. */
    copyBlock(btn) {
      const pre = btn.closest(".ps-code")?.querySelector("code, pre");
      const text = pre ? pre.innerText : "";
      navigator.clipboard.writeText(text).then(() => {
        const prev = btn.innerHTML;
        btn.innerHTML = svg("check", "w-4 h-4");
        setTimeout(() => { btn.innerHTML = prev; }, 1400);
      });
    },
  };

  window.PS = PS;
  // Apply theme ASAP to avoid a flash of the wrong color scheme.
  PS.initTheme();
})();
