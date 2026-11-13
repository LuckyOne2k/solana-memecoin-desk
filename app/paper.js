/*
 * Memecoin Desk – rein lesende Anzeige des Paper-Depots (paper_state.json).
 *
 * Einzige Datenquelle: paper_state.json im Branch desk-data. Nur Simulation,
 * kein echtes Geld, keine Wallet. Passt die Datei nicht (falscher Modus,
 * fehlende Felder), wird kein Depot angezeigt. Kein Offline-Speicher: ohne
 * Verbindung erscheint nur ein Hinweis.
 */
"use strict";

(function () {
  const PAPER_URL =
    "https://raw.githubusercontent.com/LuckyOne2k/solana-memecoin-desk/desk-data/paper_state.json";
  const SHADOW_URL =
    "https://raw.githubusercontent.com/LuckyOne2k/solana-memecoin-desk/desk-data/shadow_summary.json";
  const TIMEOUT_MS = 15000;
  const STATUS_TEXT = {
    ready_for_full_risk_screen: "Bereit",
    incomplete: "Unvollständig",
    rejected: "Abgelehnt",
  };
  const STALE_AFTER_MS = 2 * 60 * 60 * 1000;
  const MAX_CLOSED_SHOWN = 15;

  const EXIT_LABEL = {
    stop_loss: "Stop-Loss",
    trailing_stop: "Trailing-Stop",
    profit_lock: "Gewinnsicherung",
    security_rejected: "Sicherheitsprüfung abgelehnt",
    manual_exit: "Notausstieg",
    max_holding_time: "Maximale Haltedauer",
    price_unavailable_writeoff: "Kurs fehlt – abgeschrieben",
  };
  const STOP_KIND_LABEL = {
    profit_lock: "Gewinnsicherung",
    trailing: "Trailing",
  };
  const SKIP_LABEL = {
    open: "Gekauft",
    already_open: "Bereits im Depot",
    max_open_positions: "Depot voll",
    reentry_cooldown: "Sperrzeit nach Verkauf",
    price_moved_since_scan: "Kurs seit Scan zu stark bewegt",
    fresh_price_single_source: "Kurs nur aus einer Quelle",
    fresh_price_missing: "Kein aktueller Kurs",
    fresh_price_disagree: "Kursquellen widersprechen sich",
    size_below_minimum: "Position zu klein",
    scan_price_missing: "Scan-Kurs fehlt",
    scan_liquidity_missing: "Scan-Liquidität fehlt",
  };

  const $ = (id) => document.getElementById(id);

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function num(v) {
    return typeof v === "number" && isFinite(v);
  }

  function usd(v) {
    if (!num(v)) return "–";
    return v.toLocaleString("de-DE", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  }

  function price(v) {
    if (!num(v)) return "–";
    return "$" + v.toPrecision(4);
  }

  function pct(v) {
    if (!num(v)) return "–";
    return (v > 0 ? "+" : "") + v.toLocaleString("de-DE", { maximumFractionDigits: 1 }) + " %";
  }

  function signCls(v) {
    if (!num(v) || v === 0) return "";
    return v > 0 ? "pos" : "neg";
  }

  function when(iso) {
    const t = Date.parse(iso);
    if (!isFinite(t)) return "–";
    return new Date(t).toLocaleString("de-DE", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  }

  function validate(d) {
    if (!d || typeof d !== "object") return "kein_objekt";
    if (d.schema_version !== 1) return "schema_version";
    if (d.mode !== "paper" || d.live_trading !== false) return "modus";
    if (!num(d.cash_usd) || !num(d.starting_balance_usd)) return "geld";
    if (!Array.isArray(d.open_positions) || !Array.isArray(d.closed_trades)) return "listen";
    if (!Array.isArray(d.equity_history) || !d.stats || typeof d.stats !== "object") return "statistik";
    if (!isFinite(Date.parse(d.updated_at))) return "zeit";
    for (const p of d.open_positions) {
      if (!p || !num(p.entry_price_usd) || !num(p.token_amount) || !num(p.cost_basis_usd)) return "position";
    }
    return null;
  }

  function message(kind, text) {
    const box = $("paper-body");
    clear(box);
    box.appendChild(el("div", kind === "error" ? "empty empty-bad" : "empty", text));
  }

  function equity(d) {
    let value = d.cash_usd;
    for (const p of d.open_positions) {
      const mark = p.written_off ? 0 : num(p.last_price_usd) ? p.last_price_usd : p.entry_price_usd;
      value += p.token_amount * mark;
    }
    return value;
  }

  function sparkline(points) {
    const ns = "http://www.w3.org/2000/svg";
    const values = points.map((p) => p.equity_usd).filter(num);
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 300 60");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("class", "spark");
    svg.setAttribute("aria-hidden", "true");
    if (values.length < 2) return svg;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const coords = values.map((v, i) => {
      const x = (i / (values.length - 1)) * 300;
      const y = 56 - ((v - min) / span) * 52;
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", coords.join(" "));
    line.setAttribute("class", values[values.length - 1] >= values[0] ? "spark-up" : "spark-down");
    svg.appendChild(line);
    return svg;
  }

  function kpi(label, value, cls) {
    const box = el("div", "kpi kpi-paper");
    box.appendChild(el("span", "kpi-label", label));
    box.appendChild(el("span", "kpi-value kpi-small " + (cls || ""), value));
    return box;
  }

  function render(d) {
    const box = $("paper-body");
    clear(box);

    const total = equity(d);
    const result = ((total - d.starting_balance_usd) / d.starting_balance_usd) * 100;
    const age = Date.now() - Date.parse(d.updated_at);

    const meta = el("p", "kpi-meta" + (age > STALE_AFTER_MS ? " warn-text" : ""),
      `Stand: ${when(d.updated_at)}` + (age > STALE_AFTER_MS ? " – älter als 2 Std., Überwachung prüfen" : ""));
    box.appendChild(meta);

    const kpis = el("div", "kpis");
    kpis.appendChild(kpi("Depotwert", usd(total)));
    kpis.appendChild(kpi("Ergebnis", pct(result), signCls(result)));
    kpis.appendChild(kpi("Bargeld", usd(d.cash_usd)));
    box.appendChild(kpis);
    box.appendChild(sparkline(d.equity_history));

    const s = d.stats;
    const closed = num(s.trades_closed) ? s.trades_closed : 0;
    const rate = closed ? Math.round(((s.wins || 0) / closed) * 100) + " %" : "–";
    box.appendChild(el("p", "kpi-meta",
      `Abgeschlossen: ${closed} · Trefferquote: ${rate} · Realisiert: ${usd(s.realized_pnl_usd)}`));

    box.appendChild(el("h2", "section-title", `Offene Positionen (${d.open_positions.length})`));
    if (!d.open_positions.length) {
      box.appendChild(el("div", "empty", "Keine offene Position."));
    } else {
      const list = el("div", "pool-list");
      for (const p of d.open_positions) {
        const mark = num(p.last_price_usd) ? p.last_price_usd : null;
        const change = mark ? ((p.token_amount * mark - p.cost_basis_usd) / p.cost_basis_usd) * 100 : null;
        const row = el("div", "pool-row");
        const main = el("div", "pool-main");
        main.appendChild(el("div", "pool-symbol", p.token_symbol || "–"));
        main.appendChild(el("div", "pool-sub",
          `Kauf ${when(p.opened_at)} · ${usd(p.cost_basis_usd)} · Einstieg ${price(p.entry_price_usd)} · Stop ${price(p.current_stop_usd)}` +
          (STOP_KIND_LABEL[p.current_stop_kind] ? ` (${STOP_KIND_LABEL[p.current_stop_kind]})` : "") +
          (p.price_missing_since ? " · Kurs fehlt seit " + when(p.price_missing_since) : "")));
        row.appendChild(main);
        row.appendChild(el("span", "tag " + (signCls(change) === "pos" ? "tag-ready" : "tag-rejected"), pct(change)));
        list.appendChild(row);
      }
      box.appendChild(list);
    }

    const run = d.last_run;
    if (run && Array.isArray(run.entry_decisions) && run.entry_decisions.length) {
      box.appendChild(el("h2", "section-title", "Letzte Einstiegsprüfung"));
      const list = el("div", "pool-list");
      for (const e of run.entry_decisions) {
        const row = el("div", "pool-row");
        const main = el("div", "pool-main");
        main.appendChild(el("div", "pool-symbol", e.token_symbol || "–"));
        main.appendChild(el("div", "pool-sub", SKIP_LABEL[e.decision] || e.decision));
        row.appendChild(main);
        list.appendChild(row);
      }
      box.appendChild(list);
    }

    box.appendChild(el("h2", "section-title", "Letzte Verkäufe"));
    const trades = d.closed_trades.slice(-MAX_CLOSED_SHOWN).reverse();
    if (!trades.length) {
      box.appendChild(el("div", "empty", "Noch kein Verkauf."));
    } else {
      const list = el("div", "pool-list");
      for (const t of trades) {
        const row = el("div", "pool-row");
        const main = el("div", "pool-main");
        main.appendChild(el("div", "pool-symbol", t.token_symbol || "–"));
        main.appendChild(el("div", "pool-sub",
          `${when(t.closed_at)} · ${EXIT_LABEL[t.exit_reason] || t.exit_reason} · ${usd(t.pnl_usd)}`));
        row.appendChild(main);
        row.appendChild(el("span", "tag " + (signCls(t.pnl_pct) === "pos" ? "tag-ready" : "tag-rejected"), pct(t.pnl_pct)));
        list.appendChild(row);
      }
      box.appendChild(list);
    }
  }

  /* ---------- Schattendepot (nur Lernen) ---------- */

  function groupLabel(name) {
    if (STATUS_TEXT[name]) return STATUS_TEXT[name];
    const [status, code] = String(name).split(":");
    return (STATUS_TEXT[status] || status) + (code ? " · " + code : "");
  }

  function shadowRow(r) {
    const row = el("div", "pool-row");
    const main = el("div", "pool-main");
    main.appendChild(el("div", "pool-symbol", groupLabel(r.group)));
    const parts = [];
    for (const h of ["1h", "6h", "24h"]) {
      const s = r[h];
      if (s && num(s.mean_pct)) parts.push(`${h}: ${pct(s.mean_pct)} (${s.count}, ${s.positive_share_pct} % positiv)`);
    }
    main.appendChild(el("div", "pool-sub wrap", `${r.observations} Beobachtungen` + (parts.length ? " · " + parts.join(" · ") : " · noch keine Messung")));
    row.appendChild(main);
    const day = r["24h"];
    if (day && num(day.mean_pct))
      row.appendChild(el("span", "tag " + (day.mean_pct > 0 ? "tag-ready" : "tag-rejected"), pct(day.mean_pct)));
    return row;
  }

  function renderShadow(d) {
    const box = $("shadow-body");
    clear(box);
    box.appendChild(el("p", "kpi-meta",
      `${d.observations_total} Token beobachtet, Stand ${when(d.generated_at)}. Durchschnittliche Kursveränderung ohne Kosten – keine Kaufempfehlung.`));
    const list = el("div", "pool-list");
    for (const r of d.by_status) list.appendChild(shadowRow(r));
    box.appendChild(list);
    if (Array.isArray(d.by_reason) && d.by_reason.length) {
      box.appendChild(el("h2", "section-title", "Nach Grund"));
      const reasons = el("div", "pool-list");
      for (const r of d.by_reason.slice(0, 10)) reasons.appendChild(shadowRow(r));
      box.appendChild(reasons);
    }
  }

  async function loadShadow() {
    const box = $("shadow-body");
    let response;
    try {
      response = await fetch(SHADOW_URL + "?t=" + Date.now(), { cache: "no-store", credentials: "omit" });
    } catch (e) {
      clear(box);
      box.appendChild(el("div", "empty", "Schattendepot nicht abrufbar."));
      return;
    }
    let data = null;
    if (response.ok) {
      try {
        data = await response.json();
      } catch (e) {
        data = null;
      }
    }
    clear(box);
    if (response.status === 404) {
      box.appendChild(el("div", "empty", "Wird beim nächsten Scan angelegt."));
      return;
    }
    if (!data || data.purpose !== "learning_only_no_trading" || !Array.isArray(data.by_status)) {
      box.appendChild(el("div", "empty empty-bad", "Schattendaten fehlerhaft oder nicht abrufbar."));
      return;
    }
    renderShadow(data);
  }

  async function loadPaper() {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    let response;
    try {
      response = await fetch(PAPER_URL + "?t=" + Date.now(), {
        cache: "no-store",
        credentials: "omit",
        signal: ctrl.signal,
      });
    } catch (e) {
      message("error", "Depot nicht abrufbar (keine Verbindung).");
      return;
    } finally {
      clearTimeout(timer);
    }
    if (response.status === 404) {
      message("info", "Das Paper-Depot wird beim nächsten Scan angelegt.");
      return;
    }
    if (!response.ok) {
      message("error", `Depot nicht abrufbar (HTTP ${response.status}).`);
      return;
    }
    let data;
    try {
      data = await response.json();
    } catch (e) {
      message("error", "Depotdaten fehlerhaft (kein JSON).");
      return;
    }
    const problem = validate(data);
    if (problem) {
      message("error", `Depotdaten fehlerhaft (Grund: ${problem}). Es wird kein Depot angezeigt.`);
      return;
    }
    render(data);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const loadAll = () => {
      loadPaper();
      loadShadow();
    };
    $("refresh").addEventListener("click", loadAll);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") loadAll();
    });
    loadAll();
  });
})();
