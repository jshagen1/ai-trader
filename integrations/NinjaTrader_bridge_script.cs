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

        private double orbHigh = 0;
        private double orbLow = 0;
        private bool orbComplete = false;

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

            if (currentSessionDate.Date != Time[0].Date)
            {
                cumulativePV = 0;
                cumulativeVolume = 0;
                currentSessionDate = Time[0].Date;

                orbHigh = 0;
                orbLow = 0;
                orbComplete = false;

                Print("New session reset: " + Time[0].Date);
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

            TimeSpan currentTime = Time[0].TimeOfDay;

            if (currentTime >= new TimeSpan(8, 30, 0) && currentTime < new TimeSpan(8, 45, 0))
            {
                orbHigh = orbHigh == 0 ? High[0] : Math.Max(orbHigh, High[0]);
                orbLow = orbLow == 0 ? Low[0] : Math.Min(orbLow, Low[0]);
            }

            if (currentTime >= new TimeSpan(8, 45, 0))
                orbComplete = true;

            double safeOrbHigh = orbHigh > 0 ? orbHigh : price;
            double safeOrbLow = orbLow > 0 ? orbLow : price;

            DateTime openTime = new DateTime(Time[0].Year, Time[0].Month, Time[0].Day, 8, 30, 0);
            int minutesAfterOpen = Math.Max(0, (int)(Time[0] - openTime).TotalMinutes);

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

            string json = $@"
            {{
                ""symbol"": ""{Instrument.FullName}"",
                ""timestamp"": ""{Time[0]:O}"",
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

                double stopLoss = ExtractNullableDouble(result, "stop_loss");
                double takeProfit = ExtractNullableDouble(result, "take_profit");

                if (stopLoss <= 0 || takeProfit <= 0)
                {
                    Print(action + " blocked: missing stop_loss or take_profit.");
                    Print("Reason: " + reason);
                    return;
                }

                double currentPrice = Close[0];

                Print("ORDER CHECK | Action: " + action
                    + " | Current: " + currentPrice
                    + " | Stop: " + stopLoss
                    + " | Target: " + takeProfit);

                if (!IsValidOrderPrices(action, currentPrice, stopLoss, takeProfit))
                    return;

                activeStopLoss = stopLoss;
                initialStopLoss = stopLoss;
                activeTakeProfit = takeProfit;

                if (action == "BUY")
                {
                    Print("Submitting BUY | Stop: " + activeStopLoss + " | Target: " + activeTakeProfit);

                    SetStopLoss("AI_LONG", CalculationMode.Price, activeStopLoss, false);
                    SetProfitTarget("AI_LONG", CalculationMode.Price, activeTakeProfit);

                    EnterLong(1, "AI_LONG");
                }
                else if (action == "SELL")
                {
                    Print("Submitting SELL | Stop: " + activeStopLoss + " | Target: " + activeTakeProfit);

                    SetStopLoss("AI_SHORT", CalculationMode.Price, activeStopLoss, false);
                    SetProfitTarget("AI_SHORT", CalculationMode.Price, activeTakeProfit);

                    EnterShort(1, "AI_SHORT");
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

                Print("ENTRY FILLED: BUY @ " + entryPrice);
                return;
            }

            if (orderName == "AI_SHORT")
            {
                entryPrice = price;
                positionSize = quantity;
                lastAction = "SELL";

                Print("ENTRY FILLED: SELL @ " + entryPrice);
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
                lastManageCall = Core.Globals.MinDate;
            }
        }

        private bool IsValidOrderPrices(string action, double currentPrice, double stopLoss, double takeProfit)
        {
            if (currentPrice <= 0)
            {
                Print("Order blocked: current price is invalid.");
                return false;
            }

            if (action == "BUY")
            {
                if (stopLoss >= currentPrice)
                {
                    Print("BUY blocked: stop_loss must be below current price.");
                    return false;
                }

                if (takeProfit <= currentPrice)
                {
                    Print("BUY blocked: take_profit must be above current price.");
                    return false;
                }
            }

            if (action == "SELL")
            {
                if (stopLoss <= currentPrice)
                {
                    Print("SELL blocked: stop_loss must be above current price.");
                    return false;
                }

                if (takeProfit >= currentPrice)
                {
                    Print("SELL blocked: take_profit must be below current price.");
                    return false;
                }
            }

            return true;
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
    }
}
