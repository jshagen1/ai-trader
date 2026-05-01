#region Using declarations
using System;
using System.Net.Http;
using System.Text;
using System.Text.RegularExpressions;
using System.Globalization;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class MES_AI_Bridge : Strategy
    {
        private static readonly HttpClient client = new HttpClient();
        private string apiBaseUrl = "http://192.168.12.247:8000";

        // Pin all session math to US Central Time regardless of the machine's zone or
        // the chart's display zone. Falls back to America/Chicago for non-Windows hosts.
        private static readonly TimeZoneInfo CentralTimeZone = ResolveCentralTimeZone();

        // ORB window is the CME equity-index cash-open: 08:30:00 - 08:45:00 CT.
        private static readonly TimeSpan OrbWindowStart = new TimeSpan(8, 30, 0);
        private static readonly TimeSpan OrbWindowEnd = new TimeSpan(8, 45, 0);

        private double orbHigh = 0;
        private double orbLow = 0;
        private bool orbComplete = false;
        private bool tradingDisabledForDay = false;

        private double cumulativePV = 0;
        private double cumulativeVolume = 0;
        private DateTime currentSessionDate = Core.Globals.MinDate;

        private double entryPrice = 0;
        private double exitPrice = 0;
        private string lastAction = "";
        private int positionSize = 0;

        private double activeStopLoss = 0;
        private double activeTakeProfit = 0;
        private double initialStopLoss = 0;
        private DateTime lastManageCall = Core.Globals.MinDate;

        // Tracks whether we've sent today's historical bars to the API this session.
        // Reset to false on each new trading date so the backfill runs once per session.
        private bool barBackfillDone = false;

        // Stop/target are submitted as tick offsets from entry — these track the points
        // form so OnExecutionUpdate can compute the absolute fill-relative prices for the
        // trailing-stop logic without trusting the now-stale signal `entry`.
        private double pendingRiskPoints = 0;
        private double pendingRewardPoints = 0;

        // If the live quote drifts more than this from the signal entry between bar close
        // and order submission, skip the trade rather than fill at a far-off price.
        private const double MaxSignalDriftPoints = 3.0;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "MES_AI_Bridge";
                Calculate = Calculate.OnBarClose;

                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;

                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;

                IncludeCommission = true;

                // Prevent strategy from terminating automatically on rejected orders.
                // Bad orders are now blocked before submission.
                RealtimeErrorHandling = RealtimeErrorHandling.IgnoreAllErrors;
            }

            if (State == State.Configure)
                Print("MES_AI_Bridge configured");

            if (State == State.DataLoaded)
                Print("MES_AI_Bridge data loaded");

            if (State == State.Realtime)
                Print("MES_AI_Bridge realtime");
        }

        protected override async void OnBarUpdate()
        {
            if (CurrentBar < 50)
                return;

            // Only call the API on the primary series in live mode so JSON price/close match the chart.
            if (State != State.Realtime)
                return;

            if (BarsInProgress != 0)
                return;

            DateTime barTimeCT = ConvertToCentral(Time[0]);
            TimeSpan currentTime = barTimeCT.TimeOfDay;

            if (currentSessionDate.Date != barTimeCT.Date)
            {
                cumulativePV = 0;
                cumulativeVolume = 0;
                currentSessionDate = barTimeCT.Date;

                orbHigh = 0;
                orbLow = 0;
                orbComplete = false;
                tradingDisabledForDay = false;
                barBackfillDone = false; // New trading date — queue a fresh backfill.

                Print("New session reset (CT): " + barTimeCT.Date.ToString("yyyy-MM-dd"));

                // Recover the ORB from history first so orbHigh/orbLow are populated
                // before BackfillBarsToAPI uses them when building bar payloads.
                BackfillOrbFromHistory(barTimeCT.Date);
            }

            // Send today's historical bars to the API once per session.
            // Fires on the first live bar after a date transition (or on cold start).
            // BackfillOrbFromHistory runs synchronously above, so orbHigh/orbLow are
            // ready when BackfillBarsToAPI reads them. Fire-and-forget: bar processing
            // continues immediately while the HTTP POST runs in the background.
            if (!barBackfillDone)
            {
                barBackfillDone = true;
                Print("Backfill trigger: starting bar backfill for "
                    + barTimeCT.Date.ToString("yyyy-MM-dd")
                    + " | CurrentBar=" + CurrentBar
                    + " | orbHigh=" + orbHigh + " orbLow=" + orbLow);
                _ = BackfillBarsToAPI(barTimeCT.Date);
            }

            double price = Close[0];
            string position = Position.MarketPosition.ToString().ToLower();

            double typicalPrice = (High[0] + Low[0] + Close[0]) / 3.0;
            cumulativePV += typicalPrice * Volume[0];
            cumulativeVolume += Volume[0];

            double vwapValue = cumulativeVolume > 0
                ? cumulativePV / cumulativeVolume
                : Close[0];

            double atrValue = ATR(14)[0];
            double rsiValue = RSI(14, 3)[0];
            double avgVolume = SMA(Volume, 20)[0];

            if (currentTime >= OrbWindowStart && currentTime < OrbWindowEnd)
            {
                orbHigh = orbHigh == 0 ? High[0] : Math.Max(orbHigh, High[0]);
                orbLow = orbLow == 0 ? Low[0] : Math.Min(orbLow, Low[0]);
            }

            if (currentTime >= OrbWindowEnd && !orbComplete)
            {
                orbComplete = true;
                if (orbHigh <= 0 || orbLow <= 0)
                {
                    tradingDisabledForDay = true;
                    Print("WARNING: ORB window closed without captured bars (started after 08:45 CT?). "
                        + "Trading disabled for " + barTimeCT.Date.ToString("yyyy-MM-dd"));
                }
                else
                {
                    Print("ORB locked (CT): high=" + orbHigh + " low=" + orbLow);
                }
            }

            double safeOrbHigh = orbHigh > 0 ? orbHigh : price;
            double safeOrbLow = orbLow > 0 ? orbLow : price;

            int minutesAfterOpen = Math.Max(0, (int)(currentTime - OrbWindowStart).TotalMinutes);

            double trendScore = 0.0;

            if (Close[0] > vwapValue)
                trendScore += 0.3;

            if (SMA(20)[0] > SMA(50)[0])
                trendScore += 0.3;

            if (ADX(14)[0] > 20)
                trendScore += 0.4;

            double chopScore = 0.0;

            if (ADX(14)[0] < 18)
                chopScore += 0.4;

            if (Math.Abs(Close[0] - vwapValue) < atrValue * 0.3)
                chopScore += 0.3;

            if (Math.Abs(SMA(20)[0] - SMA(50)[0]) < atrValue * 0.2)
                chopScore += 0.3;

            if (Position.MarketPosition != MarketPosition.Flat)
                await ManageOpenPosition(price, atrValue);

            if (tradingDisabledForDay)
                return;

            string json = $@"
            {{
                ""symbol"": ""{Instrument.FullName}"",
                ""timestamp"": ""{FormatTimestampCT(Time[0])}"",
                ""open"": {FormatDouble(Open[0])},
                ""high"": {FormatDouble(High[0])},
                ""low"": {FormatDouble(Low[0])},
                ""close"": {FormatDouble(Close[0])},
                ""price"": {FormatDouble(price)},
                ""vwap"": {FormatDouble(vwapValue)},
                ""atr"": {FormatDouble(atrValue)},
                ""rsi"": {FormatDouble(rsiValue)},
                ""orb_high"": {FormatDouble(safeOrbHigh)},
                ""orb_low"": {FormatDouble(safeOrbLow)},
                ""volume"": {FormatDouble(Volume[0])},
                ""avg_volume"": {FormatDouble(avgVolume)},
                ""trend_score"": {FormatDouble(trendScore)},
                ""chop_score"": {FormatDouble(chopScore)},
                ""position"": ""{position}"",
                ""minutes_after_open"": {minutesAfterOpen}
            }}";

            try
            {
                Print("OUTGOING JSON: " + json);

                var content = new StringContent(json, Encoding.UTF8, "application/json");
                var response = await client.PostAsync(apiBaseUrl + "/signal", content);
                var result = await response.Content.ReadAsStringAsync();

                Print("Signal response: " + result);

                if (string.IsNullOrWhiteSpace(result))
                {
                    Print("Signal blocked: empty API response.");
                    return;
                }

                if (!response.IsSuccessStatusCode)
                {
                    Print("Signal blocked: API returned status " + response.StatusCode);
                    return;
                }

                if (Position == null)
                {
                    Print("Signal ignored: Position object is null.");
                    return;
                }

                if (Position.MarketPosition != MarketPosition.Flat)
                {
                    Print("Signal ignored: already in position.");
                    return;
                }

                if (!orbComplete)
                {
                    Print("Signal ignored: ORB not complete.");
                    return;
                }

                string action = ExtractString(result, "action").ToUpperInvariant();
                string reason = ExtractString(result, "reason");
                int signalQuantity = ExtractInt(result, "quantity", 1);

                if (string.IsNullOrEmpty(action))
                {
                    Print("Signal blocked: missing action.");
                    return;
                }

                if (action == "HOLD")
                {
                    Print("Signal HOLD: " + reason);
                    return;
                }

                if (action != "BUY" && action != "SELL")
                {
                    Print("Signal blocked: unknown action: " + action);
                    Print("Reason: " + reason);
                    return;
                }

                double signalEntry = ExtractNullableDouble(result, "entry");
                double stopLoss = ExtractNullableDouble(result, "stop_loss");
                double takeProfit = ExtractNullableDouble(result, "take_profit");

                if (signalEntry <= 0 || stopLoss <= 0 || takeProfit <= 0)
                {
                    Print(action + " blocked: missing entry, stop_loss, or take_profit.");
                    Print("Reason: " + reason);
                    return;
                }

                // Validate the SIGNAL's internal geometry (Python's prices, not market).
                if (!IsValidSignalGeometry(action, signalEntry, stopLoss, takeProfit))
                {
                    Print(action + " blocked: signal geometry invalid (entry=" + signalEntry
                        + " stop=" + stopLoss + " target=" + takeProfit + ").");
                    return;
                }

                // Convert absolute prices into point/tick offsets so they apply at the
                // *actual fill price*, not the bar close from a minute ago. This is what
                // fixes "stop_loss above current price" rejections caused by drift during
                // the async round-trip.
                double riskPoints = action == "BUY" ? (signalEntry - stopLoss) : (stopLoss - signalEntry);
                double rewardPoints = action == "BUY" ? (takeProfit - signalEntry) : (signalEntry - takeProfit);
                int riskTicks = (int)Math.Round(riskPoints / TickSize);
                int rewardTicks = (int)Math.Round(rewardPoints / TickSize);

                if (riskTicks <= 0 || rewardTicks <= 0)
                {
                    Print(action + " blocked: non-positive risk/reward ticks "
                        + "(risk=" + riskTicks + " reward=" + rewardTicks + ").");
                    return;
                }

                // Drift guard: if the live quote moved away from the signal entry while we
                // were waiting on Python, skip rather than fill far from the planned level.
                // GetCurrentAsk/GetCurrentBid return the latest tick regardless of bar mode.
                double liveQuote = action == "BUY" ? GetCurrentAsk(0) : GetCurrentBid(0);
                if (liveQuote <= 0)
                    liveQuote = Close[0]; // fall back if level-1 not available

                double drift = Math.Abs(liveQuote - signalEntry);
                if (drift > MaxSignalDriftPoints)
                {
                    Print(action + " skipped: signal entry " + signalEntry
                        + " drifted " + drift.ToString("0.00") + " pts from live "
                        + liveQuote + " (max " + MaxSignalDriftPoints + ").");
                    SendSkipToAPI(action, signalEntry, liveQuote, drift, "drift");
                    return;
                }

                Print("ORDER CHECK | " + action + " | live: " + liveQuote
                    + " | signal entry: " + signalEntry
                    + " | risk: " + riskPoints + "pt (" + riskTicks + "tk)"
                    + " | reward: " + rewardPoints + "pt (" + rewardTicks + "tk)");

                pendingRiskPoints = riskPoints;
                pendingRewardPoints = rewardPoints;

                if (action == "BUY")
                {
                    Print("Submitting BUY x" + signalQuantity + " (stops set as ticks-from-entry)");

                    SetStopLoss("AI_LONG", CalculationMode.Ticks, riskTicks, false);
                    SetProfitTarget("AI_LONG", CalculationMode.Ticks, rewardTicks);

                    EnterLong(signalQuantity, "AI_LONG");
                }
                else if (action == "SELL")
                {
                    Print("Submitting SELL x" + signalQuantity + " (stops set as ticks-from-entry)");

                    SetStopLoss("AI_SHORT", CalculationMode.Ticks, riskTicks, false);
                    SetProfitTarget("AI_SHORT", CalculationMode.Ticks, rewardTicks);

                    EnterShort(signalQuantity, "AI_SHORT");
                }
            }
            catch (Exception ex)
            {
                Print("API FULL ERROR:");
                Print(ex.ToString());

                if (ex.InnerException != null)
                    Print("INNER: " + ex.InnerException.ToString());
            }
        }

        protected override void OnExecutionUpdate(
            Execution execution,
            string executionId,
            double price,
            int quantity,
            MarketPosition marketPosition,
            string orderId,
            DateTime time)
        {
            if (execution == null || execution.Order == null)
                return;

            if (execution.Order.OrderState != OrderState.Filled)
                return;

            string orderName = execution.Order.Name;

            Print("EXECUTION DEBUG:");
            Print("Order name: " + orderName);
            Print("Execution price: " + price);
            Print("Quantity: " + quantity);
            Print("MarketPosition param: " + marketPosition);
            Print("Strategy Position: " + Position.MarketPosition);

            if (orderName == "AI_LONG")
            {
                entryPrice = price;
                positionSize = quantity;
                lastAction = "BUY";

                // NT placed the stop/target as tick-offsets from this fill. Mirror those
                // absolute prices locally so ManageOpenPosition / trailing-stop logic
                // operates against the correct levels.
                activeStopLoss = entryPrice - pendingRiskPoints;
                initialStopLoss = activeStopLoss;
                activeTakeProfit = entryPrice + pendingRewardPoints;

                Print("ENTRY FILLED: BUY @ " + entryPrice
                    + " | stop=" + activeStopLoss + " | target=" + activeTakeProfit);
                return;
            }

            if (orderName == "AI_SHORT")
            {
                entryPrice = price;
                positionSize = quantity;
                lastAction = "SELL";

                activeStopLoss = entryPrice + pendingRiskPoints;
                initialStopLoss = activeStopLoss;
                activeTakeProfit = entryPrice - pendingRewardPoints;

                Print("ENTRY FILLED: SELL @ " + entryPrice
                    + " | stop=" + activeStopLoss + " | target=" + activeTakeProfit);
                return;
            }

            if (positionSize > 0 && entryPrice > 0 && lastAction != "")
            {
                exitPrice = price;

                double pnl = 0;

                if (lastAction == "BUY")
                    pnl = (exitPrice - entryPrice) * 5.0 * positionSize;

                if (lastAction == "SELL")
                    pnl = (entryPrice - exitPrice) * 5.0 * positionSize;

                Print("EXIT FILLED via " + orderName + " @ " + exitPrice + " | PnL: " + pnl);

                SendTradeToAPI(entryPrice, exitPrice, pnl, lastAction, positionSize, time);

                positionSize = 0;
                entryPrice = 0;
                exitPrice = 0;
                lastAction = "";

                activeStopLoss = 0;
                activeTakeProfit = 0;
                initialStopLoss = 0;
                pendingRiskPoints = 0;
                pendingRewardPoints = 0;
                lastManageCall = Core.Globals.MinDate;
            }
        }

        private bool IsValidSignalGeometry(string action, double entry, double stopLoss, double takeProfit)
        {
            // Validates that Python returned a self-consistent signal. Does NOT check
            // against live market — that's enforced by the tick-offset stops at fill time
            // and the drift guard before submission.
            if (entry <= 0 || stopLoss <= 0 || takeProfit <= 0)
                return false;

            if (action == "BUY")
                return stopLoss < entry && takeProfit > entry;

            if (action == "SELL")
                return stopLoss > entry && takeProfit < entry;

            return false;
        }

        private async System.Threading.Tasks.Task ManageOpenPosition(double currentPrice, double atrValue)
        {
            if ((DateTime.Now - lastManageCall).TotalSeconds < 5)
                return;

            lastManageCall = DateTime.Now;

            if (entryPrice <= 0 || initialStopLoss <= 0 || activeStopLoss <= 0)
                return;

            string action = Position.MarketPosition == MarketPosition.Long ? "BUY" : "SELL";

            string json = $@"
            {{
                ""symbol"": ""{Instrument.FullName}"",
                ""action"": ""{action}"",
                ""entry_price"": {FormatDouble(entryPrice)},
                ""current_price"": {FormatDouble(currentPrice)},
                ""stop_loss"": {FormatDouble(activeStopLoss)},
                ""initial_stop"": {FormatDouble(initialStopLoss)},
                ""take_profit"": {FormatDouble(activeTakeProfit)},
                ""atr"": {FormatDouble(atrValue)}
            }}";

            try
            {
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                var response = await client.PostAsync(apiBaseUrl + "/manage-position", content);
                var result = await response.Content.ReadAsStringAsync();

                Print("Manage response: " + result);

                if (string.IsNullOrWhiteSpace(result))
                {
                    Print("Manage ignored: empty API response.");
                    return;
                }

                if (!response.IsSuccessStatusCode)
                {
                    Print("Manage ignored: API returned status " + response.StatusCode);
                    return;
                }

                string updateAction = ExtractString(result, "action").ToUpperInvariant();
                double newStopLoss = ExtractNullableDouble(result, "new_stop_loss");

                if (updateAction != "UPDATE_STOP" || newStopLoss <= 0)
                    return;

                if (Position.MarketPosition == MarketPosition.Long)
                {
                    if (newStopLoss > activeStopLoss && newStopLoss < currentPrice)
                    {
                        activeStopLoss = newStopLoss;
                        SetStopLoss("AI_LONG", CalculationMode.Price, activeStopLoss, false);
                        Print("Updated LONG stop to: " + activeStopLoss);
                    }
                }

                if (Position.MarketPosition == MarketPosition.Short)
                {
                    if (newStopLoss < activeStopLoss && newStopLoss > currentPrice)
                    {
                        activeStopLoss = newStopLoss;
                        SetStopLoss("AI_SHORT", CalculationMode.Price, activeStopLoss, false);
                        Print("Updated SHORT stop to: " + activeStopLoss);
                    }
                }
            }
            catch (Exception ex)
            {
                Print("Manage position API error:");
                Print(ex.ToString());
            }
        }

        private async void SendSkipToAPI(
            string action,
            double signalEntry,
            double liveQuote,
            double drift,
            string reason)
        {
            try
            {
                string json = $@"
                {{
                    ""timestamp"": ""{FormatTimestampCT(Time[0])}"",
                    ""symbol"": ""{Instrument.FullName}"",
                    ""action"": ""{action}"",
                    ""signal_entry"": {FormatDouble(signalEntry)},
                    ""live_quote"": {FormatDouble(liveQuote)},
                    ""drift_points"": {FormatDouble(drift)},
                    ""reason"": ""{reason}""
                }}";

                var content = new StringContent(json, Encoding.UTF8, "application/json");
                await client.PostAsync(apiBaseUrl + "/skip", content);
            }
            catch (Exception ex)
            {
                Print("Skip log API error: " + ex.Message);
            }
        }

        private async void SendTradeToAPI(
            double entry,
            double exit,
            double pnl,
            string action,
            int quantity,
            DateTime time)
        {
            try
            {
                string json = $@"
                {{
                    ""symbol"": ""{Instrument.FullName}"",
                    ""timestamp"": ""{time:O}"",
                    ""action"": ""{action}"",
                    ""entry_price"": {FormatDouble(entry)},
                    ""exit_price"": {FormatDouble(exit)},
                    ""pnl"": {FormatDouble(pnl)},
                    ""quantity"": {quantity}
                }}";

                var content = new StringContent(json, Encoding.UTF8, "application/json");
                var response = await client.PostAsync(apiBaseUrl + "/trade", content);
                var result = await response.Content.ReadAsStringAsync();

                Print("Trade logged: " + result);

                if (!response.IsSuccessStatusCode)
                    Print("Trade log API status: " + response.StatusCode);
            }
            catch (Exception ex)
            {
                Print("Trade API error:");
                Print(ex.ToString());
            }
        }

        private string ExtractString(string json, string key)
        {
            if (string.IsNullOrWhiteSpace(json) || string.IsNullOrWhiteSpace(key))
                return "";

            Match match = Regex.Match(json, "\"" + key + "\"\\s*:\\s*\"([^\"]*)\"");
            return match.Success ? match.Groups[1].Value : "";
        }

        private int ExtractInt(string json, string key, int defaultValue)
        {
            double raw = ExtractNullableDouble(json, key);
            return raw > 0 ? (int)raw : defaultValue;
        }

        private double ExtractNullableDouble(string json, string key)
        {
            if (string.IsNullOrWhiteSpace(json) || string.IsNullOrWhiteSpace(key))
                return 0;

            Match match = Regex.Match(json, "\"" + key + "\"\\s*:\\s*(null|-?\\d+(\\.\\d+)?)");

            if (!match.Success || match.Groups[1].Value == "null")
                return 0;

            double value;

            if (double.TryParse(match.Groups[1].Value, NumberStyles.Any, CultureInfo.InvariantCulture, out value))
                return value;

            return 0;
        }

        private string FormatDouble(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value))
                return "0";

            return value.ToString(CultureInfo.InvariantCulture);
        }

        private static TimeZoneInfo ResolveCentralTimeZone()
        {
            try
            {
                return TimeZoneInfo.FindSystemTimeZoneById("Central Standard Time");
            }
            catch (TimeZoneNotFoundException)
            {
                return TimeZoneInfo.FindSystemTimeZoneById("America/Chicago");
            }
        }

        private DateTime ConvertToCentral(DateTime t)
        {
            // Time[0] in NinjaTrader is typically Unspecified (chart-zone clock).
            // Treat Unspecified as Local (the chart's zone), then convert to CT.
            if (t.Kind == DateTimeKind.Utc)
                return TimeZoneInfo.ConvertTimeFromUtc(t, CentralTimeZone);

            DateTime asLocal = t.Kind == DateTimeKind.Local
                ? t
                : DateTime.SpecifyKind(t, DateTimeKind.Local);

            return TimeZoneInfo.ConvertTime(asLocal, TimeZoneInfo.Local, CentralTimeZone);
        }

        private string FormatTimestampCT(DateTime t)
        {
            DateTime ct = ConvertToCentral(t);
            TimeSpan offset = CentralTimeZone.GetUtcOffset(ct);
            DateTimeOffset dto = new DateTimeOffset(ct, offset);
            return dto.ToString("yyyy-MM-ddTHH:mm:ss.fffffffzzz", CultureInfo.InvariantCulture);
        }

        private async System.Threading.Tasks.Task BackfillBarsToAPI(DateTime sessionDateCT)
        {
            // Active trade window: ORB open → ES regular session close.
            // Pre-market and overnight bars are excluded because they have no ORB context
            // and would pollute the decision engine's recent_bars window with stale data.
            TimeSpan activeStart = new TimeSpan(8, 30, 0);   // 08:30 CT — ORB window opens
            TimeSpan activeEnd   = new TimeSpan(15, 15, 0);  // 15:15 CT — ES regular session close

            // Collect today's active-hour bar indices. Time[0] = current bar, Time[n] = n bars ago,
            // so this list is ordered newest-first. We reverse it for chronological VWAP accumulation.
            var indices = new System.Collections.Generic.List<int>();
            for (int bb = 1; bb <= CurrentBar && bb <= 700; bb++)
            {
                DateTime ct = ConvertToCentral(Time[bb]);
                if (ct.Date < sessionDateCT) break;           // Passed into the previous session — stop.
                if (ct.Date == sessionDateCT)
                {
                    TimeSpan tod = ct.TimeOfDay;
                    if (tod >= activeStart && tod < activeEnd)
                        indices.Add(bb);
                }
            }

            if (indices.Count == 0)
            {
                Print("Backfill: no active-session bars found for " + sessionDateCT.ToString("yyyy-MM-dd")
                    + " — session may have just opened.");
                return;
            }

            // Reverse to chronological (oldest → newest) so VWAP accumulates correctly.
            indices.Reverse();

            // Recompute session VWAP from the first bar of the day forward.
            // This mirrors the live cumulativePV / cumulativeVolume logic exactly so
            // the stored vwap values match what the live feed would have produced.
            double pv  = 0.0;
            double vol = 0.0;

            var barJsons = new System.Collections.Generic.List<string>();

            foreach (int bb in indices)
            {
                DateTime ct = ConvertToCentral(Time[bb]);

                double typical = (High[bb] + Low[bb] + Close[bb]) / 3.0;
                pv  += typical * Volume[bb];
                vol += Volume[bb];
                double vwap = vol > 0 ? pv / vol : Close[bb];

                double atr    = ATR(14)[bb];
                double rsi    = RSI(14, 3)[bb];
                double avgVol = SMA(Volume, 20)[bb];

                // Trend and chop scores — same formula as OnBarUpdate so historical bars
                // are scored identically to bars the live feed would have produced.
                double trendScore = 0.0;
                if (Close[bb] > vwap)              trendScore += 0.3;
                if (SMA(20)[bb] > SMA(50)[bb])     trendScore += 0.3;
                if (ADX(14)[bb] > 20)              trendScore += 0.4;

                double chopScore = 0.0;
                if (ADX(14)[bb] < 18)                                   chopScore += 0.4;
                if (Math.Abs(Close[bb] - vwap)         < atr * 0.3)    chopScore += 0.3;
                if (Math.Abs(SMA(20)[bb] - SMA(50)[bb]) < atr * 0.2)   chopScore += 0.3;

                // Use the final backfilled ORB for all bars. Bars inside the ORB window
                // (minutes_after_open < 15) do not trigger ORB entries anyway, so the
                // orb_high/orb_low value there is not load-bearing for the signal engine.
                double orbH = orbHigh > 0 ? orbHigh : Close[bb];
                double orbL = orbLow  > 0 ? orbLow  : Close[bb];

                int minutesAfterOpen = Math.Max(0, (int)(ct.TimeOfDay - activeStart).TotalMinutes);

                barJsons.Add($@"{{
                    ""symbol"": ""{Instrument.FullName}"",
                    ""timestamp"": ""{FormatTimestampCT(Time[bb])}"",
                    ""open"":  {FormatDouble(Open[bb])},
                    ""high"":  {FormatDouble(High[bb])},
                    ""low"":   {FormatDouble(Low[bb])},
                    ""close"": {FormatDouble(Close[bb])},
                    ""price"": {FormatDouble(Close[bb])},
                    ""vwap"":  {FormatDouble(vwap)},
                    ""atr"":   {FormatDouble(atr)},
                    ""rsi"":   {FormatDouble(rsi)},
                    ""orb_high"":   {FormatDouble(orbH)},
                    ""orb_low"":    {FormatDouble(orbL)},
                    ""volume"":     {FormatDouble(Volume[bb])},
                    ""avg_volume"": {FormatDouble(avgVol)},
                    ""trend_score"": {FormatDouble(trendScore)},
                    ""chop_score"":  {FormatDouble(chopScore)},
                    ""position"": ""flat"",
                    ""minutes_after_open"": {minutesAfterOpen}
                }}");
            }

            string json = "[" + string.Join(",\n", barJsons) + "]";

            try
            {
                Print("Backfill: sending " + indices.Count + " bars for " + sessionDateCT.ToString("yyyy-MM-dd"));
                var content  = new StringContent(json, Encoding.UTF8, "application/json");
                var response = await client.PostAsync(apiBaseUrl + "/backfill", content);
                var result   = await response.Content.ReadAsStringAsync();
                Print("Backfill response: " + result);
            }
            catch (Exception ex)
            {
                Print("Backfill API error: " + ex.Message);
            }
        }

        private void BackfillOrbFromHistory(DateTime sessionDateCT)
        {
            // Walk back through loaded bars looking for today's 08:30-08:45 CT window.
            // CurrentBar is the index of the bar in OnBarUpdate; older bars are at higher indices.
            int scanned = 0;
            int barsBack = 1; // skip current bar (it's the one being processed)

            while (barsBack <= CurrentBar && scanned < 600)
            {
                DateTime candidate = ConvertToCentral(Time[barsBack]);

                if (candidate.Date < sessionDateCT)
                    break;

                if (candidate.Date == sessionDateCT)
                {
                    TimeSpan tod = candidate.TimeOfDay;
                    if (tod >= OrbWindowStart && tod < OrbWindowEnd)
                    {
                        double h = High[barsBack];
                        double l = Low[barsBack];
                        orbHigh = orbHigh == 0 ? h : Math.Max(orbHigh, h);
                        orbLow = orbLow == 0 ? l : Math.Min(orbLow, l);
                    }
                }

                barsBack++;
                scanned++;
            }

            DateTime currentCT = ConvertToCentral(Time[0]);
            if (currentCT.TimeOfDay >= OrbWindowEnd)
            {
                orbComplete = true;
                if (orbHigh > 0 && orbLow > 0)
                {
                    Print("ORB backfilled from history: high=" + orbHigh + " low=" + orbLow);
                }
                else
                {
                    tradingDisabledForDay = true;
                    Print("WARNING: started after 08:45 CT and no ORB bars in history. "
                        + "Trading disabled for " + sessionDateCT.ToString("yyyy-MM-dd"));
                }
            }
        }
    }
}
