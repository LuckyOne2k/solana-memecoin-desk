/*
 * Memecoin Desk – rein lesende Anzeige von latest.json.
 *
 * Einzige Datenquelle: latest.json im Branch desk-data.
 * Keine Schreibzugriffe, keine Wallet, keine Neubewertung: Status, Gründe
 * und Zahlen werden nur angezeigt. Passt etwas nicht zusammen, erscheint
 * „Daten fehlerhaft“ und kein Token als bereit.
 *
 * Ohne Verbindung darf der zuletzt erfolgreich geladene Stand angezeigt
 * werden, aber nur deutlich als „Offline – Daten möglicherweise veraltet“.
 */
"use strict";

const DATA_URL =
  "https://raw.githubusercontent.com/LuckyOne2k/solana-memecoin-desk/desk-data/latest.json";
const CACHE_KEY = "desk.lastGood.v1";
const TIMEOUT_MS = 15000;
const STALE_AFTER_MS = 24 * 60 * 60 * 1000;

const READY = "ready_for_full_risk_screen";
const STATUSES = ["rejected", "incomplete", READY];
const SCAN_STATUSES = ["ok", "failed", "log_unreadable", "inconsistent"];
const COUNT_FIELDS = [
  "total_pools",
  "rejected_pools",
  "incomplete_pools",
  "ready_for_full_risk_screen",
];

const STATUS_LABEL = {
  [READY]: "Bereit",
  incomplete: "Unvollständig",
  rejected: "Abgelehnt",
};
const STATUS_CLASS = {
  [READY]: "ready",
  incomplete: "incomplete",
  rejected: "rejected",
};

// Nur Übersetzungen für die Anzeige. Unbekannte Codes erscheinen unverändert.
const REASON_LABEL = {
  lp_burn_or_lock_not_verified: "LP-Verbrennung/-Sperre nicht nachgewiesen",
  market_cap_below_minimum: "Marktkapitalisierung unter Minimum",
  market_cap_above_maximum: "Marktkapitalisierung über Maximum",
  market_cap_out_of_range: "Marktkapitalisierung außerhalb des Bereichs",
  no_24h_trading_activity: "Kein Handel in den letzten 24 h",
  liquidity_below_minimum: "Liquidität unter Minimum",
  liquidity_below_minimum_cross_checked: "Liquidität unter Minimum (abgeglichen)",
  liquidity_missing_or_invalid: "Liquidität fehlt oder ungültig",
  pair_age_out_of_range: "Pool-Alter außerhalb des Bereichs",
  created_at_missing_or_invalid: "Erstellungszeitpunkt fehlt oder ungültig",
  excluded_stablecoin: "Stablecoin ausgeschlossen",
  excluded_staking_token: "Staking-Token ausgeschlossen",
  excluded_no_memecoin_in_pair: "Kein Memecoin im Paar",
  freeze_authority_not_revoked: "Freeze-Authority nicht entzogen",
  mint_authority_not_revoked: "Mint-Authority nicht entzogen",
  top10_share_too_high: "Top-10-Anteil zu hoch",
  dev_share_too_high: "Entwickler-Anteil zu hoch",
};
const CHECK_LABEL = {
  market_cap_usd: "Marktkapitalisierung",
  top10_share_pct: "Top-10-Anteil",
  dev_share_pct: "Entwickler-Anteil",
  permanent_delegate_absent: "Kein Permanent Delegate",
  token_extensions_safe: "Token-Erweiterungen unbedenklich",
  liquidity_cross_check: "Liquiditätsvergleich zwischen Quellen",
  mint_authority_revoked: "Mint-Authority entzogen",
  freeze_authority_revoked: "Freeze-Authority entzogen",
  lp_burned_or_locked: "LP verbrannt oder gesperrt",
  unambiguous_base_token: "Eindeutiger Basis-Token",
};
const SECURITY_LABEL = {
  completed: "Sicherheitsprüfung abgeschlossen",
  skipped_run_limit: "Nicht geprüft (höchstens 10 Token je Lauf)",
  skipped_missing_mint: "Nicht geprüft (Token-Adresse fehlt)",
  skipped_pool_rejected: "Nicht geprüft (Pool vorab abgelehnt)",
  skipped_security_rejection: "Übersprungen (andere Prüfung abgelehnt)",
};

