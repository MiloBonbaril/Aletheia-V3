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

// ---------- chat tab ----------

function ChatTab({ messages, connected }) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const box = useRef(null);
  const stuck = useRef(true);

  // On colle au bas, sauf si le lecteur est remonté lire un message précédent.
  useEffect(() => {
    if (stuck.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  });

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!response.ok) { setError(await response.text()); return; }
      setDraft("");
      stuck.current = true;
    } catch {
      setError("le daemon ne répond pas");
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };

  return html`
    <div class="body">
      <main class="main chat">
        <header>
          <div>
            <h1>Chat</h1>
            <p>publie sur <code>io.user.msg.text</code>, comme io_text</p>
          </div>
          <span class="spacer"></span>
          <span class="badge-daemon">
            <span class=${`dot ${connected ? "running" : "crashed"}`}></span>
            ${connected ? "connecté au bus" : "bus injoignable"}
          </span>
        </header>

        <div class="thread" ref=${box}
             onScroll=${(e) => {
               const t = e.currentTarget;
               stuck.current = t.scrollHeight - t.scrollTop - t.clientHeight < 40;
             }}>
          ${messages.length === 0
            ? html`<p class="empty">aucun message. Le tampon vit en mémoire et part avec le daemon.</p>`
            : messages.map((m) => html`
                <div class=${`msg ${m.role}`} key=${m.id}>
                  <span class="who">${m.role === "moi" ? "moi" : "aletheia"}</span>
                  <div class="txt">
                    ${m.text || (m.done ? html`<i class="silence">silence</i>` : "")}
                    ${!m.done && html`<span class="caret"></span>`}
                  </div>
                  <span class="at">${clock(m.t)}</span>
                </div>`)}
        </div>

        ${error && html`<p class="sendfail">${error}</p>`}

        <div class="composer">
          <textarea rows="3" placeholder=${connected ? "Entrée pour envoyer · Maj+Entrée pour un retour à la ligne" : "le daemon n'est pas connecté au bus"}
                    value=${draft} disabled=${!connected}
                    onKeyDown=${onKeyDown} onInput=${(e) => setDraft(e.target.value)}></textarea>
          <button class="primary" onClick=${send} disabled=${!connected || sending || !draft.trim()}>
            Envoyer
          </button>
        </div>
      </main>
    </div>`;
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
    setNote({
      kind: "ok",
      text: body.applied ? "enregistré · appliqué au prochain prompt" : "enregistré",
      service: body.service,  // absent quand le service suit le fichier tout seul
    });
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
                ${file.hot_reload
                  ? html`<b>${file.service}</b> relit ce fichier quand il change. Une modification
                         s'applique au prompt suivant, sans redémarrage.`
                  : html`<b>${file.service}</b> lit ce fichier à son démarrage. Une modification
                         s'applique au redémarrage du service.`}
              </p>`}`}
      </main>
    </div>`;
}

// ---------- kanban tab ----------

// Les cinq colonnes, dans l'ordre du flux. La clé est le slug ASCII écrit dans
// le fichier ; le libellé est ce que la console montre.
const COLUMNS = [
  ["en-attente", "En attente"],
  ["en-cours", "En cours"],
  ["termine", "Terminé"],
  ["bloque", "Bloqué"],
  ["amelioration-continue", "Amélioration continue"],
];
// La colonne des terminés grossit sans fin. On montre les plus récents et on
// déplie le reste : aucun fichier n'est déplacé, aucun ticket n'est archivé.
const DONE_SHOWN = 10;

const recent = (a, b) => (b.updated || "").localeCompare(a.updated || "") || b.id - a.id;

function TicketCard({ t, onDragStart, onOpen }) {
  return html`
    <article class="tk" draggable=${true} title=${t.file}
             onDragStart=${(e) => onDragStart(e, t)} onClick=${() => onOpen(t)}>
      <div class="t">${t.title}</div>
      <div class="meta">
        <span class="n">#${t.id}</span>
        ${t.service && html`<span class="tag">${t.service}</span>`}
        ${t.priority && t.priority !== "normale"
          && html`<span class=${`tag ${t.priority}`}>${t.priority}</span>`}
      </div>
    </article>`;
}

const PRIORITIES = ["haute", "normale", "basse"];

// Le panneau d'édition, sur le patron de l'onglet Config: un brouillon local,
// un bouton d'enregistrement, et le conflit visible au lieu d'un écrasement.
function TicketPanel({ t, onClose, onSaved, onDeleted }) {
  const [draft, setDraft] = useState(null);
  const [note, setNote] = useState(null);
  const [armed, setArmed] = useState(false);

  // `mtime` est figé à l'ouverture: c'est la version que l'on a sous les yeux.
  // Il ne suit pas les mises à jour du flux, sinon le garde-fou ne garde rien.
  useEffect(() => {
    setDraft({
      title: t.title, service: t.service || "", priority: t.priority || "",
      body: t.body, mtime: t.mtime,
    });
    setNote(null);
    setArmed(false);
  }, [t.id]);

  if (!draft) return null;
  const set = (key, value) => { setArmed(false); setDraft({ ...draft, [key]: value }); };

  const save = async () => {
    let response;
    try {
      response = await fetch(`/api/tickets/${t.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          changes: { title: draft.title, service: draft.service, priority: draft.priority },
          body: draft.body,
          mtime: draft.mtime,
        }),
      });
    } catch {
      setNote({ kind: "err", text: "le daemon ne répond pas" });
      return;
    }
    const payload = await response.json().catch(() => ({}));
    if (response.status === 409 && payload.ticket) {
      onSaved(payload.ticket);
      setNote({ kind: "warn", text: "le ticket a changé sur le disque depuis l'ouverture", disk: payload.ticket });
      return;
    }
    if (!response.ok) {
      setNote({ kind: "err", text: "écriture refusée par le daemon" });
      return;
    }
    onSaved(payload.ticket);
    setDraft({ ...draft, mtime: payload.ticket.mtime });
    setNote({ kind: "ok", text: "enregistré" });
  };

  // Deux temps plutôt qu'une boîte de dialogue: le premier clic arme, le second
  // supprime. Un navigateur qui refuse `confirm` rendrait le bouton inerte.
  const remove = async () => {
    if (!armed) { setArmed(true); return; }
    const response = await fetch(`/api/tickets/${t.id}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mtime: draft.mtime }),
    }).catch(() => null);
    if (!response || !response.ok) {
      setNote({ kind: "err", text: "suppression refusée · le ticket a changé sur le disque" });
      return;
    }
    onDeleted(t.id);
  };

  const takeDisk = (disk) => {
    setDraft({
      title: disk.title, service: disk.service || "", priority: disk.priority || "",
      body: disk.body, mtime: disk.mtime,
    });
    setNote(null);
  };

  return html`
    <aside class="tkedit">
      <header>
        <h2>#${t.id}<button class="close" onClick=${onClose} title="fermer">✕</button></h2>
        <div class="meta">${t.file} · créé le ${t.created} · modifié le ${t.updated}</div>
      </header>
      <div class="form">
        <label>
          <span class="label">Titre</span>
          <input value=${draft.title} onInput=${(e) => set("title", e.target.value)} />
        </label>
        <div class="pair">
          <label>
            <span class="label">Service</span>
            <input value=${draft.service} placeholder="aucun"
                   onInput=${(e) => set("service", e.target.value)} />
          </label>
          <label>
            <span class="label">Priorité</span>
            <select value=${draft.priority} onChange=${(e) => set("priority", e.target.value)}>
              <option value="">aucune</option>
              ${PRIORITIES.map((p) => html`<option key=${p} value=${p}>${p}</option>`)}
            </select>
          </label>
        </div>
        <label class="grow">
          <span class="label">Corps</span>
          <textarea spellcheck=${false} value=${draft.body}
                    onInput=${(e) => set("body", e.target.value)}></textarea>
        </label>
      </div>
      <div class=${`status ${note ? note.kind : ""}`}>
        ${note ? html`
            <span>${note.text}</span>
            ${note.disk && html`
              <button onClick=${() => takeDisk(note.disk)}>Prendre la version du disque</button>`}`
          : html`<span>le statut se change en glissant la carte</span>`}
        <span class="spacer"></span>
        <button class=${`danger ${armed ? "armed" : ""}`} onClick=${remove}>
          ${armed ? "Confirmer la suppression" : "Supprimer"}
        </button>
        <button class="primary" onClick=${save}>Enregistrer</button>
      </div>
    </aside>`;
}

function KanbanTab({ tickets, setTickets }) {
  const [service, setService] = useState("*");
  const [allDone, setAllDone] = useState(false);
  const [note, setNote] = useState(null);
  const [over, setOver] = useState(null);
  const [openId, setOpenId] = useState(null);
  const dragged = useRef(null);
  const open = tickets.find((t) => t.id === openId) || null;
  const services = [...new Set(tickets.map((t) => t.service).filter(Boolean))].sort();
  const visible = service === "*" ? tickets : tickets.filter((t) => t.service === service);

  const replace = (ticket) => setTickets((prev) => prev.map((t) => (t.id === ticket.id ? ticket : t)));

  // Le board donne l'identifiant, la date et le nom du fichier. Le ticket naît
  // avec un titre d'attente, et le panneau s'ouvre pour l'écrire: pas de boîte
  // de dialogue, qu'un navigateur embarqué peut refuser d'afficher.
  const create = async () => {
    const response = await fetch("/api/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes: { title: "Nouveau ticket" } }),
    }).catch(() => null);
    const payload = response ? await response.json().catch(() => ({})) : {};
    if (!response || !response.ok) {
      setNote({ kind: "err", text: "création refusée par le daemon" });
      return;
    }
    setTickets((prev) => [...prev, payload.ticket]);
    setOpenId(payload.ticket.id);
    setNote(null);
  };

  const onDragStart = (e, t) => {
    dragged.current = t;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(t.id));  // Firefox refuse un glissement sans données
  };

  // La carte saisie ne sert qu'une fois. Sans cela, un fichier lâché sur une
  // colonne depuis le bureau déplacerait la carte du glissement précédent.
  const take = () => {
    const t = dragged.current;
    dragged.current = null;
    return t;
  };

  // `mtime` est la version que le navigateur a lue. Le daemon refuse d'écrire
  // par-dessus une version qu'il n'a jamais montrée: un agent écrit le même
  // fichier depuis une autre session.
  const move = async (t, status) => {
    if (!t || t.status === status) return;
    let response;
    try {
      response = await fetch(`/api/tickets/${t.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes: { status }, mtime: t.mtime }),
      });
    } catch {
      setNote({ kind: "err", text: "le daemon ne répond pas" });
      return;
    }
    const body = await response.json().catch(() => ({}));
    if (response.status === 409 && body.ticket) {
      replace(body.ticket);
      setNote({ kind: "warn", text: `#${t.id} a changé sur le disque · la carte reprend la version du disque` });
      return;
    }
    if (!response.ok) {
      setNote({ kind: "err", text: `déplacement de #${t.id} refusé par le daemon` });
      return;
    }
    replace(body.ticket);
    setNote(null);
  };

  return html`
    <div class="body">
    <main class="main kanban">
      <header>
        <div>
          <h1>Kanban</h1>
          <p>${tickets.length} tickets · le dossier <code>tickets/</code> à la racine du dépôt</p>
        </div>
        <span class="spacer"></span>
        <div class="toolbar">
          ${note && html`<span class=${`tknote ${note.kind}`}>${note.text}</span>`}
          <select value=${service} onChange=${(e) => setService(e.target.value)}>
            <option value="*">tous les services</option>
            ${services.map((s) => html`<option key=${s} value=${s}>${s}</option>`)}
          </select>
          <button class="primary" onClick=${create}>+ Ticket</button>
        </div>
      </header>

      ${tickets.length === 0
        ? html`<p class="empty">aucun ticket dans <code>tickets/</code>. Voir docs/agents/tickets.md.</p>`
        : html`
        <div class="cols">
          ${COLUMNS.map(([status, libelle]) => {
            const found = visible.filter((t) => t.status === status);
            const done = status === "termine";
            const shown = done ? [...found].sort(recent) : found;
            const hidden = done && !allDone ? Math.max(0, shown.length - DONE_SHOWN) : 0;
            return html`
              <section key=${status} class=${`col ${status} ${over === status ? "over" : ""}`}
                       onDragOver=${(e) => { e.preventDefault(); if (over !== status) setOver(status); }}
                       onDragLeave=${() => setOver((c) => (c === status ? null : c))}
                       onDrop=${(e) => { e.preventDefault(); setOver(null); move(take(), status); }}>
                <header>
                  <span class="label">${libelle}</span>
                  <span class="n">${found.length}</span>
                </header>
                <div class="stack">
                  ${(hidden ? shown.slice(0, DONE_SHOWN) : shown).map((t) => html`
                    <${TicketCard} key=${t.id} t=${t} onDragStart=${onDragStart}
                                   onOpen=${() => setOpenId(t.id)} />`)}
                  ${hidden > 0 && html`
                    <button class="more" onClick=${() => setAllDone(true)}>+${hidden} de plus</button>`}
                  ${done && allDone && found.length > DONE_SHOWN && html`
                    <button class="more" onClick=${() => setAllDone(false)}>replier</button>`}
                </div>
              </section>`;
          })}
        </div>`}
    </main>
    ${open && html`
      <${TicketPanel} key=${open.id} t=${open} onClose=${() => setOpenId(null)}
                      onSaved=${replace}
                      onDeleted=${(id) => {
                        setTickets((prev) => prev.filter((t) => t.id !== id));
                        setOpenId(null);
                      }} />`}
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
  const [chat, setChat] = useState([]);
  const [tickets, setTickets] = useState([]);
  const [online, setOnline] = useState(false);

  useEffect(() => {
    // Le tampon du daemon existe avant l'ouverture de l'onglet: on le rattrape.
    fetch("/api/chat").then((r) => r.json()).then((d) => setChat(d.messages)).catch(() => {});
    fetch("/api/tickets").then((r) => r.json()).then((d) => setTickets(d.tickets)).catch(() => {});
    const source = new EventSource("/api/stream");
    source.onopen = () => setOnline(true);
    source.onerror = () => setOnline(false);
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type === "state") {
        setOnline(true);
        setSnap(payload);
      } else if (payload.type === "chat") {
        // Le daemon renvoie le message entier à chaque fragment: on remplace par id.
        setChat((prev) => {
          const at = prev.findIndex((m) => m.id === payload.message.id);
          if (at === -1) return [...prev, payload.message];
          const next = [...prev];
          next[at] = payload.message;
          return next;
        });
      } else if (payload.type === "tickets") {
        // Le daemon n'émet cet événement que lorsque le dossier a bougé.
        setTickets(payload.tickets);
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
          <button class=${`tab ${tab === "chat" ? "active" : ""}`}
                  onClick=${() => setTab("chat")}>Chat</button>
          <button class=${`tab ${tab === "kanban" ? "active" : ""}`}
                  onClick=${() => setTab("kanban")}>Kanban</button>
          <button class=${`tab ${tab === "config" ? "active" : ""}`}
                  onClick=${() => setTab("config")}>Config</button>
        </nav>
        <span class="spacer"></span>
        <span class="badge-daemon">
          <span class=${`dot ${online ? "running" : "crashed"}`}></span>
          daemon ${snap.daemon.version} · 127.0.0.1:7420
        </span>
      </div>

      ${tab === "config" ? html`<${ConfigTab} />`
        : tab === "kanban" ? html`<${KanbanTab} tickets=${tickets} setTickets=${setTickets} />`
        : tab === "chat" ? html`<${ChatTab} messages=${chat} connected=${(snap.chat || {}).connected} />`
        : html`
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
