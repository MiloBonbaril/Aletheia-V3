import {
  html, render, useState, useEffect, useRef, useMemo, useCallback,
} from "/web/vendor/htm-preact.module.js";

const LOG_CAP = 2000;
const LOG_WIDTH_KEY = "terminal.logWidth";
const LOG_WIDTH_DEFAULT = 420;
const LOG_WIDTH_MIN = 320;
// Room that the sidebar and the toolbar of the main column need. The same value
// is the max-width of .logs in style.css: change the two together. The CSS one
// also narrows the panel on its own when the window shrinks, thus no resize
// listener is needed here.
const LOG_WIDTH_GUTTER = 620;
const readLogWidth = () => {
  try {
    return Number(localStorage.getItem(LOG_WIDTH_KEY)) || LOG_WIDTH_DEFAULT;
  } catch {
    return LOG_WIDTH_DEFAULT;  // a private window can refuse the read
  }
};
const writeLogWidth = (w) => {
  try { localStorage.setItem(LOG_WIDTH_KEY, String(w)); } catch { /* not important */ }
};

// ---------- formatting ----------

const bytes = (n) => {
  if (!n) return "—";
  const units = ["o", "Ko", "Mo", "Go"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
};

const duration = (s) => {
  if (s == null) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} j ${String(h).padStart(2, "0")} h`;
  if (h) return `${h} h ${String(m).padStart(2, "0")} m`;
  return `${m} m ${String(Math.floor(s % 60)).padStart(2, "0")} s`;
};

const clock = (t) => new Date(t * 1000).toTimeString().slice(0, 8);

const STATE_FR = {
  running: "actif", stopped: "arrêté", crashed: "crash",
  starting: "démarre", stopping: "arrête", building: "compile",
};

// ---------- api ----------

const post = (path) => fetch(path, { method: "POST" });

// ---------- pieces ----------

function Meter({ name, value, total, unit, pct }) {
  const ratio = pct != null ? pct / 100 : (total ? value / total : 0);
  const cls = ratio > 0.9 ? "crit" : ratio > 0.75 ? "hot" : "";
  const right = pct != null
    ? `${Math.round(pct)}%`
    : html`<b>${bytes(value)}</b> / ${bytes(total)}`;
  return html`
    <div class="meter">
      <div class="row"><span>${name}</span><span>${right}</span></div>
      <div class="track"><div class=${`fill ${cls}`} style=${{ width: `${Math.min(100, ratio * 100)}%` }}></div></div>
    </div>`;
}

function HostMeters({ host }) {
  const gpu = host.gpu || {};
  return html`
    <div class="meters">
      <${Meter} name="CPU hôte" pct=${host.cpu || 0} />
      <${Meter} name="RAM" value=${host.ram_used} total=${host.ram_total} />
      ${gpu.name && html`
        <${Meter} name="GPU" pct=${gpu.util} />
        <${Meter} name="VRAM" value=${gpu.vram_used} total=${gpu.vram_total} />
        <div class="gpu-name"><span>${gpu.name}</span><span>${Math.round(gpu.temp)} °C</span></div>`}
    </div>`;
}

function Sidebar({ services, profiles, host, group, setGroup }) {
  const groups = useMemo(() => {
    const seen = [];
    for (const s of services) if (!seen.includes(s.group)) seen.push(s.group);
    return seen;
  }, [services]);
  const failing = services.filter((s) => s.status === "crashed").length;

  const item = (key, name, count, danger) => html`
    <button class=${`grp ${group === key ? "active" : ""} ${danger ? "danger" : ""}`}
            onClick=${() => setGroup(key)}>
      <span>${name}</span><span class="count">${count}</span>
    </button>`;

  return html`
    <aside class="sidebar">
      <div class="scroll">
        <section>
          <span class="label">Groupes</span>
          ${item("*", "Tous les services", services.length, false)}
          ${groups.map((g) => item(g, g, services.filter((s) => s.group === g).length, false))}
          ${failing > 0 && item("!", "En erreur", failing, true)}
        </section>
        <section>
          <span class="label">Profils de lancement</span>
          ${Object.keys(profiles).map((name) => html`
            <div class="prof">
              <span class="name">${name}</span>
              <button title="démarrer le profil" onClick=${() => post(`/api/profiles/${name}/start`)}>▶</button>
              <button title="arrêter le profil" onClick=${() => post(`/api/profiles/${name}/stop`)}>■</button>
            </div>`)}
        </section>
      </div>
      <${HostMeters} host=${host} />
    </aside>`;
}

function Spark({ history }) {
  const points = history.slice(-40);
  const peak = Math.max(10, ...points.map((p) => p.cpu));
  const live = points.some((p) => p.cpu > 0);
  return html`
    <div class=${`spark ${live ? "live" : ""}`}>
      ${points.map((p) => html`<i style=${{ height: `${Math.max(2, (p.cpu / peak) * 100)}%` }}></i>`)}
    </div>`;
}

function Actions({ s }) {
  const busy = ["starting", "stopping", "building"].includes(s.status);
  const up = s.status === "running";
  const stop = (e) => { e.stopPropagation(); post(`/api/services/${s.name}/stop`); };
  const start = (e) => { e.stopPropagation(); post(`/api/services/${s.name}/start`); };
  return html`
    <div class="actions">
      ${up
        ? html`<button onClick=${stop} disabled=${busy}>■ Arrêter</button>`
        : html`<button class=${s.status === "crashed" ? "primary" : ""} onClick=${start} disabled=${busy}>${s.status === "crashed" ? "▶ Relancer" : "▶ Démarrer"}</button>`}
      <button onClick=${(e) => { e.stopPropagation(); post(`/api/services/${s.name}/restart`); }}
              disabled=${busy}>Redémarrer</button>
      ${s.can_rebuild && html`
        <button onClick=${(e) => { e.stopPropagation(); post(`/api/services/${s.name}/rebuild`); }}
                disabled=${busy}>Rebuild</button>`}
    </div>`;
}

function Card({ s, selected, onSelect }) {
  return html`
    <div class=${`card ${selected ? "selected" : ""}`} onClick=${() => onSelect(s.name)}>
      <div class="head">
        <span class=${`dot ${s.status}`}></span>
        <span class="name">${s.name}</span>
        ${s.stale && html`<span class="stale" title="une source est plus récente que le binaire">périmé</span>`}
        <span class=${`state ${s.status}`}>${STATE_FR[s.status] || s.status}</span>
      </div>
      <p class="desc">${s.note || s.group}</p>
      <div class="stats">
        <div><span class="k">Uptime</span><span class="v">${duration(s.uptime)}</span></div>
        <div><span class="k">CPU</span><span class="v">${s.status === "running" ? `${s.cpu}%` : "—"}</span></div>
        <div><span class="k">RAM</span><span class="v">${bytes(s.rss)}</span></div>
        <div><span class="k">VRAM</span><span class="v">${bytes(s.vram)}</span></div>
      </div>
      <${Spark} history=${s.history} />
      <${Actions} s=${s} />
    </div>`;
}

function Table({ services, selected, onSelect }) {
  return html`
    <table>
      <thead><tr>
        <th>Service</th><th>Groupe</th><th>État</th><th>PID</th>
        <th class="num">Uptime</th><th class="num">CPU</th><th class="num">RAM</th><th class="num">VRAM</th>
      </tr></thead>
      <tbody>
        ${services.map((s) => html`
          <tr class=${selected === s.name ? "selected" : ""} onClick=${() => onSelect(s.name)}>
            <td><span class=${`dot ${s.status}`} style="display:inline-block;margin-right:7px"></span>${s.name}</td>
            <td>${s.group}</td>
            <td class=${`state ${s.status}`} style="text-transform:none">${STATE_FR[s.status] || s.status}</td>
            <td>${s.pid || "—"}</td>
            <td class="num">${duration(s.uptime)}</td>
            <td class="num">${s.status === "running" ? `${s.cpu}%` : "—"}</td>
            <td class="num">${bytes(s.rss)}</td>
            <td class="num">${bytes(s.vram)}</td>
          </tr>`)}
      </tbody>
    </table>`;
}

function Grip({ width, setWidth }) {
  const [dragging, setDragging] = useState(false);
  const latest = useRef(width);
  const apply = (w) => {
    const room = Math.max(LOG_WIDTH_MIN, window.innerWidth - LOG_WIDTH_GUTTER);
    const clamped = Math.min(room, Math.max(LOG_WIDTH_MIN, Math.round(w)));
    latest.current = clamped;
    setWidth(clamped);
  };
  const onKeyDown = (e) => {
    const step = e.key === "ArrowLeft" ? 24 : e.key === "ArrowRight" ? -24 : 0;
    if (!step) return;
    e.preventDefault();
    apply(latest.current + step);
    writeLogWidth(latest.current);
  };
  return html`
    <div class=${`grip ${dragging ? "dragging" : ""}`} role="separator" aria-orientation="vertical"
         tabindex="0" title="Glisser pour redimensionner · double-clic pour réinitialiser"
         onKeyDown=${onKeyDown}
         onDblClick=${() => { apply(LOG_WIDTH_DEFAULT); writeLogWidth(LOG_WIDTH_DEFAULT); }}
         onPointerDown=${(e) => {
           e.preventDefault();
           e.currentTarget.setPointerCapture(e.pointerId);
           document.body.classList.add("resizing");
           setDragging(true);
         }}
         onPointerMove=${(e) => {
           // hasPointerCapture, not the `dragging` state: a fast drag sends its
           // first moves before the re-render, and those moves were lost.
           if (e.currentTarget.hasPointerCapture(e.pointerId)) apply(window.innerWidth - e.clientX);
         }}
         onPointerUp=${(e) => {
           e.currentTarget.releasePointerCapture(e.pointerId);
           document.body.classList.remove("resizing");
           setDragging(false);
           writeLogWidth(latest.current);
         }}></div>`;
}

function LogPanel({ s, lines, onClose }) {
  const [follow, setFollow] = useState(true);
  const [wrap, setWrap] = useState(false);
  const [level, setLevel] = useState("all");
  const [width, setWidth] = useState(readLogWidth);
  const box = useRef(null);

  const shown = useMemo(() => (
    level === "all" ? lines
      : level === "warn" ? lines.filter((l) => l.level !== "info")
        : lines.filter((l) => l.level === "err")
  ), [lines, level]);

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [shown.length, follow]);

  const cycle = () => setLevel(level === "all" ? "warn" : level === "warn" ? "err" : "all");
  const levelName = { all: "tout", warn: "warn+", err: "erreurs" }[level];

  return html`
    <aside class="logs" style=${{ width: `${width}px` }}>
      <${Grip} width=${width} setWidth=${setWidth} />
      <header>
        <h2>
          <span class=${`dot ${s.status}`}></span>${s.name}
          <button class="close" onClick=${onClose} title="fermer">✕</button>
        </h2>
        <div class="meta">
          ${STATE_FR[s.status] || s.status}
          ${s.pid ? ` · pid ${s.pid}` : ""}
          ${s.uptime != null ? ` · ${duration(s.uptime)}` : ""}
          ${s.exit_code != null && s.status !== "running" ? ` · code ${s.exit_code}` : ""}
          ${s.restarts ? ` · ${s.restarts} redémarrage(s)` : ""}
        </div>
      </header>
      ${s.depends_on.length > 0 && html`
        <div class="dep">dépend de <b>${s.depends_on.join(", ")}</b></div>`}
      <div class="ctl">
        <button class=${follow ? "on" : ""} onClick=${() => setFollow(!follow)}>suivi</button>
        <button class=${wrap ? "on" : ""} onClick=${() => setWrap(!wrap)}>retour ligne</button>
        <button onClick=${cycle}>niveau · ${levelName}</button>
        <span class="spacer"></span>
        <button onClick=${() => post(`/api/services/${s.name}/clear`)}>effacer</button>
      </div>
      <div class=${`stream ${wrap ? "wrap" : ""}`} ref=${box}>
        ${shown.length === 0
          ? html`<div class="empty">aucune ligne</div>`
          : shown.map((l) => html`
              <div class=${`line ${l.level}`} key=${l.seq}>
                <span class="t">${clock(l.t)}</span>
                <span class="lv">${l.level.toUpperCase()}</span>
                <span class="txt">${l.text}</span>
              </div>`)}
      </div>
    </aside>`;
}

// ---------- config tab ----------

function ConfigTab() {
  const [files, setFiles] = useState([]);
  const [name, setName] = useState(null);
  const [saved, setSaved] = useState({ text: "", mtime: "" });
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState(null);
  const dirty = draft !== saved.text;
  const file = files.find((f) => f.name === name);

  const load = useCallback((n) => (
    fetch(`/api/config/${encodeURIComponent(n)}`)
      .then((r) => r.json())
      .then((d) => {
        setName(d.name);
        setSaved({ text: d.text, mtime: d.mtime });
        setDraft(d.text);
        setNote(null);
      })
      .catch(() => setNote({ kind: "err", text: "lecture impossible" }))
  ), []);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((d) => { setFiles(d.files); if (d.files.length) load(d.files[0].name); })
      .catch(() => setNote({ kind: "err", text: "le daemon ne répond pas" }));
  }, [load]);

  // The same file also lives in an editor and in git. `mtime` is the version the
  // browser read; the daemon refuses to write over a version it never saw.
  const save = async (mtime) => {
    const response = await fetch(`/api/config/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: draft, mtime: mtime ?? saved.mtime }),
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 409) {
      setNote({ kind: "warn", text: "le fichier a changé sur le disque depuis l'ouverture", conflict: body });
      return;
    }
    if (!response.ok) {
      setNote({ kind: "err", text: "écriture refusée par le daemon" });
      return;
    }
    setSaved({ text: draft, mtime: body.mtime });
    setNote({ kind: "ok", text: "enregistré", service: body.service });
  };

  const open = (n) => {
    if (n === name) return;
    if (dirty && !window.confirm(`${name} a des modifications non enregistrées. Les abandonner ?`)) return;
    load(n);
  };

  const onKeyDown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(); }
  };

  return html`
    <div class="body">
      <aside class="sidebar">
        <div class="scroll">
          <section>
            <span class="label">Fichiers</span>
            ${files.map((f) => html`
              <button key=${f.name} class=${`grp ${name === f.name ? "active" : ""}`}
                      onClick=${() => open(f.name)}>
                <span>${f.name}</span>
                <span class="count">${name === f.name && dirty ? "●" : ""}</span>
              </button>`)}
          </section>
        </div>
      </aside>

      <main class="main cfg">
        <header>
          <div>
            <h1>${name || "Configuration"}</h1>
            <p>${file ? (file.note || file.path) : "les fichiers déclarés dans services.toml"}</p>
          </div>
          <span class="spacer"></span>
          <div class="toolbar">
            ${file && html`<span class="path">${file.path}</span>`}
            <button onClick=${() => load(name)} disabled=${!name}>Recharger</button>
            <button class="primary" onClick=${() => save()} disabled=${!dirty}>
              Enregistrer${dirty ? " ·" : ""}
            </button>
          </div>
        </header>

        ${!name
          ? html`<p class="empty">services.toml ne déclare aucun bloc [[config_file]].</p>`
          : html`
            <textarea spellcheck=${false} value=${draft} onKeyDown=${onKeyDown}
                      onInput=${(e) => setDraft(e.target.value)}></textarea>
            <div class=${`status ${note ? note.kind : ""}`}>
              ${note
                ? html`
                    <span>${note.text}</span>
                    ${note.conflict && html`
                      <button onClick=${() => { setDraft(note.conflict.text); setSaved({ text: note.conflict.text, mtime: note.conflict.mtime }); setNote(null); }}>
                        Prendre la version du disque
                      </button>
                      <button onClick=${() => save(note.conflict.mtime)}>Écraser avec la mienne</button>`}
                    ${note.service && html`
                      <button class="primary" onClick=${() => { post(`/api/services/${note.service}/restart`); setNote({ kind: "ok", text: `redémarrage de ${note.service} demandé` }); }}>
                        Redémarrer ${note.service}
                      </button>`}`
                : html`<span>${dirty ? "modifié · Ctrl+S pour enregistrer" : "à jour"}</span>`}
              <span class="spacer"></span>
              <span class="count">${draft.length} caractères</span>
            </div>
            ${file && file.service && html`
              <p class="hint">
                <b>${file.service}</b> lit ce fichier une seule fois, à son démarrage.
                Une modification s'applique au redémarrage du service.
              </p>`}`}
      </main>
    </div>`;
}