const SCAN_STATUS_VIEW = {
  ok: { pill: "Scan OK", cls: "pill-ok", text: "" },
  failed: {
    pill: "Scan fehlgeschlagen",
    cls: "pill-bad",
    text: "Der letzte Scan-Lauf war nicht erfolgreich. Es werden keine Token angezeigt.",
  },
  log_unreadable: {
    pill: "Protokoll nicht lesbar",
    cls: "pill-bad",
    text: "Das Scan-Protokoll konnte nicht gelesen werden. Es werden keine Token angezeigt.",
  },
  inconsistent: {
    pill: "Daten widersprüchlich",
    cls: "pill-warn",
    text: "Die Scan-Daten widersprechen sich. Es werden keine Token angezeigt.",
  },
};

const $ = (id) => document.getElementById(id);
let current = null; // { data, loadedAt, offline }
let filter = "all";

/* ---------- Prüfung der Datei (nur Plausibilität, keine Neubewertung) ---------- */

function isCount(v) {
  return Number.isInteger(v) && v >= 0;
}
function isStrList(v) {
  return Array.isArray(v) && v.every((x) => typeof x === "string");
}
function isText(v) {
  return typeof v === "string" || v === null || v === undefined;
}
function validTime(v) {
  return typeof v === "string" && !Number.isNaN(Date.parse(v));
}

/** Gibt null zurück, wenn die Datei passt, sonst einen Fehlergrund. */
function validate(d) {
  if (!d || typeof d !== "object" || Array.isArray(d)) return "kein_objekt";
  if (d.schema_version !== 1) return "unbekannte_version";
  if (d.live_trading !== false) return "live_trading_nicht_false";
  if (!SCAN_STATUSES.includes(d.scan_status)) return "unbekannter_scan_status";
  if (!Array.isArray(d.ready_tokens) || !Array.isArray(d.pools)) return "listen_fehlen";
  if (!validTime(d.generated_at)) return "zeit_ungueltig";

  if (d.scan_status !== "ok") {
    // Nicht ok: es dürfen keine Token oder Pools mitkommen.
    if (d.ready_tokens.length || d.pools.length) return "token_trotz_fehlerstatus";
    return null;
  }

  if (!validTime(d.scan_at)) return "scan_zeit_ungueltig";
  const c = d.counts;
  if (!c || typeof c !== "object") return "zahlen_fehlen";
  if (!COUNT_FIELDS.every((f) => isCount(c[f]))) return "zahl_ungueltig";
  if (c.rejected_pools + c.incomplete_pools + c.ready_for_full_risk_screen !== c.total_pools)
    return "summe_falsch";
  if (d.pools.length > c.total_pools) return "zu_viele_pools";
  if (d.pools_not_listed !== c.total_pools - d.pools.length) return "nicht_gelistet_falsch";

  const per = { rejected: 0, incomplete: 0, [READY]: 0 };
  for (const p of d.pools) {
    if (!p || typeof p !== "object") return "pool_ungueltig";
    if (!STATUSES.includes(p.status)) return "pool_status_unbekannt";
    if (!isStrList(p.rejection_reasons) || !isStrList(p.missing_checks)) return "gruende_ungueltig";
    for (const f of ["token_symbol", "token_name", "token_address", "pool_address", "dex_name", "security_check_status"])
      if (!isText(p[f])) return "textfeld_ungueltig";
    if (p.status === READY && (p.rejection_reasons.length || p.missing_checks.length))
      return "bereit_mit_offenen_pruefungen";
    per[p.status] += 1;
  }
  if (per.rejected > c.rejected_pools || per.incomplete > c.incomplete_pools) return "anzahl_falsch";
  if (per[READY] !== c.ready_for_full_risk_screen) return "anzahl_bereit_falsch";

  // Die Liste bereiter Token muss genau den bereiten Pools entsprechen.
  const readyPools = d.pools.filter((p) => p.status === READY).map((p) => p.pool_address);
  const readyTokens = d.ready_tokens.map((t) => t && t.pool_address);
  if (readyPools.length !== readyTokens.length) return "bereit_liste_falsch";
  if (readyPools.some((a, i) => a !== readyTokens[i])) return "bereit_liste_falsch";
  return null;
}

