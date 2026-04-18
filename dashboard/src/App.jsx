import React, { useState, useEffect, useCallback } from "react";

const API_BASE = "http://localhost:8000";
const WS_URL = "ws://localhost:8000/ws";

// ============ DESIGN TOKENS ============
// Aesthetic: Bloomberg Terminal meets brutalist quant dashboard
// Dark, data-dense, monospace primary, red/green as PnL language

const theme = {
  bg: "#0a0a0a",
  bgElevated: "#111111",
  bgPanel: "#161616",
  border: "#262626",
  borderActive: "#3f3f3f",
  text: "#e5e5e5",
  textDim: "#737373",
  textFaint: "#525252",
  green: "#22c55e",
  greenDim: "#16a34a",
  red: "#ef4444",
  redDim: "#dc2626",
  yellow: "#eab308",
  blue: "#3b82f6",
  amber: "#f59e0b",
  mono: "'JetBrains Mono', 'SF Mono', 'Menlo', monospace",
  display: "'Space Grotesk', 'Inter', sans-serif",
};

// ============ UTILS ============
const fmt = {
  usd: (n) => (n == null ? "—" : `$${Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`),
  pct: (n) => (n == null ? "—" : `${Number(n).toFixed(2)}%`),
  pctSigned: (n) => {
    if (n == null) return "—";
    const v = Number(n);
    return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  },
  num: (n, d = 2) => (n == null ? "—" : Number(n).toFixed(d)),
  time: (iso) => {
    if (!iso) return "—";
    return new Date(iso).toLocaleTimeString("en-US", { hour12: false });
  },
};

const pnlColor = (v) => (v > 0 ? theme.green : v < 0 ? theme.red : theme.textDim);

// ============ COMPONENTS ============
const Pill = ({ children, color = "default" }) => {
  const colors = {
    default: { bg: "#262626", fg: theme.text },
    green: { bg: "rgba(34,197,94,0.15)", fg: theme.green },
    red: { bg: "rgba(239,68,68,0.15)", fg: theme.red },
    yellow: { bg: "rgba(234,179,8,0.15)", fg: theme.yellow },
    blue: { bg: "rgba(59,130,246,0.15)", fg: theme.blue },
  };
  const c = colors[color];
  return (
    <span style={{
      padding: "2px 8px",
      borderRadius: 2,
      fontSize: 10,
      letterSpacing: 0.5,
      fontWeight: 600,
      background: c.bg,
      color: c.fg,
      fontFamily: theme.mono,
      textTransform: "uppercase",
    }}>{children}</span>
  );
};

const Panel = ({ title, subtitle, action, children, accent }) => (
  <div style={{
    background: theme.bgPanel,
    border: `1px solid ${theme.border}`,
    borderLeft: accent ? `2px solid ${accent}` : `1px solid ${theme.border}`,
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
  }}>
    <div style={{
      padding: "12px 16px",
      borderBottom: `1px solid ${theme.border}`,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: 12,
    }}>
      <div>
        <div style={{
          fontSize: 11,
          letterSpacing: 2,
          color: theme.textDim,
          textTransform: "uppercase",
          fontFamily: theme.mono,
        }}>{title}</div>
        {subtitle && <div style={{ fontSize: 10, color: theme.textFaint, marginTop: 2 }}>{subtitle}</div>}
      </div>
      {action}
    </div>
    <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
      {children}
    </div>
  </div>
);

const StatCard = ({ label, value, sub, color }) => (
  <div style={{
    background: theme.bgPanel,
    border: `1px solid ${theme.border}`,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 4,
  }}>
    <div style={{
      fontSize: 10,
      letterSpacing: 1.5,
      color: theme.textDim,
      textTransform: "uppercase",
      fontFamily: theme.mono,
    }}>{label}</div>
    <div style={{
      fontSize: 24,
      fontWeight: 500,
      fontFamily: theme.mono,
      color: color || theme.text,
      fontVariantNumeric: "tabular-nums",
    }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: theme.textFaint, fontFamily: theme.mono }}>{sub}</div>}
  </div>
);

// ============ TABLE COMPONENTS ============
const th = {
  padding: "10px 16px",
  fontSize: 10,
  letterSpacing: 1,
  color: theme.textDim,
  textTransform: "uppercase",
  textAlign: "left",
  fontFamily: theme.mono,
  borderBottom: `1px solid ${theme.border}`,
  fontWeight: 500,
};
const td = {
  padding: "12px 16px",
  fontSize: 12,
  color: theme.text,
  fontFamily: theme.mono,
  borderBottom: `1px solid ${theme.border}`,
  fontVariantNumeric: "tabular-nums",
};

// ============ PANELS ============