// ---------- app ----------

function App() {
  const [snap, setSnap] = useState(null);
  const [logs, setLogs] = useState({});
  const [selected, setSelected] = useState(null);
  const [group, setGroup] = useState("*");
  const [filter, setFilter] = useState("");
  const [view, setView] = useState("grid");
  const [tab, setTab] = useState("services");
  const [online, setOnline] = useState(false);

  useEffect(() => {
    const source = new EventSource("/api/stream");
    source.onopen = () => setOnline(true);
    source.onerror = () => setOnline(false);
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type === "state") {
        setOnline(true);
        setSnap(payload);
      } else if (payload.type === "log") {
        setLogs((prev) => {
          const next = [...(prev[payload.service] || []), payload.line];
          if (next.length > LOG_CAP) next.splice(0, next.length - LOG_CAP);
          return { ...prev, [payload.service]: next };
        });
      }
    };
    return () => source.close();
  }, []);

  // Backfill the buffer that the daemon already holds for a newly opened service.
  const select = useCallback((name) => {
    setSelected((cur) => (cur === name ? cur : name));
    fetch(`/api/services/${name}/logs`)
      .then((r) => r.json())
      .then((d) => setLogs((prev) => {
        const live = prev[name] || [];
        const known = new Set(live.map((l) => l.seq));
        const merged = [...d.lines.filter((l) => !known.has(l.seq)), ...live];
        merged.sort((a, b) => a.seq - b.seq);
        return { ...prev, [name]: merged.slice(-LOG_CAP) };
      }))
      .catch(() => {});
  }, []);

  if (!snap) {
    return html`<div class="shell"><div class="main"><p class="label">connexion au daemon…</p></div></div>`;
  }

  const all = snap.services;
  const visible = all.filter((s) => {
    const inGroup = group === "*" || (group === "!" ? s.status === "crashed" : s.group === group);
    const matches = !filter || s.name.toLowerCase().includes(filter.toLowerCase());
    return inGroup && matches;
  });
  const active = all.filter((s) => s.status === "running").length;
  const detail = selected ? all.find((s) => s.name === selected) : null;
  const bus = snap.bus || {};

  return html`
    <div class="shell">
      <div class="topbar">
        <div class="brand"><span class="mark">A</span>Aletheia · terminal</div>
        <nav class="tabs">
          <button class=${`tab ${tab === "services" ? "active" : ""}`}
                  onClick=${() => setTab("services")}>Services</button>
          <button class=${`tab ${tab === "config" ? "active" : ""}`}
                  onClick=${() => setTab("config")}>Config</button>
        </nav>
        <span class="spacer"></span>
        <span class="badge-daemon">
          <span class=${`dot ${online ? "running" : "crashed"}`}></span>
          daemon ${snap.daemon.version} · 127.0.0.1:7420
        </span>
      </div>

      ${tab === "config" ? html`<${ConfigTab} />` : html`
      <div class="body">
        <${Sidebar} services=${all} profiles=${snap.profiles} host=${snap.host}
                    group=${group} setGroup=${setGroup} />

        <main class="main">
          <header>
            <div>
              <h1>Services</h1>
              <p>${all.length} services au manifeste · échantillon toutes les 2 s</p>
            </div>
            <span class="spacer"></span>
            <div class="toolbar">
              <input placeholder="Filtrer les services…" value=${filter}
                     onInput=${(e) => setFilter(e.target.value)} />
              <div class="seg">
                <button class=${view === "grid" ? "active" : ""} onClick=${() => setView("grid")}>Grille</button>
                <button class=${view === "table" ? "active" : ""} onClick=${() => setView("table")}>Table</button>
              </div>
              <button class="primary" onClick=${() => post("/api/profiles/all/stop")}>Tout arrêter</button>
            </div>
          </header>

          <div class="tiles">
            <div class="tile">
              <span class="label">Actifs</span>
              <div class="v">${active}<small>/ ${all.length}</small></div>
            </div>
            <div class=${`tile ${bus.up ? "" : "down"}`}>
              <span class="label">Bus NATS</span>
              <div class="v">${bus.up ? "en ligne" : "absent"}</div>
            </div>
            <div class="tile">
              <span class="label">Débit du bus</span>
              <div class="v">${bus.up ? Math.round(bus.msgs_per_min) : "—"}<small>msg/min</small></div>
            </div>
            <div class="tile">
              <span class="label">Clients NATS</span>
              <div class="v">${bus.up ? bus.connections : "—"}</div>
            </div>
          </div>

          ${view === "grid"
            ? html`<div class="grid">
                ${visible.map((s) => html`
                  <${Card} key=${s.name} s=${s} selected=${selected === s.name} onSelect=${select} />`)}
              </div>`
            : html`<${Table} services=${visible} selected=${selected} onSelect=${select} />`}
        </main>

        ${detail && html`
          <${LogPanel} s=${detail} lines=${logs[detail.name] || []}
                       onClose=${() => setSelected(null)} />`}
      </div>`}
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