/* ---------- Laden ---------- */

function readCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const entry = JSON.parse(raw);
    if (!entry || validate(entry.data) !== null || !validTime(entry.loadedAt)) return null;
    return entry;
  } catch (e) {
    return null;
  }
}

function writeCache(entry) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(entry));
  } catch (e) {
    /* Speicher nicht verfügbar: dann eben ohne Offline-Stand. */
  }
}

async function load() {
  const btn = $("refresh");
  btn.classList.add("loading");
  btn.disabled = true;

  let response;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    response = await fetch(DATA_URL + "?t=" + Date.now(), {
      cache: "no-store",
      credentials: "omit",
      signal: ctrl.signal,
    });
  } catch (e) {
    // Keine Verbindung oder Zeitüberschreitung.
    showOffline();
    return finish();
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    // Server antwortet, aber ohne Datei: kein alter Stand, klare Meldung.
    showError(
      "Daten nicht abrufbar",
      `Der Server hat die Datei nicht geliefert (HTTP ${response.status}). Es werden keine Token angezeigt.`
    );
    return finish();
  }

  let data;
  try {
    data = await response.json();
  } catch (e) {
    showError("Daten fehlerhaft", "Die Datei ist kein gültiges JSON. Es werden keine Token angezeigt.", "kein_json");
    return finish();
  }

  const problem = validate(data);
  if (problem) {
    showError("Daten fehlerhaft", "Die Datei ist unvollständig oder widersprüchlich. Es werden keine Token angezeigt.", problem);
    return finish();
  }

  const entry = { data, loadedAt: new Date().toISOString() };
  writeCache(entry);
  current = { ...entry, offline: false };
  render();
  return finish();
}

function finish() {
  const btn = $("refresh");
  btn.classList.remove("loading");
  btn.disabled = false;
}

/* ---------- Anzeige ---------- */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function banner(kind, title, text, withRetry) {
  const b = el("div", "banner banner-" + kind);
  b.appendChild(el("strong", "", title));
  if (text) b.appendChild(el("span", "", text));
  if (withRetry) {
    b.appendChild(el("br"));
    const btn = el("button", "", "Erneut versuchen");
    btn.type = "button";
    btn.addEventListener("click", load);
    b.appendChild(btn);
  }
  $("banners").appendChild(b);
}