function SignalsPanel({ signals }) {
  return (
    <Panel title="Active Signals" subtitle={`${signals.length} signals`} accent={theme.blue}>
      {signals.length === 0 ? (
        <div style={{ padding: 24, color: theme.textFaint, fontSize: 12, fontFamily: theme.mono }}>
          No active signals. Scanner running...
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={th}>Symbol</th>
              <th style={th}>Side</th>
              <th style={th}>Strength</th>
              <th style={th}>Entry</th>
              <th style={th}>SL / TP</th>
              <th style={th}>R:R</th>
              <th style={th}>Size</th>
              <th style={th}>Conf</th>
            </tr>
          </thead>
          <tbody>
            {signals.map((s) => (
              <tr key={s.signal_id} style={{ transition: "background 0.1s" }}
                  onMouseEnter={(e) => e.currentTarget.style.background = theme.bgElevated}
                  onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
                <td style={{...td, fontWeight: 600}}>{s.symbol}</td>
                <td style={td}><Pill color={s.side === "LONG" ? "green" : "red"}>{s.side}</Pill></td>
                <td style={td}>
                  <Pill color={s.strength === "STRONG" ? "green" : s.strength === "MEDIUM" ? "yellow" : "default"}>
                    {s.strength}
                  </Pill>
                </td>
                <td style={td}>{fmt.num(s.entry_price, 4)}</td>
                <td style={{...td, fontSize: 11}}>
                  <span style={{ color: theme.red }}>{fmt.num(s.stop_loss, 4)}</span>
                  {" / "}
                  <span style={{ color: theme.green }}>{fmt.num(s.take_profit, 4)}</span>
                </td>
                <td style={td}>1:{fmt.num(s.risk_reward_ratio, 1)}</td>
                <td style={td}>{fmt.usd(s.suggested_size_usdt)} @ {s.leverage}x</td>
                <td style={td}>{fmt.pct(s.confidence * 100)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function PositionsPanel({ positions, onClose }) {
  return (
    <Panel title="Open Positions" subtitle={`${positions.length} active`} accent={theme.green}>
      {positions.length === 0 ? (
        <div style={{ padding: 24, color: theme.textFaint, fontSize: 12, fontFamily: theme.mono }}>
          No open positions.
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={th}>Symbol</th>
              <th style={th}>Side</th>
              <th style={th}>Entry</th>
              <th style={th}>Mark</th>
              <th style={th}>PnL</th>
              <th style={th}>PnL %</th>
              <th style={th}>Size</th>
              <th style={th}>Action</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.symbol}>
                <td style={{...td, fontWeight: 600}}>{p.symbol}</td>
                <td style={td}><Pill color={p.side === "LONG" ? "green" : "red"}>{p.side}</Pill></td>
                <td style={td}>{fmt.num(p.entry_price, 4)}</td>
                <td style={td}>{fmt.num(p.current_price, 4)}</td>
                <td style={{...td, color: pnlColor(p.unrealized_pnl_usdt)}}>
                  {fmt.usd(p.unrealized_pnl_usdt)}
                </td>
                <td style={{...td, color: pnlColor(p.unrealized_pnl_pct)}}>
                  {fmt.pctSigned(p.unrealized_pnl_pct)}
                </td>
                <td style={td}>{fmt.usd(p.size_usdt)} @ {p.leverage}x</td>
                <td style={td}>
                  <button onClick={() => onClose(p.symbol)} style={{
                    background: "transparent",
                    border: `1px solid ${theme.red}`,
                    color: theme.red,
                    padding: "4px 12px",
                    fontSize: 10,
                    fontFamily: theme.mono,
                    cursor: "pointer",
                    letterSpacing: 1,
                    textTransform: "uppercase",
                  }}>Close</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function DivergencesPanel({ divergences }) {
  return (
    <Panel title="OI Divergences" subtitle="Real-time scanner output" accent={theme.amber}>
      {divergences.length === 0 ? (
        <div style={{ padding: 24, color: theme.textFaint, fontSize: 12, fontFamily: theme.mono }}>
          Scanning market...
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={th}>Symbol</th>
              <th style={th}>Dir</th>
              <th style={th}>ΔOI</th>
              <th style={th}>ΔPrice</th>
              <th style={th}>Ratio</th>
              <th style={th}>Funding</th>
              <th style={th}>Taker</th>
              <th style={th}>Conf</th>
            </tr>
          </thead>
          <tbody>
            {divergences.slice(0, 15).map((d, i) => (
              <tr key={`${d.symbol}-${i}`}>
                <td style={{...td, fontWeight: 600}}>{d.symbol}</td>
                <td style={td}><Pill color={d.direction === "LONG" ? "green" : "red"}>{d.direction}</Pill></td>
                <td style={{...td, color: theme.green}}>{fmt.pctSigned(d.oi_change_pct)}</td>
                <td style={{...td, color: pnlColor(d.price_change_pct)}}>{fmt.pctSigned(d.price_change_pct)}</td>
                <td style={{...td, color: theme.amber, fontWeight: 600}}>{fmt.num(d.divergence_ratio, 1)}x</td>
                <td style={td}>{fmt.pct(d.funding_rate * 100)}</td>
                <td style={td}>{fmt.num(d.taker_ratio, 2)}</td>
                <td style={td}>{fmt.pct(d.confidence * 100)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function SentimentPanel({ sentiments }) {
  const items = Object.values(sentiments).sort((a, b) => b.composite_score - a.composite_score).slice(0, 10);
  return (
    <Panel title="Sentiment Heat" subtitle="Binance Square + CoinGecko + Gainers" accent="#a855f7">
      {items.length === 0 ? (
        <div style={{ padding: 24, color: theme.textFaint, fontSize: 12, fontFamily: theme.mono }}>
          Aggregating sentiment...
        </div>
      ) : (
        <div style={{ padding: "8px 0" }}>
          {items.map((s) => (
            <div key={s.symbol} style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "10px 16px",
              borderBottom: `1px solid ${theme.border}`,
            }}>
              <div style={{ width: 60, fontFamily: theme.mono, fontSize: 12, fontWeight: 600 }}>${s.symbol}</div>
              <div style={{ flex: 1, height: 4, background: theme.bgElevated, position: "relative" }}>
                <div style={{
                  position: "absolute",
                  left: 0, top: 0, bottom: 0,
                  width: `${s.composite_score}%`,
                  background: `linear-gradient(90deg, ${theme.blue}, #a855f7)`,
                }} />
              </div>
              <div style={{ width: 40, fontFamily: theme.mono, fontSize: 11, color: theme.textDim, textAlign: "right" }}>
                {fmt.num(s.composite_score, 0)}
              </div>
              <div style={{ width: 80, fontFamily: theme.mono, fontSize: 10, color: theme.textFaint }}>
                {s.square_mentions}M · #{s.gainers_rank || "—"}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function ClosedTradesPanel({ trades }) {
  return (
    <Panel title="Trade History" subtitle={`Last ${trades.length} trades`}>
      {trades.length === 0 ? (
        <div style={{ padding: 24, color: theme.textFaint, fontSize: 12, fontFamily: theme.mono }}>
          No closed trades yet.
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={th}>Symbol</th>
              <th style={th}>Side</th>
              <th style={th}>Entry → Exit</th>
              <th style={th}>PnL</th>
              <th style={th}>PnL %</th>
              <th style={th}>Exit</th>
              <th style={th}>Time</th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(-20).reverse().map((t, i) => (
              <tr key={i}>
                <td style={{...td, fontWeight: 600}}>{t.symbol}</td>
                <td style={td}><Pill color={t.side === "LONG" ? "green" : "red"}>{t.side}</Pill></td>
                <td style={td}>{fmt.num(t.entry_price, 4)} → {fmt.num(t.exit_price, 4)}</td>
                <td style={{...td, color: pnlColor(t.realized_pnl_usdt)}}>{fmt.usd(t.realized_pnl_usdt)}</td>
                <td style={{...td, color: pnlColor(t.realized_pnl_pct)}}>{fmt.pctSigned(t.realized_pnl_pct)}</td>
                <td style={td}>
                  <Pill color={t.exit_reason === "TP" ? "green" : t.exit_reason === "SL" ? "red" : "default"}>
                    {t.exit_reason}
                  </Pill>
                </td>
                <td style={{...td, color: theme.textFaint}}>{fmt.time(t.closed_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

// ============ NEW LISTINGS PANEL ============

const NL_STATUS_COLOR = {
  READY:     "green",
  TRIGGERED: "yellow",
  DANGER:    "red",
  WATCHING:  "default",
};

function CondBadge({ met, label, naMode }) {
  const color = naMode ? theme.textFaint : met ? theme.green : theme.red;
  const char  = naMode ? "◌" : met ? "●" : "○";
  return (
    <span title={label} style={{
      color,
      fontSize: 13,
      lineHeight: 1,
      cursor: "default",
      userSelect: "none",
    }}>{char}</span>
  );
}

function NewListingRow({ setup }) {
  const sc = NL_STATUS_COLOR[setup.status] || "default";
  const fr = (setup.funding_rate * 100).toFixed(3);
  const volPct = setup.volume_ratio != null ? (setup.volume_ratio * 100).toFixed(0) : "—";
  const distToBreak = setup.consolidation_high > 0 && setup.current_price > 0
    ? ((setup.consolidation_high - setup.current_price) / setup.current_price * 100).toFixed(1)
    : null;

  return (
    <div style={{
      borderBottom: `1px solid ${theme.border}`,
      padding: "10px 16px",
      background: setup.status === "DANGER" ? "rgba(239,68,68,0.05)"
                : setup.status === "READY"   ? "rgba(34,197,94,0.04)"
                : "transparent",
    }}>
      {/* Row 1 — identity + status + conditions */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{
          fontFamily: theme.mono,
          fontWeight: 700,
          fontSize: 13,
          color: theme.text,
          minWidth: 110,
        }}>
          {setup.symbol.replace("USDT", "")}
          <span style={{ color: theme.textFaint, fontWeight: 400, fontSize: 10 }}>/USDT</span>
        </span>

        <Pill color={sc}>{setup.status}</Pill>

        <span style={{ fontSize: 10, color: theme.textFaint, fontFamily: theme.mono }}>
          listed {setup.listing_age_hours?.toFixed(0)}h ago
        </span>

        {/* 5 condition dots */}
        <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
          <CondBadge
            met={setup.cond_consolidation}
            label={`① Consolidation: ${setup.consolidation_hours?.toFixed(0)}h / ${setup.consolidation_range_pct?.toFixed(1)}%`}
          />
          <CondBadge
            met={setup.cond_funding}
            label={`② Funding: ${fr}%`}
          />
          <CondBadge
            met={setup.cond_volume}
            label={`③ Volume: ${volPct}% of peak`}
          />
          <CondBadge
            met={setup.cond_oi_stable}
            label={`④ OI 4h: ${setup.oi_change_4h_pct?.toFixed(1)}%`}
          />
          <CondBadge
            met={setup.cond_ls_ratio}
            naMode={setup.ls_ratio == null}
            label={`⑤ L/S: ${setup.ls_ratio?.toFixed(2) ?? "N/A"}`}
          />
          <span style={{
            fontSize: 10,
            color: setup.conditions_met === 5 ? theme.green : theme.textFaint,
            fontFamily: theme.mono,
            marginLeft: 2,
          }}>
            {setup.conditions_met}/5
          </span>
        </div>

        {distToBreak !== null && setup.status !== "TRIGGERED" && setup.status !== "DANGER" && (
          <span style={{ fontSize: 10, color: theme.amber, fontFamily: theme.mono }}>
            {parseFloat(distToBreak) <= 0
              ? `+${Math.abs(parseFloat(distToBreak)).toFixed(1)}% above break`
              : `${distToBreak}% to break`}
          </span>
        )}
      </div>

      {/* Row 2 — metrics */}
      <div style={{
        marginTop: 5,
        display: "flex",
        gap: 18,
        fontSize: 11,
        fontFamily: theme.mono,
        color: theme.textDim,
        flexWrap: "wrap",
      }}>
        <span>
          Fund{" "}
          <span style={{ color: setup.cond_funding ? theme.green : theme.red }}>
            {fr}%
          </span>
        </span>
        <span>
          Vol{" "}
          <span style={{ color: setup.cond_volume ? theme.green : theme.yellow }}>
            {volPct}%
          </span>
          <span style={{ color: theme.textFaint }}> of peak</span>
        </span>
        <span>
          OI 4h{" "}
          <span style={{ color: setup.cond_oi_stable ? theme.green : theme.red }}>
            {setup.oi_change_4h_pct != null ? `${setup.oi_change_4h_pct > 0 ? "+" : ""}${setup.oi_change_4h_pct?.toFixed(1)}%` : "—"}
          </span>
        </span>
        <span>
          L/S{" "}
          <span style={{ color: setup.cond_ls_ratio ? theme.green : setup.ls_ratio == null ? theme.textFaint : theme.red }}>
            {setup.ls_ratio?.toFixed(2) ?? "N/A"}
          </span>
        </span>
        <span>
          Cons{" "}
          <span style={{ color: setup.cond_consolidation ? theme.green : theme.yellow }}>
            {setup.consolidation_hours?.toFixed(0)}h
          </span>
          {" / "}
          <span style={{ color: setup.consolidation_range_pct < 22 ? theme.green : theme.yellow }}>
            {setup.consolidation_range_pct?.toFixed(1)}%
          </span>
        </span>
        <span>
          Range{" "}
          <span style={{ color: theme.amber }}>
            {setup.consolidation_low > 0 ? fmt.num(setup.consolidation_low, 4) : "—"}
          </span>
          {" – "}
          <span style={{ color: theme.amber }}>
            {setup.consolidation_high > 0 ? fmt.num(setup.consolidation_high, 4) : "—"}
          </span>
        </span>
        <span>
          Now{" "}
          <span style={{ color: theme.text, fontWeight: 600 }}>
            {setup.current_price > 0 ? fmt.num(setup.current_price, 4) : "—"}
          </span>
        </span>
        {setup.triggered_at && (
          <span>
            Triggered <span style={{ color: theme.yellow }}>{fmt.time(setup.triggered_at)}</span>
          </span>
        )}
      </div>

      {/* Row 3 — danger signals */}
      {setup.danger_signals && setup.danger_signals.length > 0 && (
        <div style={{
          marginTop: 5,
          display: "flex",
          gap: 8,
          flexWrap: "wrap",
        }}>
          {setup.danger_signals.map((d, i) => (
            <span key={i} style={{
              background: "rgba(239,68,68,0.15)",
              color: theme.red,
              padding: "2px 8px",
              borderRadius: 2,
              fontSize: 10,
              fontFamily: theme.mono,
              fontWeight: 600,
              letterSpacing: 0.5,
            }}>
              ⚠ {d}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function NewListingsPanel({ setups }) {
  const list = Object.values(setups || {});
  const nReady     = list.filter((s) => s.status === "READY").length;
  const nTriggered = list.filter((s) => s.status === "TRIGGERED").length;
  const nDanger    = list.filter((s) => s.status === "DANGER").length;

  // Sort: DANGER > TRIGGERED > READY > WATCHING, then by conditions_met desc
  const sorted = [...list].sort((a, b) => {
    const order = { DANGER: 4, TRIGGERED: 3, READY: 2, WATCHING: 1 };
    const od = (order[b.status] || 0) - (order[a.status] || 0);
    return od !== 0 ? od : (b.conditions_met || 0) - (a.conditions_met || 0);
  });

  const subtitleParts = [`${list.length} tracked`];
  if (nReady)     subtitleParts.push(`${nReady} ready`);
  if (nTriggered) subtitleParts.push(`${nTriggered} triggered`);
  if (nDanger)    subtitleParts.push(`${nDanger} ⚠ danger`);

  return (
    <Panel
      title="New Listing Pump"
      subtitle={subtitleParts.join(" · ")}
      accent={theme.amber}
    >
      {list.length === 0 ? (
        <div style={{ padding: "24px 16px", color: theme.textFaint, fontSize: 12, fontFamily: theme.mono }}>
          No coins in 12–96h listing window. Checking every 60s…
        </div>
      ) : (
        sorted.map((s) => <NewListingRow key={s.symbol} setup={s} />)
      )}
    </Panel>
  );
}

// ============ DATA SOURCES PANEL ============

const STATUS_COLOR = {
  OK:           "green",
  FALLBACK:     "yellow",
  NO_KEY:       "yellow",
  DISABLED:     "default",
  STANDBY:      "default",
  NO_DATA:      "yellow",
  INITIALIZING: "blue",
  ERROR:        "red",
  KILLED:       "red",
};

function fmtAge(ageS) {
  if (ageS == null) return "—";
  if (ageS < 60)   return `${ageS}s ago`;
  if (ageS < 3600) return `${Math.floor(ageS / 60)}m ago`;
  return `${Math.floor(ageS / 3600)}h ago`;
}

function SourceRow({ name, src }) {
  if (!src) return null;
  const color = STATUS_COLOR[src.status] || "default";
  const metaParts = [];
  if (src.symbols_tracked != null)   metaParts.push(`${src.symbols_tracked} syms`);
  if (src.active_divergences != null) metaParts.push(`${src.active_divergences} divs`);
  if (src.symbols_streaming != null) metaParts.push(`${src.symbols_streaming} streaming`);
  if (src.symbols_parsed != null)    metaParts.push(`${src.symbols_parsed} symbols`);
  if (src.multi_tf_confirmed != null) metaParts.push(`${src.multi_tf_confirmed} multi-TF`);
  if (src.posts_scraped != null)     metaParts.push(`${src.posts_scraped} posts`);
  if (src.tickers_found != null)     metaParts.push(`${src.tickers_found} tickers`);
  if (src.trending_count != null)    metaParts.push(`${src.trending_count} trending`);
  if (src.value != null)             metaParts.push(`F&G ${src.value} (${src.label || ""})`);
  if (src.has_api_key === false)     metaParts.push("no key");
  if (src.is_fallback === true)      metaParts.push("fallback mode");
  if (src.testnet)                   metaParts.push("testnet");
  if (src.dry_run)                   metaParts.push("dry-run");
  const meta = metaParts.join(" · ");

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "8px 16px",
      borderBottom: `1px solid ${theme.border}`,
    }}>
      <div style={{ width: 130, fontSize: 11, fontFamily: theme.mono, color: theme.text, fontWeight: 600 }}>
        {name}
      </div>
      <Pill color={color}>{src.status}</Pill>
      <div style={{ flex: 1, fontSize: 11, fontFamily: theme.mono, color: theme.textDim }}>
        {meta}
      </div>
      <div style={{ fontSize: 10, fontFamily: theme.mono, color: theme.textFaint, whiteSpace: "nowrap" }}>
        {fmtAge(src.last_update_age_s)}
      </div>
      {src.last_error && (
        <div style={{ fontSize: 10, fontFamily: theme.mono, color: theme.red, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
             title={src.last_error}>
          ⚠ {src.last_error}
        </div>
      )}
    </div>
  );
}

// ============ BTC BIAS PANEL ============
function BTCBiasPanel({ bias }) {
  if (!bias) return (
    <Panel title="Smart Money Bias" subtitle="Paul Wei · @coolish · BitMEX Hall of Legends" accent="#a855f7">
      <div style={{ padding: 24, color: theme.textFaint, fontSize: 12, fontFamily: theme.mono }}>
        Fetching data from github.com/bwjoke/BTC-Trading-Since-2020…
      </div>
    </Panel>
  );

  const dirColor = bias.direction === "BULLISH" ? theme.green
    : bias.direction === "BEARISH" ? theme.red
    : theme.textDim;

  const dirEmoji = bias.direction === "BULLISH" ? "🟢" : bias.direction === "BEARISH" ? "🔴" : "⚪";
  const regimeColor = bias.regime === "BULL" ? theme.green : bias.regime === "BEAR" ? theme.red : theme.yellow;

  const confPct = ((bias.confidence || 0) * 100).toFixed(0);
  const isShort = bias.position_qty < 0;

  const subtitle = `52x return · ${bias.account_multiple?.toFixed(1)}x adjusted multiple · ${bias.data_date || "—"}`;

  return (
    <Panel title="Smart Money Bias · Paul Wei @coolish" subtitle={subtitle} accent="#a855f7">
      <div style={{ display: "flex", gap: 0 }}>

        {/* Left: main signal */}
        <div style={{
          flex: "0 0 220px",
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          padding: "20px 16px", borderRight: `1px solid ${theme.border}`,
          background: `rgba(${bias.direction==="BEARISH"?"239,68,68":bias.direction==="BULLISH"?"34,197,94":"100,100,100"},0.04)`,
        }}>
          <div style={{ fontSize: 36, marginBottom: 4 }}>{dirEmoji}</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: dirColor, fontFamily: theme.display, letterSpacing: 1 }}>
            {bias.direction}
          </div>
          <div style={{ fontSize: 11, color: theme.textDim, fontFamily: theme.mono, marginTop: 4 }}>
            CONFIDENCE
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: dirColor, fontFamily: theme.mono }}>
            {confPct}%
          </div>
          {/* Confidence bar */}
          <div style={{ width: "100%", height: 4, background: theme.border, borderRadius: 2, marginTop: 8 }}>
            <div style={{
              width: `${confPct}%`, height: "100%", borderRadius: 2,
              background: dirColor, transition: "width 0.4s ease",
            }}/>
          </div>
        </div>

        {/* Middle: position details */}
        <div style={{ flex: 1, padding: "12px 16px", borderRight: `1px solid ${theme.border}` }}>
          <div style={{ fontSize: 10, color: theme.textFaint, fontFamily: theme.mono, marginBottom: 8, letterSpacing: 1 }}>
            CURRENT POSITION — XBTUSD (BitMEX)
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 16px" }}>
            {[
              ["Direction", isShort ? "SHORT" : bias.position_qty > 0 ? "LONG" : "FLAT",
               isShort ? theme.red : bias.position_qty > 0 ? theme.green : theme.textDim],
              ["Qty", bias.position_qty != null ? bias.position_qty.toLocaleString() : "—", dirColor],
              ["Avg Entry", bias.avg_entry_price ? `$${bias.avg_entry_price.toLocaleString()}` : "—", theme.text],
              ["Mark Price", bias.mark_price ? `$${bias.mark_price.toLocaleString()}` : "—", theme.text],
              ["Unrealized PnL", bias.unrealized_pnl_pct != null
                ? `${bias.unrealized_pnl_pct >= 0 ? "+" : ""}${bias.unrealized_pnl_pct.toFixed(2)}%`
                : "—",
               pnlColor(bias.unrealized_pnl_pct)],
              ["Leverage", bias.leverage ? `${bias.leverage}x` : "—", theme.amber],
            ].map(([label, value, color]) => (
              <div key={label}>
                <div style={{ fontSize: 9, color: theme.textFaint, fontFamily: theme.mono, letterSpacing: 0.5 }}>{label}</div>
                <div style={{ fontSize: 13, fontWeight: 600, color: color || theme.text, fontFamily: theme.mono }}>{value}</div>
              </div>
            ))}
          </div>

          {bias.key_level && (
            <div style={{
              marginTop: 10, padding: "5px 10px", borderRadius: 4,
              background: "rgba(168,85,247,0.1)", border: "1px solid rgba(168,85,247,0.3)",
              fontSize: 11, fontFamily: theme.mono, color: "#a855f7",
            }}>
              🎯 Key Level: <strong>${bias.key_level.toLocaleString()}</strong>
              {" "}(Paul Wei avg entry — watch for reaction)
            </div>
          )}
        </div>

        {/* Right: market regime + equity stats */}
        <div style={{ flex: "0 0 180px", padding: "12px 16px" }}>
          <div style={{ fontSize: 10, color: theme.textFaint, fontFamily: theme.mono, marginBottom: 8, letterSpacing: 1 }}>
            MARKET REGIME
          </div>
          <div style={{
            fontSize: 16, fontWeight: 700, color: regimeColor, fontFamily: theme.display,
            marginBottom: 14, letterSpacing: 1,
          }}>
            {bias.regime || "—"}
          </div>

          <div style={{ fontSize: 10, color: theme.textFaint, fontFamily: theme.mono, marginBottom: 6, letterSpacing: 1 }}>
            EQUITY CURVE (XBT)
          </div>
          {[
            ["7d change", bias.equity_7d_pct != null
              ? `${bias.equity_7d_pct >= 0 ? "+" : ""}${bias.equity_7d_pct.toFixed(2)}%`
              : "—", pnlColor(bias.equity_7d_pct)],
            ["30d change", bias.equity_30d_pct != null
              ? `${bias.equity_30d_pct >= 0 ? "+" : ""}${bias.equity_30d_pct.toFixed(2)}%`
              : "—", pnlColor(bias.equity_30d_pct)],
            ["Data age", bias.last_update_age_h != null ? `${bias.last_update_age_h}h ago` : "—",
             bias.is_fresh ? theme.green : theme.red],
          ].map(([label, value, color]) => (
            <div key={label} style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 9, color: theme.textFaint, fontFamily: theme.mono, letterSpacing: 0.5 }}>{label}</div>
              <div style={{ fontSize: 13, fontWeight: 600, color: color || theme.text, fontFamily: theme.mono }}>{value}</div>
            </div>
          ))}

          {!bias.is_fresh && (
            <div style={{ fontSize: 9, color: theme.red, fontFamily: theme.mono, marginTop: 4 }}>
              ⚠ STALE — repo updates daily
            </div>
          )}
          {bias.error && (
            <div style={{ fontSize: 9, color: theme.red, fontFamily: theme.mono, marginTop: 4,
              wordBreak: "break-all" }}>
              ERR: {bias.error.slice(0, 60)}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

function DataSourcesPanel({ ds }) {
  if (!ds) return (
    <Panel title="Data Sources" subtitle="Loading…" accent={theme.amber}>
      <div style={{ padding: 24, color: theme.textFaint, fontSize: 12, fontFamily: theme.mono }}>
        Waiting for datasource status…
      </div>
    </Panel>
  );

  const totalTickers = ds.sentiment?.total_scored_tickers ?? 0;
  const subtitle = `${totalTickers} scored tickers`;

  return (
    <Panel title="Data Sources" subtitle={subtitle} accent={theme.amber}>
      <SourceRow name="OI Scanner"       src={ds.oi_scanner} />
      <SourceRow name="Price Streamer"   src={ds.price_streamer} />
      <SourceRow name="Binance API"      src={ds.binance_api} />
      <SourceRow name="CatTrade Sheet"   src={ds.cattrade} />
      <SourceRow name="Binance Square"   src={ds.sentiment?.binance_square} />
      <SourceRow name="CryptoPanic"      src={ds.sentiment?.cryptopanic} />
      <SourceRow name="Reddit"           src={ds.sentiment?.reddit} />
      <SourceRow name="Fear & Greed"     src={ds.sentiment?.fear_greed} />
      <SourceRow name="CoinGecko"        src={ds.sentiment?.coingecko} />
      <SourceRow name="Binance Gainers"  src={ds.sentiment?.binance_gainers} />
    </Panel>
  );
}

// ============ APP ============

export default function App() {
  const [status, setStatus] = useState(null);
  const [divergences, setDivergences] = useState([]);
  const [sentiments, setSentiments] = useState({});
  const [signals, setSignals] = useState([]);
  const [positions, setPositions] = useState([]);
  const [closedTrades, setClosedTrades] = useState([]);
  const [stats, setStats] = useState(null);
  const [datasources, setDatasources] = useState(null);
  const [newListings, setNewListings] = useState({});
  const [btcBias, setBtcBias] = useState(null);
  const [connected, setConnected] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [s, d, se, si, p, ct, st, ds, nl, bb] = await Promise.all([
        fetch(`${API_BASE}/api/status`).then((r) => r.json()),
        fetch(`${API_BASE}/api/divergences`).then((r) => r.json()),
        fetch(`${API_BASE}/api/sentiments`).then((r) => r.json()),
        fetch(`${API_BASE}/api/signals`).then((r) => r.json()),
        fetch(`${API_BASE}/api/positions`).then((r) => r.json()),
        fetch(`${API_BASE}/api/closed-trades`).then((r) => r.json()),
        fetch(`${API_BASE}/api/stats`).then((r) => r.json()),
        fetch(`${API_BASE}/api/datasources`).then((r) => r.json()).catch(() => null),
        fetch(`${API_BASE}/api/new-listings`).then((r) => r.json()).catch(() => ({})),
        fetch(`${API_BASE}/api/btc-bias`).then((r) => r.json()).catch(() => null),
      ]);
      setStatus(s);
      setDivergences(d);
      setSentiments(se);
      setSignals(si);
      setPositions(p);
      setClosedTrades(ct);
      setStats(st);
      if (ds) setDatasources(ds);
      if (nl) setNewListings(nl);
      if (bb) setBtcBias(bb);
      setConnected(true);
    } catch (e) {
      setConnected(false);
    }
  }, []);
  
  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    
    // WebSocket for real-time updates
    let ws;
    try {
      ws = new WebSocket(WS_URL);
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          // Fast-path updates without full refetch for new_listings pushes
          if (msg.type === "new_listings") {
            setNewListings(msg.data);
          } else {
            fetchData();
          }
        } catch {
          fetchData();
        }
      };
    } catch (e) {}
    
    return () => {
      clearInterval(interval);
      if (ws) ws.close();
    };
  }, [fetchData]);
  
  const handleClose = async (symbol) => {
    if (!confirm(`Close position ${symbol}?`)) return;
    await fetch(`${API_BASE}/api/position/${symbol}/close`, { method: "POST" });
    fetchData();
  };
  
  const handleKillSwitch = async () => {
    if (!confirm("EMERGENCY STOP: close all positions and halt trading. Continue?")) return;
    const reason = prompt("Reason?", "Manual emergency");
    await fetch(`${API_BASE}/api/kill-switch/trigger?reason=${encodeURIComponent(reason || "")}`, { method: "POST" });
    fetchData();
  };
  
  const handleResetKill = async () => {
    await fetch(`${API_BASE}/api/kill-switch/reset`, { method: "POST" });
    fetchData();
  };
  
  return (
    <div style={{
      minHeight: "100vh",
      background: theme.bg,
      color: theme.text,
      fontFamily: theme.display,
      display: "flex",
      flexDirection: "column",
    }}>
      {/* Header */}
      <header style={{
        padding: "16px 24px",
        borderBottom: `1px solid ${theme.border}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: theme.bgElevated,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{
            fontSize: 14,
            fontFamily: theme.mono,
            letterSpacing: 3,
            color: theme.text,
            fontWeight: 600,
          }}>
            OI.<span style={{color: theme.amber}}>DIVERGENCE</span>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Pill color={connected ? "green" : "red"}>
              {connected ? "● CONNECTED" : "● DISCONNECTED"}
            </Pill>
            {status?.testnet && <Pill color="yellow">TESTNET</Pill>}
            {status?.dry_run && <Pill color="blue">DRY RUN</Pill>}
            {status?.kill_switch && <Pill color="red">⚠ KILLED</Pill>}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {status?.kill_switch ? (
            <button onClick={handleResetKill} style={{
              background: "transparent",
              border: `1px solid ${theme.yellow}`,
              color: theme.yellow,
              padding: "8px 16px",
              fontFamily: theme.mono,
              fontSize: 11,
              letterSpacing: 1.5,
              textTransform: "uppercase",
              cursor: "pointer",
            }}>Reset Kill Switch</button>
          ) : (
            <button onClick={handleKillSwitch} style={{
              background: theme.red,
              border: `1px solid ${theme.red}`,
              color: "#fff",
              padding: "8px 16px",
              fontFamily: theme.mono,
              fontSize: 11,
              letterSpacing: 1.5,
              textTransform: "uppercase",
              cursor: "pointer",
              fontWeight: 600,
            }}>⚠ Kill Switch</button>
          )}
        </div>
      </header>
      
      {/* Stats Row */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, 1fr)",
        gap: 1,
        background: theme.border,
      }}>
        <StatCard
          label="Total PnL"
          value={stats ? fmt.usd(stats.total_pnl_usdt) : "—"}
          color={stats ? pnlColor(stats.total_pnl_usdt) : theme.text}
        />
        <StatCard
          label="Win Rate"
          value={stats ? fmt.pct((stats.win_rate || 0) * 100) : "—"}
          sub={stats ? `${stats.total_trades} trades` : ""}
        />
        <StatCard
          label="Profit Factor"
          value={stats ? fmt.num(stats.profit_factor, 2) : "—"}
          sub={stats && stats.profit_factor > 1.5 ? "✓ good" : "needs >1.5"}
          color={stats && stats.profit_factor > 1.5 ? theme.green : theme.yellow}
        />
        <StatCard
          label="Open Positions"
          value={positions.length}
          sub={`${signals.length} signals queued`}
        />
        <StatCard
          label="Avg Win/Loss"
          value={stats ? `${fmt.usd(stats.avg_win_usdt)} / ${fmt.usd(stats.avg_loss_usdt)}` : "—"}
        />
      </div>
      
      {/* Main Grid */}
      <main style={{
        flex: 1,
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gridTemplateRows: "1fr 1fr auto auto auto auto",
        gap: 1,
        background: theme.border,
        padding: 1,
        minHeight: 0,
      }}>
        {/* Row 1 */}
        <div style={{ gridColumn: "1 / 2", gridRow: "1 / 2", minHeight: 0 }}>
          <PositionsPanel positions={positions} onClose={handleClose} />
        </div>
        <div style={{ gridColumn: "2 / 3", gridRow: "1 / 2", minHeight: 0 }}>
          <SignalsPanel signals={signals} />
        </div>
        {/* Row 2 */}
        <div style={{ gridColumn: "1 / 2", gridRow: "2 / 3", minHeight: 0 }}>
          <DivergencesPanel divergences={divergences} />
        </div>
        <div style={{ gridColumn: "2 / 3", gridRow: "2 / 3", minHeight: 0 }}>
          <SentimentPanel sentiments={sentiments} />
        </div>
        {/* Row 3 — BTC Smart Money Bias (full width) */}
        <div style={{ gridColumn: "1 / 3", gridRow: "3 / 4", minHeight: 0 }}>
          <BTCBiasPanel bias={btcBias} />
        </div>
        {/* Row 4 — New Listings (full width) */}
        <div style={{ gridColumn: "1 / 3", gridRow: "4 / 5", minHeight: 0 }}>
          <NewListingsPanel setups={newListings} />
        </div>
        {/* Row 5 — Data Sources (full width) */}
        <div style={{ gridColumn: "1 / 3", gridRow: "5 / 6", minHeight: 0 }}>
          <DataSourcesPanel ds={datasources} />
        </div>
        {/* Row 6 — Closed Trades (full width) */}
        <div style={{ gridColumn: "1 / 3", gridRow: "6 / 7", minHeight: 0 }}>
          <ClosedTradesPanel trades={closedTrades} />
        </div>
      </main>
      
      {/* Footer */}
      <footer style={{
        padding: "8px 24px",
        borderTop: `1px solid ${theme.border}`,
        fontSize: 10,
        color: theme.textFaint,
        fontFamily: theme.mono,
        display: "flex",
        justifyContent: "space-between",
      }}>
        <span>OI.DIVERGENCE v1.0 · BINANCE USDT-M FUTURES · {status?.testnet ? "TESTNET" : "MAINNET"}</span>
        <span>{new Date().toLocaleTimeString()}</span>
      </footer>
    </div>
  );
}
