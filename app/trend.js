/*
 * Trend-Bot – rein lesende Anzeige des Trend-Depots (trend_state.json).
 *
 * Einzige Datenquelle: trend_state.json im Branch desk-data. Nur Simulation,
 * kein echtes Geld, keine Wallet. Passt die Datei nicht (falscher Modus,
 * fehlende Felder), wird kein Depot angezeigt.
 */
"use strict";

(function () {
  const TREND_URL =
    "https://raw.githubusercontent.com/LuckyOne2k/solana-memecoin-desk/desk-data/trend_state.json";
  const TIMEOUT_MS = 15000;
  const STALE_AFTER_MS = 30 * 60 * 60 * 1000;

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

  function pct(v) {
    if (!num(v)) return "–";
    return (v > 0 ? "+" : "") + v.toLocaleString("de-DE", { maximumFractionDigits: 1 }) + " %";
  }

  function message(kind, text) {
    const box = $("trend-body");
    clear(box);
    box.appendChild(el("div", kind === "error" ? "empty empty-bad" : "empty", text));
  }

  function validate(d) {
    if (!d || typeof d !== "object") return "kein_objekt";
    if (d.schema_version !== 1) return "schema_version";
    if (d.mode !== "paper" || d.live_trading !== false) return "modus";
    if (!num(d.starting_balance_usd) || !num(d.sma_days)) return "werte";
    if (!d.products || typeof d.products !== "object") return "coins";
    if (!Array.isArray(d.equity_history) || !d.stats || typeof d.stats !== "object") return "statistik";
    if (!isFinite(Date.parse(d.updated_at))) return "zeit";
    for (const p of Object.values(d.products)) {
      if (!p || !num(p.cash_usd) || !num(p.units)) return "coin";
    }
    return null;
  }

  function coinValue(p) {
    const mark = num(p.last_close_usd) ? p.last_close_usd : p.entry_price_usd;
    return p.cash_usd + (p.units > 0 && num(mark) ? p.units * mark : 0);
  }

  function render(d) {
    const box = $("trend-body");
    clear(box);
    const coins = Object.entries(d.products);
    const total = coins.reduce((sum, [, p]) => sum + coinValue(p), 0);
    const result = ((total - d.starting_balance_usd) / d.starting_balance_usd) * 100;
    const age = Date.now() - Date.parse(d.updated_at);

    box.appendChild(el("p", "kpi-meta" + (age > STALE_AFTER_MS ? " warn-text" : ""),
      `Regel: Schluss über ${d.sma_days}-Tage-Durchschnitt · Stand: ` +
      new Date(Date.parse(d.updated_at)).toLocaleString("de-DE") +
      (age > STALE_AFTER_MS ? " – älter als 30 Std., Lauf prüfen" : "")));

    const kpis = el("div", "kpis");
    for (const [label, value, cls] of [
      ["Depotwert", usd(total), ""],
      ["Ergebnis", pct(result), result > 0 ? "pos" : result < 0 ? "neg" : ""],
      ["Verkäufe", String(num(d.stats.trades_closed) ? d.stats.trades_closed : 0), ""],
    ]) {
      const k = el("div", "kpi kpi-paper");
      k.appendChild(el("span", "kpi-label", label));
      k.appendChild(el("span", "kpi-value kpi-small " + cls, value));
      kpis.appendChild(k);
    }
    box.appendChild(kpis);

    const list = el("div", "pool-list");
    for (const [name, p] of coins) {
      const row = el("div", "pool-row");
      const main = el("div", "pool-main");
      main.appendChild(el("div", "pool-symbol", name.replace("-USD", "")));
      const s = p.signal;
      let sub = s && num(s.close_usd) && num(s.sma_usd)
        ? `Tag ${p.signal_day}: Schluss ${usd(s.close_usd)} · Ø ${usd(s.sma_usd)} · Wert ${usd(coinValue(p))}`
        : "Noch kein Signal";
      if (p.status === "incomplete") sub += " · unvollständig: " + (p.reason || "–");
      main.appendChild(el("div", "pool-sub wrap", sub));
      row.appendChild(main);
      row.appendChild(el("span", "tag " + (p.units > 0 ? "tag-ready" : "tag-incomplete"),
        p.units > 0 ? "Investiert" : "Bargeld"));
      list.appendChild(row);
    }
    box.appendChild(list);
  }

  async function loadTrend() {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    let response;
    try {
      response = await fetch(TREND_URL + "?t=" + Date.now(), {
        cache: "no-store",
        credentials: "omit",
        signal: ctrl.signal,
      });
    } catch (e) {
      message("error", "Trend-Depot nicht abrufbar (keine Verbindung).");
      return;
    } finally {
      clearTimeout(timer);
    }
    if (response.status === 404) {
      message("info", "Das Trend-Depot wird beim nächsten Lauf angelegt.");
      return;
    }
    if (!response.ok) {
      message("error", `Trend-Depot nicht abrufbar (HTTP ${response.status}).`);
      return;
    }
    let data;
    try {
      data = await response.json();
    } catch (e) {
      message("error", "Trend-Daten fehlerhaft (kein JSON).");
      return;
    }
    const problem = validate(data);
    if (problem) {
      message("error", `Trend-Daten fehlerhaft (Grund: ${problem}). Es wird kein Depot angezeigt.`);
      return;
    }
    render(data);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("refresh").addEventListener("click", loadTrend);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") loadTrend();
    });
    loadTrend();
  });
})();
