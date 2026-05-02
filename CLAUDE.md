# CLAUDE.md

Context for future Claude sessions working on this project. Read this before
making strategy or filter changes.

## What this project is

ES futures (E-mini S&P 500) intraday automated trading system using an
**Opening Range Breakout (ORB)** strategy.

Architecture:
- **NinjaTrader bridge** ([integrations/NinjaTrader_bridge_script.cs](integrations/NinjaTrader_bridge_script.cs)) — live market data feed, posts each 1-min bar to the API.
- **FastAPI Python backend** ([app/main.py](app/main.py)) — receives bars, runs the decision engine, manages positions.
- **SQLite database** (`trades.db`) — stores `market_bars`, `trade_logs`, `completed_trades`, `order_skips`. Backup convention: `trades.db.bak.<reason>`.
- **Vue dashboard** ([ai-trader-dashboard/](ai-trader-dashboard/)) — visualizes per-day session candles + decisions, summary stats.

The decision engine runs on every bar; the bridge gates whether to actually submit orders.

## Strategy snapshot (production logic)

Entry: ORB break (price within 0.15 × ATR of the level) → multi-filter validation → score gate → signal.

The current filter stack, in order ([app/decision_engine.py](app/decision_engine.py)):

1. **Position/loss-streak/time gates** (`decide()`):
   - Already in position → HOLD
   - 3+ consecutive losses today → HALT
   - `mao >= 360` → no new entries
   - Weak windows blocked: `[30, 90)` (post-ORB chop) and `[90, 180)` (mid-session)
   - Window `[180, 240)` and `[240, 300)` intentionally **unblocked** — backtest shows 52% and 63% WR respectively
   - Chop ≥ 0.55 → HOLD
   - ATR ≤ 0 → HOLD
   - `trend_score < ORB_TREND_ENTRY_FLOOR (0.65)` → HOLD (soft floor only — wider check below)
   - HTF regime computed (`htf_regime_adaptive` graduates EMA20 → EMA50 around 60 bars).

2. **ORB-level gates** (`orb_breakout()`):
   - Risk cap from `orb_max_risk_points(mao)` — time-of-day-aware
   - Proximity gate: `price ≥ orb_high − 0.15 × ATR` (or mirror for shorts)
   - RSI exhaustion lookback: any of last 5 bars' RSI > 72 (longs) / < 28 (shorts) blocks the entry
   - Consecutive close direction: entry close > prior close, prior close > prior-prior close (mirror for shorts)
   - **Wider trend window**: ≥ 6 of last 20 bars must have `trend_score ≥ 0.90`

3. **Score gates** (each side, must reach 0.70):
   - Break ORB (0.30), VWAP side (0.20), trend ≥ 0.7 (0.20), volume surge (0.15), RSI in band (0.10), time after ORB context (0.05)

4. **Signal-level gates**:
   - RSI band: longs `[55, 75]`, shorts `[25, 45]`
   - HTF regime must agree (`htf_up` for longs, `htf_dn` for shorts)
   - Wick guard: entry-bar wick into stop zone ≤ 2 × ATR

Exits: `2:1 R/R` based on ATR-multiplier risk (`1.25` mid/late, `1.75` first 30 min). Trailing stop tightens through profit tiers in `adjust_trailing_stop()`.

## Known issues — non-obvious traps

### `trend_score` is structurally up-biased