function formatTime(iso) {
  return new Date(iso).toLocaleString("de-DE", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatAge(ms) {
  if (ms < 0) ms = 0;
  const min = Math.floor(ms / 60000);
  if (min < 1) return "gerade eben";
  if (min < 60) return `vor ${min} Min.`;
  const h = Math.floor(min / 60);
  if (h < 48) return `vor ${h} Std. ${min % 60} Min.`;
  return `vor ${Math.floor(h / 24)} Tagen`;
}

function hideDashboard() {
  $("dashboard").hidden = true;
  clear($("ready-list"));
  clear($("pool-list"));
  for (const id of ["kpi-ready", "kpi-incomplete", "kpi-rejected"]) $(id).textContent = "–";
  $("kpi-meta").textContent = "";
}

function setStatus(pillText, pillCls, text) {
  const pill = $("status-pill");
  pill.textContent = pillText;
  pill.className = "pill " + pillCls;
  $("status-text").textContent = text || "";
}

function showOffline() {
  const cached = readCache();
  if (!cached) {
    current = null;
    document.body.classList.remove("stale");
    clear($("banners"));
    banner("red", "Keine Verbindung", "Die Daten konnten nicht geladen werden. Prüfe die Internetverbindung.", true);
    setStatus("Keine Daten", "pill-neutral", "");
    $("scan-time").textContent = "";
    $("scan-age").textContent = "";
    hideDashboard();
    $("loaded-at").textContent = "";
    return;
  }
  current = { ...cached, offline: true };
  render();
}

function showError(title, text, code) {
  current = null;
  document.body.classList.remove("stale");
  clear($("banners"));
  banner("red", title, text + (code ? ` (Grund: ${code})` : ""), true);
  setStatus(title, "pill-bad", "");
  $("scan-time").textContent = "";
  $("scan-age").textContent = "";
  hideDashboard();
  $("loaded-at").textContent = "";
}

function render() {
  const { data, loadedAt, offline } = current;
  clear($("banners"));
  document.body.classList.toggle("stale", offline);

  if (offline) {
    const b = el("div", "banner banner-offline");
    b.appendChild(el("strong", "", "Offline – Daten möglicherweise veraltet"));
    b.appendChild(el("span", "", `Angezeigt wird der zuletzt geladene Stand vom ${formatTime(loadedAt)}.`));
    b.appendChild(el("br"));
    const retry = el("button", "", "Erneut versuchen");
    retry.type = "button";
    retry.addEventListener("click", load);
    b.appendChild(retry);
    $("banners").appendChild(b);
  }

  const view = SCAN_STATUS_VIEW[data.scan_status];
  let pillText = view.pill;
  let pillCls = view.cls;
  if (offline) {
    pillText = "Offline – möglicherweise veraltet";
    pillCls = "pill-warn";
  }
  let text = view.text;
  if (data.status_reason) text += ` (Grund: ${data.status_reason})`;
  setStatus(pillText, pillCls, text);

  const when = data.scan_status === "ok" ? data.scan_at : data.generated_at;
  $("scan-time").textContent = (data.scan_status === "ok" ? "Scan: " : "Erzeugt: ") + formatTime(when);
  updateAge();

  $("loaded-at").textContent = `Geladen: ${formatTime(loadedAt)}`;

  if (data.scan_status !== "ok") {
    hideDashboard();
    return;
  }
  $("dashboard").hidden = false;
  renderCounts(data);
  renderReady(data);
  renderPools(data);
}

function updateAge() {
  const ageEl = $("scan-age");
  if (!current || current.data.scan_status !== "ok") {
    ageEl.textContent = "";
    ageEl.classList.remove("old");
    const old = document.getElementById("age-warning");
    if (old) old.remove();
    return;
  }
  const age = Date.now() - Date.parse(current.data.scan_at);
  ageEl.textContent = formatAge(age);
  const tooOld = age > STALE_AFTER_MS;
  ageEl.classList.toggle("old", tooOld);
  const existing = document.getElementById("age-warning");
  if (tooOld && !existing) {
    const b = el("div", "banner banner-yellow");
    b.id = "age-warning";
    b.appendChild(el("strong", "", "Scan älter als 24 Stunden"));
    b.appendChild(el("span", "", "Die Werte können sich inzwischen geändert haben. Starte bei Bedarf einen neuen Scan."));
    $("banners").appendChild(b);
  } else if (!tooOld && existing) {
    existing.remove();
  }
}

function renderCounts(d) {
  const c = d.counts;
  $("kpi-ready").textContent = c.ready_for_full_risk_screen;
  $("kpi-incomplete").textContent = c.incomplete_pools;
  $("kpi-rejected").textContent = c.rejected_pools;
  const total = c.total_pools || 1;
  const segs = $("kpi-bar").children;
  segs[0].style.width = (100 * c.ready_for_full_risk_screen) / total + "%";
  segs[1].style.width = (100 * c.incomplete_pools) / total + "%";
  segs[2].style.width = (100 * c.rejected_pools) / total + "%";
  let meta = `${c.total_pools} Pools gesamt`;
  if (d.pools_not_listed > 0) meta += ` · ${d.pools_not_listed} nicht einzeln aufgeführt`;
  $("kpi-meta").textContent = meta;
}

function shortAddr(a) {
  if (typeof a !== "string" || a.length < 12) return a || "–";
  return a.slice(0, 4) + "…" + a.slice(-4);
}

function initials(sym) {
  return (typeof sym === "string" && sym ? sym : "?").slice(0, 3).toUpperCase();
}

function renderReady(d) {
  const list = $("ready-list");
  clear(list);
  const ready = d.pools.filter((p) => p.status === READY);
  if (!ready.length) {
    list.appendChild(el("div", "empty", "Aktuell ist kein Token bereit."));
    return;
  }
  for (const p of ready) {
    const card = el("button", "ready-card");
    card.type = "button";
    card.appendChild(el("div", "avatar", initials(p.token_symbol)));
    const main = el("div", "ready-main");
    main.appendChild(el("div", "ready-symbol", p.token_symbol || "–"));
    main.appendChild(el("div", "ready-name", p.token_name || ""));
    main.appendChild(el("div", "ready-meta", `${p.dex_name || "–"} · ${shortAddr(p.token_address)}`));
    card.appendChild(main);
    card.appendChild(el("span", "ready-badge", "Bereit"));
    card.addEventListener("click", () => openSheet(p));
    list.appendChild(card);
  }
}

function renderPools(d) {
  const list = $("pool-list");
  clear(list);
  const order = { [READY]: 0, incomplete: 1, rejected: 2 };
  const pools = d.pools
    .filter((p) => filter === "all" || p.status === filter)
    .slice()
    .sort((a, b) => order[a.status] - order[b.status]);
  if (!pools.length) {
    list.appendChild(el("div", "empty", "Keine Pools in dieser Ansicht."));
    return;
  }
  for (const p of pools) {
    const cls = STATUS_CLASS[p.status];
    const row = el("button", "pool-row");
    row.type = "button";
    row.appendChild(el("span", "dot dot-" + cls));
    const main = el("div", "pool-main");
    main.appendChild(el("div", "pool-symbol", p.token_symbol || "–"));
    const n = p.rejection_reasons.length + p.missing_checks.length;
    const sub = `${p.dex_name || "–"} · ${p.token_name || ""}` + (n ? ` · ${n} ${n === 1 ? "Punkt" : "Punkte"}` : "");
    main.appendChild(el("div", "pool-sub", sub));
    row.appendChild(main);
    row.appendChild(el("span", "tag tag-" + cls, STATUS_LABEL[p.status]));
    row.appendChild(el("span", "chev", "›"));
    row.addEventListener("click", () => openSheet(p));
    list.appendChild(row);
  }
}

function detail(label, valueNode) {
  const g = el("div", "detail-group");
  g.appendChild(el("div", "detail-label", label));
  g.appendChild(valueNode);
  return g;
}

function reasonList(codes, labels, cls) {
  const ul = el("ul", "reason-list");
  for (const code of codes) {
    const li = el("li", cls, labels[code] || code);
    if (labels[code]) li.appendChild(el("span", "reason-code", code));
    ul.appendChild(li);
  }
  return ul;
}

function openSheet(p) {
  $("sheet-title").textContent = p.token_symbol || "–";
  const body = $("sheet-body");
  clear(body);

  const st = el("span", "tag tag-" + STATUS_CLASS[p.status], STATUS_LABEL[p.status]);
  body.appendChild(detail("Status", st));
  if (current && current.offline)
    body.appendChild(detail("Hinweis", el("div", "detail-value", "Offline – Daten möglicherweise veraltet")));
  body.appendChild(detail("Name", el("div", "detail-value", p.token_name || "–")));
  body.appendChild(detail("Börse", el("div", "detail-value", p.dex_name || "–")));
  const sec = p.security_check_status;
  body.appendChild(detail("Sicherheitsprüfung", el("div", "detail-value", SECURITY_LABEL[sec] || sec || "–")));

  if (p.rejection_reasons.length)
    body.appendChild(detail("Ablehnungsgründe", reasonList(p.rejection_reasons, REASON_LABEL, "bad")));
  if (p.missing_checks.length)
    body.appendChild(detail("Offene Prüfungen", reasonList(p.missing_checks, CHECK_LABEL, "open")));
  if (p.status === READY)
    body.appendChild(detail("Prüfungen", el("div", "detail-value", "Alle Prüfungen bestätigt. Keine Kaufempfehlung.")));

  body.appendChild(detail("Token-Adresse", el("div", "mono", p.token_address || "–")));
  body.appendChild(detail("Pool-Adresse", el("div", "mono", p.pool_address || "–")));

  $("sheet").hidden = false;
}

function closeSheet() {
  $("sheet").hidden = true;
}

/* ---------- Start ---------- */

document.addEventListener("DOMContentLoaded", () => {
  $("refresh").addEventListener("click", load);
  $("filters").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    filter = chip.dataset.filter;
    for (const c of $("filters").children) c.classList.toggle("active", c === chip);
    if (current && current.data.scan_status === "ok") renderPools(current.data);
  });
  $("sheet").addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) closeSheet();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeSheet();
  });
  // Beim Zurückkehren in die App neu laden, Alter jede Minute aktualisieren.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") load();
  });
  setInterval(updateAge, 60000);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  load();
});