The formula in **both** [app/scripts/import_bars_from_csv.py:153-156](app/scripts/import_bars_from_csv.py#L153-L156) and [integrations/NinjaTrader_bridge_script.cs:180-189](integrations/NinjaTrader_bridge_script.cs#L180-L189) only adds upward signals:

```python
df["trend_score"] = 0.0
df.loc[df["close"] > df["vwap"], "trend_score"] += 0.3   # UP only
df.loc[sma20 > sma50, "trend_score"] += 0.3              # UP only
df.loc[df["atr"] > atr20, "trend_score"] += 0.4          # neutral magnitude
```

**Maximum trend_score in a clean downtrend is 0.40** — it can never reach the 0.90 threshold. The strategy is structurally incapable of taking shorts. On the current dataset, 769 of 794 short-eligible bars (97%) are blocked by this filter alone.

A symmetric metric was prototyped (`/tmp/symmetric_trend_sim.py`) and unlocks shorts — but on the available data, shorts lose money even when unlocked (April-May 2026 was a sustained uptrending regime). **Do not symmetrize without first testing on a downtrending dataset.** When you do fix it, you must update both the CSV import script and the NinjaTrader bridge.

### Dataset is small — 15 clean trading days

Current `trades.db` contains complete data for **2026-04-13 → 2026-05-01 (15 days)**. All other historical dates were removed in cleanup (613 rows: 193 EOD-only summary placeholders + 420 Sunday overnight bars from 2026-04-26). Backup at `trades.db.bak.before-cleanup`.

**Do not over-fit to this dataset.** Strong-trend bucket showing negative long PnL on the regime simulator is suspicious specifically because of regime narrowness — April 2026 was an uptrend, so any "trend up confirmed" entry is a late-trend buy.

### Dashboard date filter

[app/analytics/session_view.py:25](app/analytics/session_view.py#L25) `list_available_dates()` filters to dates with: 14+ ORB bars (08:30-08:44), 300+ session bars (08:30-15:00), and 95%+ indicator coverage. New historical data must pass these to appear in the picker.

## What we tried that doesn't work — don't reinvent these

| Variant | Result vs baseline (80t / 55% WR / +85.19 PnL) | Why we rejected |
|---|---|---|
| Lower `TREND_SCORE_MIN` to 0.70 | Backtest shows 37% WR vs 47% at strict | Already studied — kept at 0.90 with soft 0.65 entry floor |
| Regime-aware (HH/HL pivots + linreg slope, relax filters in confirmed regime) | +188 trades, 41% WR, **+38.79 PnL** (−46.40) | Adds losing trades faster than wins |
| Pullback-then-reclaim (additive, regime-gated) | +19 trades, 30% WR on pullback subset, **+67.80 PnL** (−17.39) | Pullback signal itself is net-negative on this data |
| Symmetric `trend_score` (unlocks shorts) | 72t, 49% WR, **+42.64 PnL** (−42.55) | Unlocks shorts but they lose money; sign-direction gate also costs longs |
| MQL5 article retest pattern (Break → Retest → Re-Break) layered on filters | 2t, **−1.22 PnL** | Almost never fires |
| Retest pattern with minimal filters | 84-86t, 40-43% WR, **−22 to −36 PnL** | Pattern itself loses money on this data |
| Trend-line slope filter (BUY needs slope ≥ X, mirror for SELL; W=30 OLS) | best at X=−0.05: +88.30 (Δ +2.32, noise); X=0.0: +82.49 | Slope distribution at entry is essentially identical for winners and losers (BUY wins median +0.088, losses +0.105). No usable signal. |
| Trend-line extension cap (reject if `(close − fitted)/ATR > X`) | best at W=30, X=1.0: +86.13 (Δ **+0.14**); all other windows (10/20/50) and thresholds regress −4 to −37 | Winners do enter closer to the line (median +0.006 vs losers +0.296 ATR) but distributions overlap too much. Theoretically defensible (mean-reversion guard) but the +0.14 improvement is statistical noise on this 15-day uptrend-only dataset. **Re-test when downtrend / chop sessions are added; X=1.0 with W=30 is the candidate to revisit.** |

The recurring lesson: **on this 15-day dataset, opening more gates produces a strict regression**. The current filter stack is near a local optimum for this data; further gains require either more data or different ideas (not just looser filters).

## What did improve the dashboard (not strategy) this session

[app/trend_lines.py](app/trend_lines.py): rolling 30-bar OLS fitted-value at each
bar, exposed via the session view payload (`trend_line: [{timestamp, value}, …]`)
and rendered as a soft-yellow line in [SessionChart.vue](ai-trader-dashboard/src/components/SessionChart.vue).
Visualization only — was tested as a strategy gate (slope and extension filters
across windows 10/20/30/50, thresholds X = 0.1 → 2.0) and rejected, see the
"What we tried that doesn't work" table.

## What did improve the strategy this session (exits)

**Adverse-close momentum-reversal exit** ([app/constants.py](app/constants.py)
`EXIT_ADVERSE_CLOSE_STREAK = 3`):

Exit at the close of the bar when 3 consecutive bars have closed against the
position direction (close < open for a BUY, close > open for a SELL). The new
[`should_exit_adverse_close`](app/decision_engine.py) helper is wired into
[simulate_exit](app/scripts/backtest_from_bars.py) (so backtests reflect it)
and into [/manage-position](app/main.py) (live bridge can opt in by sending
`bars_since_entry` with the position payload).

Backtest on 2026-04 dataset (78 trades):
- **PnL +85.99 → +105.00 (Δ +19.01, +22%)**
- **Wins 43 → 46 (WR 55% → 59%)**
- **Profit factor 1.86 → 2.19**
- Exit-reason mix: STOPs 32→25, TARGETs 37→37 unchanged, TIME_EXITs 9→6, plus 10 new ADVERSE_CLOSE
- Per-day: **6 days improved, 7 unchanged, 0 regressed**. The wins came from BUY trades that had run up briefly, then 3 reds in a row signaled momentum reversal — exiting at the 3rd close locked in some profit instead of riding to the full stop.

Bridge integration ([integrations/NinjaTrader_bridge_script.cs](integrations/NinjaTrader_bridge_script.cs)):
the bridge maintains two `List<double>` fields (`barsSinceEntryOpens`,
`barsSinceEntryCloses`, capped at 30 entries) that:
- Reset to empty when an entry order fills (`AI_LONG`/`AI_SHORT` execution).
- Append `Open[0]`/`Close[0]` of each just-closed bar in `OnBarUpdate` while the
  position is non-flat.
- Are sent to `/manage-position` as `bars_since_entry` JSON array.
- Are cleared again when an exit fill arrives.

When the server response is `{"action": "EXIT_POSITION", "reason": "ADVERSE_CLOSE"}`,
the bridge submits `ExitLong(qty, "AI_LONG_EXIT_ADVERSE", "AI_LONG")` (or the
short mirror) to flatten the position via market order. Standard exit-fill
handling then computes PnL and logs the trade.

**Sim/Replay verification log line** ([NinjaTrader_bridge_script.cs](integrations/NinjaTrader_bridge_script.cs)
in `ManageOpenPosition`):

```
ManagePos: bars=N -> ACTION (REASON)
```

Each `/manage-position` call prints one of these to the NT output window:
- `ManagePos: bars=2 -> UPDATE_STOP` — normal trailing-stop path, only 2 bars
  since entry so the rule can't fire yet (needs 3 consecutive against-direction
  closes).
- `ManagePos: bars=5 -> UPDATE_STOP` — 5 bars sent, server decided to keep the
  position open (most recent 3 bars were not all adverse).
- `ManagePos: bars=4 -> EXIT_POSITION (ADVERSE_CLOSE)` — rule fired. Bridge will
  immediately follow with `Adverse-close exit triggered: ADVERSE_CLOSE` and an
  `EXIT FILLED via AI_LONG_EXIT_ADVERSE @ <price>` line once the market exit
  fills.

Use this to verify on NT Replay before going live: enter a trade in a chop
session, watch the bar count climb, and confirm the rule fires on a real
3-red-candle reversal.

## What did improve the dashboard (not strategy) this session

These are already shipped:

1. **Wider trend window** ([app/constants.py](app/constants.py): `ORB_TREND_RECENT_LOOKBACK = 20`, `ORB_TREND_RECENT_MIN_STRONG = 6`) — catches sustained-trend breakouts whose entry-bar trend_score has cooled.
2. **Soft entry-bar floor** (`ORB_TREND_ENTRY_FLOOR = 0.65`) — replaces the strict 0.90 check at the gate, paired with the wider-window check inside `orb_breakout()`.
3. **RSI band relaxed**: `ORB_LONG_RSI_HIGH` 72 → 75, `ORB_SHORT_RSI_LOW` 28 → 25 — keeps the 11:46am-style breakouts alive.
4. **Consecutive close direction guard** ([app/decision_engine.py](app/decision_engine.py) in `orb_breakout()`) — blocks entries after a reversal bar (12:53pm pattern) and entries that don't continue the move (1:24pm pattern).
5. **ORB proximity tolerance** (`ORB_ENTRY_PROXIMITY_ATR = 0.15`) — allows close within ~half a tick of the level (handles "tested intrabar, closed 1-2 ticks below" while blocking clearly-inside-range entries).
6. **RSI exhaustion lookback** (`ORB_RSI_EXHAUSTION_LOOKBACK = 5`) — blocks entries when RSI ran to overbought/oversold within the lookback window.

## Working with the simulator

When evaluating proposed strategy changes, don't ship without simulating on the existing dataset. Reference scripts:

- `/tmp/symmetric_trend_sim.py` — symmetric trend_score test
- `/tmp/regime_sim.py` — regime classifier (HH/HL pivots) test
- `/tmp/pullback_sim.py` — additive pullback variant
- `/tmp/retest_sim.py`, `/tmp/retest_minimal_sim.py` — MQL5 article retest pattern
- `/tmp/data_quality_audit.py` — dataset cleanliness audit

Pattern: copy the baseline simulator, modify the signal function, compare totals. Always show the per-day breakdown — totals can hide that one good day masks five bad ones.

## Constants worth knowing

In [app/constants.py](app/constants.py):

- Session: `SESSION_WEAK_OPENING_START/END = 30/90`, `SESSION_WEAK_MID_A_START/END = 90/180`, `SESSION_ORB_NO_ENTRY_MINUTES_AFTER_OPEN = 360`
- Trend: `TREND_SCORE_MIN = 0.90`, `ORB_TREND_ENTRY_FLOOR = 0.65`, `ORB_TREND_RECENT_LOOKBACK = 20`, `ORB_TREND_RECENT_MIN_STRONG = 6`
- RSI: longs `[55, 75]`, shorts `[25, 45]`
- ORB entries: `ORB_ENTRY_PROXIMITY_ATR = 0.15`, `ORB_RSI_EXHAUSTION_LOOKBACK = 5`, `ORB_ENTRY_MAX_WICK_ATR = 2.0`
- Stops: `STRATEGY_ORB_ATR_MULTIPLIER = 1.25` (mid/late), `STRATEGY_ORB_EARLY_ATR_MULTIPLIER = 1.75` (first 30 min), `STRATEGY_ORB_REWARD_RISK_RATIO = 2.0`
- Anti-martingale: `LOSS_STREAK_REDUCE_QTY_THRESHOLD = 2`, `LOSS_STREAK_HALT_THRESHOLD = 3`
- HTF: `HTF_EMA_PERIOD_EARLY = 20` (warmup), `HTF_EMA_PERIOD = 50` (full), `HTF_SLOPE_LOOKBACK = 10`
- Backtest: `BACKTEST_RECENT_BARS_WINDOW = 120` (must be ≥ `HTF_EMA_PERIOD + HTF_SLOPE_LOOKBACK = 60`)

## Code conventions in this repo

- Constants live in [app/constants.py](app/constants.py) with documenting comments explaining the *why* (not the *what*) of each threshold.
- Hold reasons use enum strings from [app/enums.py](app/enums.py) (`HoldStrategy.TREND_FILTER`, etc.).
- Decision engine returns `TradeSignal` (action + entry/stop/target/quantity).
- The bridge gates submission via `DESYNC_PRICE_GUARD_MAX_POINTS` if the live quote drifted too far from the signal entry between bar close and submission.
