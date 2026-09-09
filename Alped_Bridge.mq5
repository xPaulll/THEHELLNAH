//+------------------------------------------------------------------+
//|                                              Alped_Bridge.mq5    |
//|                        Copyright 2026, Alped Punya V3 Systems.   |
//|                                      https://alpedpunyav3.io     |
//+------------------------------------------------------------------+
#property copyright   "Copyright 2026, Alped Punya V3 Systems."
#property link        "https://alpedpunyav3.io"
#property version     "3.00"
#property description "Alped Punya V3 — Raw Data Infrastructure Bridge EA"
#property description "Pure Raw Data Collector. Zero Strategy/Indicator/Execution logic."

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "=== FASTAPI BACKEND CONFIGURATION ==="
input string   InpFastApiUrl        = "http://127.0.0.1:8000";     // FastAPI Base URL
input string   InpApiKey            = "alped_secret_key_v3_secure"; // X-API-Key Secret
input string   InpEaIdentifier      = "ALPED_BRIDGE_01";           // Unique EA Instance ID
input string   InpEnvironment       = "DEMO";                      // DEMO or REAL

input group "=== SYNC & QUEUE CONFIGURATION ==="
input int      InpInitialSyncBars   = 0;                           // Initial Historical Bars (0 = Pure Live Mode, or e.g. 50/1000)
input int      InpBatchSize         = 200;                         // Max Bars Per Batch
input int      InpQueueCapacity     = 2000;                        // Max In-Memory Candle Queue
input int      InpEventQueueMax     = 500;                         // Max In-Memory Event Queue

input group "=== TIMEFRAMES SUBSCRIPTION ==="
input bool     InpEnableM1          = true;                        // Sync M1
input bool     InpEnableM5          = true;                        // Sync M5
input bool     InpEnableM15         = true;                        // Sync M15
input bool     InpEnableM30         = true;                        // Sync M30
input bool     InpEnableH1          = true;                        // Sync H1
input bool     InpEnableH4          = true;                        // Sync H4
input bool     InpEnableD1          = true;                        // Sync D1

input group "=== TICK DATA CONFIGURATION ==="
enum ENUM_TICK_MODE
{
   TICK_MODE_OFF    = 0, // TICK_OFF (Recommended for V1)
   TICK_MODE_SAMPLE = 1, // TICK_SAMPLE
   TICK_MODE_FULL   = 2  // TICK_FULL
};
input ENUM_TICK_MODE InpTickMode    = TICK_MODE_OFF;               // Tick Collection Mode

input group "=== SAFETY LOCK (V1 MANDATE) ==="
input bool     AllowExecution       = false;                       // Execution Locked (Strictly False)

//+------------------------------------------------------------------+
//| CONSTANTS & DATA STRUCTURES                                      |
//+------------------------------------------------------------------+
#define EA_VERSION          "3.0.0"
#define PAYLOAD_VERSION     "1.0.0"
#define SCHEMA_VERSION      "1.0.0"

enum ENUM_BRIDGE_STATE
{
   STATE_UNINITIALIZED    = 0,
   STATE_SYNCING_SYMBOLS  = 1,
   STATE_HISTORICAL_SYNC  = 2,
   STATE_LIVE_RUNNING     = 3
};

struct CandleRecord
{
   long     epoch;
   string   utc_time;
   string   broker_time;
   int      gmt_offset;
   double   open;
   double   high;
   double   low;
   double   close;
   long     volume;
   int      spread;
   string   timeframe;
};

struct PositionEventRecord
{
   long     ticket;
   string   event_type; // OPEN, MODIFY_SL, MODIFY_TP, PARTIAL_CLOSE, CLOSE
   string   symbol;
   double   lots;
   double   price;
   double   sl;
   double   tp;
   double   profit;
   long     epoch;
   string   utc_time;
};

struct TickRecord
{
   long     epoch;
   string   utc_time;
   string   broker_time;
   double   bid;
   double   ask;
   double   last;
   long     volume;
};

//+------------------------------------------------------------------+
//| GLOBAL RUNTIME STATE                                             |
//+------------------------------------------------------------------+
ENUM_BRIDGE_STATE g_State             = STATE_UNINITIALIZED;
string            g_SourceId          = "";
datetime          g_LastHeartbeatTime = 0;
int               g_BackoffSeconds    = 1;
datetime          g_NextAllowedNetOp  = 0;

// Timeframe tracking arrays
ENUM_TIMEFRAMES   g_SubscribedTFs[7];
string            g_TfNames[7];
int               g_SubscribedTfCount = 0;
datetime          g_LastBarTimes[7];
int               g_CurrentSyncTfIdx  = 0;

// Memory Queues
CandleRecord        g_CandleQueue[];
int                 g_CandleQueueCount = 0;

PositionEventRecord g_EventQueue[];
int                 g_EventQueueCount  = 0;

TickRecord          g_TickQueue[];
int                 g_TickQueueCount   = 0;

// Live Market Tracking (Zero Supabase writes)
double              g_LastBid          = 0.0;
double              g_LastAsk          = 0.0;
int                 g_LastSpread       = 0;
datetime            g_LastTickTime     = 0;
bool                g_LiveMarketDirty  = false;

//+------------------------------------------------------------------+
//| UTILITY FUNCTIONS                                                |
//+------------------------------------------------------------------+
string EscapeJsonString(string text)
{
   StringReplace(text, "\\", "\\\\");
   StringReplace(text, "\"", "\\\"");
   StringReplace(text, "\r", "");
   StringReplace(text, "\n", "\\n");
   return text;
}

int GetBrokerGmtOffset()
{
   datetime srv = TimeTradeServer();
   if(srv == 0) srv = TimeCurrent();
   datetime gmt = TimeGMT();
   if(gmt == 0) return 0;

   // Difference between Broker Server Time and GMT/UTC in seconds
   int diff_sec = (int)(srv - gmt);
   // Round to nearest whole hour (e.g. +3 hours = +10800s)
   int rounded_hours = (int)MathRound((double)diff_sec / 3600.0);
   return rounded_hours * 3600;
}

string FormatUtcIso(datetime t, int gmt_offset)
{
   // Convert broker time to UTC timestamp
   datetime utc = t - gmt_offset;
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ", dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec);
}

string FormatLocalBrokerTime(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return StringFormat("%04d-%02d-%02d %02d:%02d:%02d", dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec);
}

string ComputeAccountHash()
{
   long acc = AccountInfoInteger(ACCOUNT_LOGIN);
   string acc_str = IntegerToString(acc);
   uchar key[], data[], result[];
   StringToCharArray(acc_str, data, 0, StringLen(acc_str), CP_UTF8);
   ArrayResize(key, 0);
   CryptEncode(CRYPT_HASH_SHA256, data, key, result);
   string hash = "";
   for(int i = 0; i < ArraySize(result); i++)
      hash += StringFormat("%02x", result[i]);
   return hash;
}

//+------------------------------------------------------------------+
//| HTTP WEB REQUEST (RESTRICTED SOLELY TO OnTimer())                |
//+------------------------------------------------------------------+
bool HttpSend(string method, string endpoint, string json_body, string &response_out, int &http_code)
{
   string url = InpFastApiUrl + endpoint;
   string headers = "Content-Type: application/json\r\n" +
                    "X-API-Key: " + InpApiKey + "\r\n" +
                    "X-EA-Identifier: " + InpEaIdentifier + "\r\n" +
                    "X-EA-Version: " + EA_VERSION + "\r\n" +
                    "X-Payload-Version: " + PAYLOAD_VERSION + "\r\n";

   char post_data[];
   char result_data[];
   string resp_headers = "";
   StringToCharArray(json_body, post_data, 0, StringLen(json_body), CP_UTF8);

   ResetLastError();
   // 10000ms (10 seconds) timeout to accommodate database transactions
   http_code = WebRequest(method, url, headers, 10000, post_data, result_data, resp_headers);

   // Check for MQL5 internal timeout (code 1003)
   if(http_code == 1003 || (http_code == -1 && GetLastError() == 1003))
   {
      Print("[Alped_Bridge] Network timeout (MQL5 1003). Server connection delayed, will retry automatically.");
      return false;
   }

   if(http_code == -1)
   {
      int err = GetLastError();
      PrintFormat("[Alped_Bridge] WebRequest failed. Error code: %d. (Pastikan '%s' diizinkan di Tools -> Options -> Expert Advisors)", err, InpFastApiUrl);
      return false;
   }

   if(http_code >= 1000)
   {
      PrintFormat("[Alped_Bridge] MQL5 internal network code %d (not server HTTP error). Retrying...", http_code);
      return false;
   }

   response_out = CharArrayToString(result_data, 0, WHOLE_ARRAY, CP_UTF8);
   if(http_code < 200 || http_code >= 300)
   {
      PrintFormat("[Alped_Bridge] Server returned HTTP %d: %s", http_code, response_out);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| QUEUE OPERATIONS (NON-BLOCKING MEMORY OPERATIONS)                |
//+------------------------------------------------------------------+
void EnqueueCandle(const CandleRecord &rec)
{
   if(g_CandleQueueCount >= InpQueueCapacity)
   {
      Print("[Alped_Bridge] WARNING: CandleQueue overflow! Dropping oldest bar.");
      for(int i = 0; i < g_CandleQueueCount - 1; i++)
         g_CandleQueue[i] = g_CandleQueue[i + 1];
      g_CandleQueue[g_CandleQueueCount - 1] = rec;
      return;
   }

   ArrayResize(g_CandleQueue, g_CandleQueueCount + 1);
   g_CandleQueue[g_CandleQueueCount] = rec;
   g_CandleQueueCount++;
}

void EnqueuePositionEvent(const PositionEventRecord &ev)
{
   if(g_EventQueueCount >= InpEventQueueMax)
   {
      Print("[Alped_Bridge] WARNING: EventQueue overflow! Dropping oldest event.");
      for(int i = 0; i < g_EventQueueCount - 1; i++)
         g_EventQueue[i] = g_EventQueue[i + 1];
      g_EventQueue[g_EventQueueCount - 1] = ev;
      return;
   }

   ArrayResize(g_EventQueue, g_EventQueueCount + 1);
   g_EventQueue[g_EventQueueCount] = ev;
   g_EventQueueCount++;
}

void EnqueueTick(const TickRecord &tk)
{
   if(InpTickMode == TICK_MODE_OFF) return;
   if(g_TickQueueCount >= 500) return; // Drop if buffer is full

   ArrayResize(g_TickQueue, g_TickQueueCount + 1);
   g_TickQueue[g_TickQueueCount] = tk;
   g_TickQueueCount++;
}

//+------------------------------------------------------------------+
//| STEP 1: HANDSHAKE OPERATION                                      |
//+------------------------------------------------------------------+
bool PerformHandshake()
{
   string broker = AccountInfoString(ACCOUNT_COMPANY);
   string acc_hash = ComputeAccountHash();

   string json = "{\n" +
                 "  \"broker\": \"" + EscapeJsonString(broker) + "\",\n" +
                 "  \"environment\": \"" + InpEnvironment + "\",\n" +
                 "  \"account_hash\": \"" + acc_hash + "\",\n" +
                 "  \"ea_identifier\": \"" + InpEaIdentifier + "\",\n" +
                 "  \"ea_version\": \"" + EA_VERSION + "\",\n" +
                 "  \"payload_version\": \"" + PAYLOAD_VERSION + "\",\n" +
                 "  \"schema_version\": \"" + SCHEMA_VERSION + "\"\n" +
                 "}";

   string response;
   int code;
   if(!HttpSend("POST", "/api/v1/auth/handshake", json, response, code))
      return false;

   // Simple parse of source_id: "source_id":"..."
   int pos = StringFind(response, "\"source_id\":\"");
   if(pos != -1)
   {
      int start = pos + 13;
      int end = StringFind(response, "\"", start);
      if(end != -1)
      {
         g_SourceId = StringSubstr(response, start, end - start);
         PrintFormat("[Alped_Bridge] Handshake SUCCESS! Assigned source_id: %s", g_SourceId);
         return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| STEP 2: SYMBOL SPECIFICATION SYNC                                |
//+------------------------------------------------------------------+
bool SyncSymbolMetadata()
{
   string sym = _Symbol;
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   double contract_size = SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE);
   double vol_min = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vol_max = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double vol_step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   int stop_level = (int)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
   int trade_mode = (int)SymbolInfoInteger(sym, SYMBOL_TRADE_MODE);
   string cur_base = SymbolInfoString(sym, SYMBOL_CURRENCY_BASE);
   string cur_profit = SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT);

   string json = "{\n" +
                 "  \"source_id\": \"" + g_SourceId + "\",\n" +
                 "  \"symbol\": \"" + sym + "\",\n" +
                 "  \"digits\": " + IntegerToString(digits) + ",\n" +
                 "  \"point\": " + DoubleToString(point, digits) + ",\n" +
                 "  \"contract_size\": " + DoubleToString(contract_size, 2) + ",\n" +
                 "  \"volume_min\": " + DoubleToString(vol_min, 2) + ",\n" +
                 "  \"volume_max\": " + DoubleToString(vol_max, 2) + ",\n" +
                 "  \"volume_step\": " + DoubleToString(vol_step, 2) + ",\n" +
                 "  \"stop_level\": " + IntegerToString(stop_level) + ",\n" +
                 "  \"trade_mode\": " + IntegerToString(trade_mode) + ",\n" +
                 "  \"currency_base\": \"" + cur_base + "\",\n" +
                 "  \"currency_profit\": \"" + cur_profit + "\",\n" +
                 "  \"payload_version\": \"" + PAYLOAD_VERSION + "\",\n" +
                 "  \"schema_version\": \"" + SCHEMA_VERSION + "\"\n" +
                 "}";

   string response;
   int code;
   if(HttpSend("POST", "/api/v1/symbols", json, response, code))
   {
      PrintFormat("[Alped_Bridge] Symbol specs for %s synced successfully.", sym);
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| STEP 3: HISTORICAL & RECOVERY SYNC                               |
//+------------------------------------------------------------------+
bool PerformTfHistoricalSync(ENUM_TIMEFRAMES tf, string tf_name)
{
   string endpoint = "/api/v1/candles/sync-status?source_id=" + g_SourceId +
                     "&symbol=" + _Symbol + "&timeframe=" + tf_name;
   string response;
   int code;
   if(!HttpSend("GET", endpoint, "", response, code))
      return false;

   long latest_epoch = 0;
   int pos = StringFind(response, "\"latest_candle_epoch\":");
   if(pos != -1)
   {
      int start = pos + 22;
      int end = StringFind(response, ",", start);
      if(end == -1) end = StringFind(response, "}", start);
      string epoch_str = StringSubstr(response, start, end - start);
      StringTrimLeft(epoch_str);
      StringTrimRight(epoch_str);
      if(epoch_str != "null" && epoch_str != "")
         latest_epoch = StringToInteger(epoch_str);
   }

   int bars_to_fetch = InpInitialSyncBars;
   if(bars_to_fetch <= 0)
   {
      PrintFormat("[Alped_Bridge] InitialSyncBars is 0. Skipping historical bars for %s (Pure Live Mode).", tf_name);
      return true;
   }
   string sync_type = "INITIAL_SYNC";

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, tf, 1, bars_to_fetch, rates);
   if(copied <= 0) return true; // No data to copy

   int gmt_offset = GetBrokerGmtOffset();

   // Send in chunks of InpBatchSize
   for(int i = copied - 1; i >= 0; i -= InpBatchSize)
   {
      int chunk_end = MathMax(0, i - InpBatchSize + 1);
      string batch_json = "{\n" +
                          "  \"source_id\": \"" + g_SourceId + "\",\n" +
                          "  \"symbol\": \"" + _Symbol + "\",\n" +
                          "  \"timeframe\": \"" + tf_name + "\",\n" +
                          "  \"sync_type\": \"" + sync_type + "\",\n" +
                          "  \"payload_version\": \"" + PAYLOAD_VERSION + "\",\n" +
                          "  \"schema_version\": \"" + SCHEMA_VERSION + "\",\n" +
                          "  \"candles\": [\n";

      for(int j = i; j >= chunk_end; j--)
      {
         datetime b_time = rates[j].time;
         long epoch = (long)(b_time - gmt_offset);
         string utc_str = FormatUtcIso(b_time, gmt_offset);
         string b_str = FormatLocalBrokerTime(b_time);
         int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

         batch_json += "    {\n" +
                       "      \"candle_time_epoch\": " + IntegerToString(epoch) + ",\n" +
                       "      \"candle_time_utc\": \"" + utc_str + "\",\n" +
                       "      \"broker_time\": \"" + b_str + "\",\n" +
                       "      \"broker_gmt_offset\": " + IntegerToString(gmt_offset) + ",\n" +
                       "      \"open\": " + DoubleToString(rates[j].open, digits) + ",\n" +
                       "      \"high\": " + DoubleToString(rates[j].high, digits) + ",\n" +
                       "      \"low\": " + DoubleToString(rates[j].low, digits) + ",\n" +
                       "      \"close\": " + DoubleToString(rates[j].close, digits) + ",\n" +
                       "      \"tick_volume\": " + IntegerToString(rates[j].tick_volume) + ",\n" +
                       "      \"spread\": " + IntegerToString(rates[j].spread) + "\n" +
                       "    }" + (j > chunk_end ? "," : "") + "\n";
      }
      batch_json += "  ]\n}";

      string resp;
      int c_code;
      HttpSend("POST", "/api/v1/candles/batch", batch_json, resp, c_code);
   }

   PrintFormat("[Alped_Bridge] Synced %d historical bars for %s (%s).", copied, _Symbol, tf_name);
   return true;
}

//+------------------------------------------------------------------+
//| STEP 4: FLUSH QUEUES IN OnTimer()                                |
//+------------------------------------------------------------------+
void FlushCandleQueue()
{
   if(g_CandleQueueCount == 0) return;

   int chunk_size = MathMin(g_CandleQueueCount, 50);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   // Group or send first chunk's timeframe
   string target_tf = g_CandleQueue[0].timeframe;
   int sent_count = 0;

   string batch_json = "{\n" +
                       "  \"source_id\": \"" + g_SourceId + "\",\n" +
                       "  \"symbol\": \"" + _Symbol + "\",\n" +
                       "  \"timeframe\": \"" + target_tf + "\",\n" +
                       "  \"sync_type\": \"LIVE_SYNC\",\n" +
                       "  \"payload_version\": \"" + PAYLOAD_VERSION + "\",\n" +
                       "  \"schema_version\": \"" + SCHEMA_VERSION + "\",\n" +
                       "  \"candles\": [\n";

   for(int i = 0; i < g_CandleQueueCount && sent_count < chunk_size; i++)
   {
      if(g_CandleQueue[i].timeframe != target_tf) continue;

      CandleRecord c = g_CandleQueue[i];
      batch_json += (sent_count > 0 ? ",\n" : "") +
                    "    {\n" +
                    "      \"candle_time_epoch\": " + IntegerToString(c.epoch) + ",\n" +
                    "      \"candle_time_utc\": \"" + c.utc_time + "\",\n" +
                    "      \"broker_time\": \"" + c.broker_time + "\",\n" +
                    "      \"broker_gmt_offset\": " + IntegerToString(c.gmt_offset) + ",\n" +
                    "      \"open\": " + DoubleToString(c.open, digits) + ",\n" +
                    "      \"high\": " + DoubleToString(c.high, digits) + ",\n" +
                    "      \"low\": " + DoubleToString(c.low, digits) + ",\n" +
                    "      \"close\": " + DoubleToString(c.close, digits) + ",\n" +
                    "      \"tick_volume\": " + IntegerToString(c.volume) + ",\n" +
                    "      \"spread\": " + IntegerToString(c.spread) + "\n" +
                    "    }";
      sent_count++;
   }
   batch_json += "\n  ]\n}";

   string resp;
   int code;
   if(HttpSend("POST", "/api/v1/candles/batch", batch_json, resp, code))
   {
      // Shift remaining items
      int new_count = 0;
      for(int i = 0; i < g_CandleQueueCount; i++)
      {
         if(g_CandleQueue[i].timeframe == target_tf && sent_count > 0)
         {
            sent_count--;
            continue;
         }
         g_CandleQueue[new_count++] = g_CandleQueue[i];
      }
      g_CandleQueueCount = new_count;
      ArrayResize(g_CandleQueue, g_CandleQueueCount);
   }
   else if(code == 404)
   {
      Print("[Alped_Bridge] Source not registered (HTTP 404). Transitioning to STATE_UNINITIALIZED to re-handshake.");
      g_State = STATE_UNINITIALIZED;
   }
}

void FlushEventQueue()
{
   if(g_EventQueueCount == 0) return;

   int chunk_size = MathMin(g_EventQueueCount, 20);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   string json = "{\n" +
                 "  \"source_id\": \"" + g_SourceId + "\",\n" +
                 "  \"payload_version\": \"" + PAYLOAD_VERSION + "\",\n" +
                 "  \"schema_version\": \"" + SCHEMA_VERSION + "\",\n" +
                 "  \"events\": [\n";

   for(int i = 0; i < chunk_size; i++)
   {
      PositionEventRecord ev = g_EventQueue[i];
      json += (i > 0 ? ",\n" : "") +
              "    {\n" +
              "      \"ticket\": " + IntegerToString(ev.ticket) + ",\n" +
              "      \"event_type\": \"" + ev.event_type + "\",\n" +
              "      \"symbol\": \"" + ev.symbol + "\",\n" +
              "      \"lots\": " + DoubleToString(ev.lots, 2) + ",\n" +
              "      \"price\": " + DoubleToString(ev.price, digits) + ",\n" +
              "      \"sl\": " + DoubleToString(ev.sl, digits) + ",\n" +
              "      \"tp\": " + DoubleToString(ev.tp, digits) + ",\n" +
              "      \"profit\": " + DoubleToString(ev.profit, 2) + ",\n" +
              "      \"event_time_epoch\": " + IntegerToString(ev.epoch) + ",\n" +
              "      \"event_time_utc\": \"" + ev.utc_time + "\"\n" +
              "    }";
   }
   json += "\n  ]\n}";

   string resp;
   int code;
   if(HttpSend("POST", "/api/v1/positions/events", json, resp, code))
   {
      int remaining = g_EventQueueCount - chunk_size;
      for(int i = 0; i < remaining; i++)
         g_EventQueue[i] = g_EventQueue[i + chunk_size];
      g_EventQueueCount = remaining;
      ArrayResize(g_EventQueue, g_EventQueueCount);
   }
   else if(code == 404)
   {
      Print("[Alped_Bridge] Source not registered (HTTP 404). Transitioning to STATE_UNINITIALIZED to re-handshake.");
      g_State = STATE_UNINITIALIZED;
   }
}

void SendAccountHeartbeat()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin = AccountInfoDouble(ACCOUNT_MARGIN);
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double margin_level = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   int total_pos = PositionsTotal();

   string json = "{\n" +
                 "  \"source_id\": \"" + g_SourceId + "\",\n" +
                 "  \"payload_version\": \"" + PAYLOAD_VERSION + "\",\n" +
                 "  \"schema_version\": \"" + SCHEMA_VERSION + "\",\n" +
                 "  \"account\": {\n" +
                 "    \"balance\": " + DoubleToString(balance, 2) + ",\n" +
                 "    \"equity\": " + DoubleToString(equity, 2) + ",\n" +
                 "    \"margin\": " + DoubleToString(margin, 2) + ",\n" +
                 "    \"free_margin\": " + DoubleToString(free_margin, 2) + ",\n" +
                 "    \"margin_level\": " + DoubleToString(margin_level, 2) + ",\n" +
                 "    \"open_positions_count\": " + IntegerToString(total_pos) + "\n" +
                 "  },\n" +
                 "  \"positions\": [\n";

   int count = 0;
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   int gmt_offset = GetBrokerGmtOffset();

   for(int i = 0; i < total_pos; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;

      string sym = PositionGetString(POSITION_SYMBOL);
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double vol = PositionGetDouble(POSITION_VOLUME);
      double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
      datetime open_time = (datetime)PositionGetInteger(POSITION_TIME);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      double cur_price = PositionGetDouble(POSITION_PRICE_CURRENT);
      double profit = PositionGetDouble(POSITION_PROFIT);
      long magic = PositionGetInteger(POSITION_MAGIC);
      string comment = PositionGetString(POSITION_COMMENT);

      string type_str = (type == POSITION_TYPE_BUY ? "BUY" : "SELL");
      string open_utc = FormatUtcIso(open_time, gmt_offset);

      json += (count > 0 ? ",\n" : "") +
              "    {\n" +
              "      \"ticket\": " + IntegerToString(ticket) + ",\n" +
              "      \"symbol\": \"" + sym + "\",\n" +
              "      \"type\": \"" + type_str + "\",\n" +
              "      \"lots\": " + DoubleToString(vol, 2) + ",\n" +
              "      \"open_price\": " + DoubleToString(open_price, digits) + ",\n" +
              "      \"open_time_utc\": \"" + open_utc + "\",\n" +
              "      \"sl\": " + DoubleToString(sl, digits) + ",\n" +
              "      \"tp\": " + DoubleToString(tp, digits) + ",\n" +
              "      \"current_price\": " + DoubleToString(cur_price, digits) + ",\n" +
              "      \"profit\": " + DoubleToString(profit, 2) + ",\n" +
              "      \"magic_number\": " + IntegerToString(magic) + ",\n" +
              "      \"comment\": \"" + EscapeJsonString(comment) + "\"\n" +
              "    }";
      count++;
   }
   json += "\n  ]\n}";

   string resp;
   int code;
   if(!HttpSend("POST", "/api/v1/account/snapshot", json, resp, code))
   {
      if(code == 404)
      {
         Print("[Alped_Bridge] Source not registered (HTTP 404). Transitioning to STATE_UNINITIALIZED to re-handshake.");
         g_State = STATE_UNINITIALIZED;
      }
   }
}

//+------------------------------------------------------------------+
//| SEND LIVE MARKET SNAPSHOT (TICK & BAR 0 FORMING CANDLE)          |
//| STRICT MANDATE: Zero Supabase writes. In-memory live stream only.|
//+------------------------------------------------------------------+
void SendLiveMarketSnapshot()
{
   if(g_SourceId == "") return;

   MqlTick tick;
   if(SymbolInfoTick(_Symbol, tick))
   {
      g_LastBid = tick.bid;
      g_LastAsk = tick.ask;
      g_LastSpread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      g_LastTickTime = tick.time;
   }

   if(g_LastBid <= 0.0) return;

   int gmt_offset = GetBrokerGmtOffset();
   datetime tick_time = (g_LastTickTime > 0) ? g_LastTickTime : TimeCurrent();
   long tick_epoch = (long)(tick_time - gmt_offset);
   string tick_utc = FormatUtcIso(tick_time, gmt_offset);

   string json = "{";
   json += "\"source_id\":\"" + g_SourceId + "\",";
   json += "\"symbol\":\"" + EscapeJsonString(_Symbol) + "\",";
   json += "\"tick\":{";
   json += "\"bid\":" + DoubleToString(g_LastBid, (int)_Digits) + ",";
   json += "\"ask\":" + DoubleToString(g_LastAsk, (int)_Digits) + ",";
   json += "\"spread\":" + IntegerToString(g_LastSpread) + ",";
   json += "\"tick_time_utc\":\"" + tick_utc + "\",";
   json += "\"tick_time_epoch\":" + IntegerToString(tick_epoch);
   json += "},";

   json += "\"timeframes\":{";
   bool first_tf = true;
   for(int i = 0; i < g_SubscribedTfCount; i++)
   {
      ENUM_TIMEFRAMES tf = g_SubscribedTFs[i];
      string tf_name = g_TfNames[i];

      MqlRates current_rate[1];
      if(CopyRates(_Symbol, tf, 0, 1, current_rate) > 0)
      {
         if(!first_tf) json += ",";
         first_tf = false;

         datetime b_time = current_rate[0].time;
         long bar_epoch = (long)(b_time - gmt_offset);
         string bar_utc = FormatUtcIso(b_time, gmt_offset);

         json += "\"" + tf_name + "\":{";
         json += "\"timeframe\":\"" + tf_name + "\",";
         json += "\"time_epoch\":" + IntegerToString(bar_epoch) + ",";
         json += "\"time_utc\":\"" + bar_utc + "\",";
         json += "\"open\":" + DoubleToString(current_rate[0].open, (int)_Digits) + ",";
         json += "\"high\":" + DoubleToString(current_rate[0].high, (int)_Digits) + ",";
         json += "\"low\":" + DoubleToString(current_rate[0].low, (int)_Digits) + ",";
         json += "\"close\":" + DoubleToString(current_rate[0].close, (int)_Digits) + ",";
         json += "\"volume\":" + IntegerToString(current_rate[0].tick_volume);
         json += "}";
      }
   }
   json += "}}";

   string resp = "";
   int code = 0;
   HttpSend("POST", "/api/v1/market/live-bar", json, resp, code);
}

//+------------------------------------------------------------------+
//| EXPERT INITIALIZATION HANDLER                                    |
//| STRICT MANDATE: ZERO WebRequest() in OnInit()                    |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("[Alped_Bridge] Starting initialization (V1 Data Infrastructure)...");

   // Configure Subscribed Timeframes
   g_SubscribedTfCount = 0;
   if(InpEnableM1)  { g_SubscribedTFs[g_SubscribedTfCount] = PERIOD_M1;  g_TfNames[g_SubscribedTfCount++] = "M1"; }
   if(InpEnableM5)  { g_SubscribedTFs[g_SubscribedTfCount] = PERIOD_M5;  g_TfNames[g_SubscribedTfCount++] = "M5"; }
   if(InpEnableM15) { g_SubscribedTFs[g_SubscribedTfCount] = PERIOD_M15; g_TfNames[g_SubscribedTfCount++] = "M15"; }
   if(InpEnableM30) { g_SubscribedTFs[g_SubscribedTfCount] = PERIOD_M30; g_TfNames[g_SubscribedTfCount++] = "M30"; }
   if(InpEnableH1)  { g_SubscribedTFs[g_SubscribedTfCount] = PERIOD_H1;  g_TfNames[g_SubscribedTfCount++] = "H1"; }
   if(InpEnableH4)  { g_SubscribedTFs[g_SubscribedTfCount] = PERIOD_H4;  g_TfNames[g_SubscribedTfCount++] = "H4"; }
   if(InpEnableD1)  { g_SubscribedTFs[g_SubscribedTfCount] = PERIOD_D1;  g_TfNames[g_SubscribedTfCount++] = "D1"; }

   for(int i = 0; i < g_SubscribedTfCount; i++)
      g_LastBarTimes[i] = 0;

   // Initialize in-memory queues
   ArrayResize(g_CandleQueue, 0);
   ArrayResize(g_EventQueue, 0);
   ArrayResize(g_TickQueue, 0);
   g_CandleQueueCount = 0;
   g_EventQueueCount  = 0;
   g_TickQueueCount   = 0;

   g_State = STATE_UNINITIALIZED;
   g_NextAllowedNetOp = 0;
   g_BackoffSeconds = 1;

   // Start timer at 1 second
   EventSetTimer(1);

   Print("[Alped_Bridge] OnInit complete. Timer started. Ready for background network handshake in OnTimer().");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| EXPERT DEINITIALIZATION HANDLER                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   PrintFormat("[Alped_Bridge] Stopped. Reason code: %d", reason);
}

//+------------------------------------------------------------------+
//| EXPERT TICK HANDLER                                              |
//| STRICT MANDATE: ZERO WebRequest(). Fast detection & enqueue only.|
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. Detect New Closed Bar on subscribed timeframes
   for(int i = 0; i < g_SubscribedTfCount; i++)
   {
      datetime current_bar_time = iTime(_Symbol, g_SubscribedTFs[i], 0);
      if(current_bar_time == 0) continue;

      if(g_LastBarTimes[i] == 0)
      {
         g_LastBarTimes[i] = current_bar_time;
         continue;
      }

      // If new bar opened, bar[1] has just closed
      if(current_bar_time > g_LastBarTimes[i])
      {
         g_LastBarTimes[i] = current_bar_time;

         MqlRates closed_rate[1];
         if(CopyRates(_Symbol, g_SubscribedTFs[i], 1, 1, closed_rate) > 0)
         {
            int gmt_offset = GetBrokerGmtOffset();
            datetime b_time = closed_rate[0].time;

            CandleRecord rec;
            rec.epoch = (long)(b_time - gmt_offset);
            rec.utc_time = FormatUtcIso(b_time, gmt_offset);
            rec.broker_time = FormatLocalBrokerTime(b_time);
            rec.gmt_offset = gmt_offset;
            rec.open = closed_rate[0].open;
            rec.high = closed_rate[0].high;
            rec.low = closed_rate[0].low;
            rec.close = closed_rate[0].close;
            rec.volume = closed_rate[0].tick_volume;
            rec.spread = closed_rate[0].spread;
            rec.timeframe = g_TfNames[i];

            EnqueueCandle(rec);
         }
      }
   }

   // 2. Sample Ticks if mode is enabled
   if(InpTickMode != TICK_MODE_OFF)
   {
      MqlTick last_tick;
      if(SymbolInfoTick(_Symbol, last_tick))
      {
         int gmt_offset = GetBrokerGmtOffset();
         TickRecord tk;
         tk.epoch = (long)(last_tick.time - gmt_offset);
         tk.utc_time = FormatUtcIso(last_tick.time, gmt_offset);
         tk.broker_time = FormatLocalBrokerTime(last_tick.time);
         tk.bid = last_tick.bid;
         tk.ask = last_tick.ask;
         tk.last = last_tick.last;
         tk.volume = (long)last_tick.volume;
         EnqueueTick(tk);
      }
   }

   // 3. Track latest tick metrics for Live Market Bar 0 broadcasting
   MqlTick live_tick;
   if(SymbolInfoTick(_Symbol, live_tick))
   {
      g_LastBid = live_tick.bid;
      g_LastAsk = live_tick.ask;
      g_LastSpread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      g_LastTickTime = live_tick.time;
      g_LiveMarketDirty = true;
   }
}

//+------------------------------------------------------------------+
//| TRADE TRANSACTION HANDLER                                        |
//| STRICT MANDATE: ZERO WebRequest(). Enqueue event and return.     |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.symbol != _Symbol && trans.symbol != "") return;

   string event_type = "";
   double lots = trans.volume;
   double price = trans.price;
   long ticket = (long)trans.position;

   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      long entry_type = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
      if(entry_type == DEAL_ENTRY_IN)
         event_type = "OPEN";
      else if(entry_type == DEAL_ENTRY_OUT)
         event_type = "CLOSE";
      else if(entry_type == DEAL_ENTRY_OUT_BY)
         event_type = "PARTIAL_CLOSE";
   }
   else if(trans.type == TRADE_TRANSACTION_ORDER_UPDATE)
   {
      if(trans.order_state == ORDER_STATE_STARTED)
      {
         event_type = "MODIFY_SL";
      }
   }

   if(event_type != "")
   {
      datetime now_broker = TimeCurrent();
      int gmt_offset = GetBrokerGmtOffset();

      PositionEventRecord ev;
      ev.ticket = ticket;
      ev.event_type = event_type;
      ev.symbol = trans.symbol != "" ? trans.symbol : _Symbol;
      ev.lots = lots;
      ev.price = price;
      ev.sl = trans.price_sl;
      ev.tp = trans.price_tp;
      ev.profit = 0.0;
      ev.epoch = (long)(now_broker - gmt_offset);
      ev.utc_time = FormatUtcIso(now_broker, gmt_offset);

      EnqueuePositionEvent(ev);
   }
}

//+------------------------------------------------------------------+
//| TIMER HANDLER                                                    |
//| STRICT MANDATE: THE SOLE PLACE ALLOWED TO CALL WebRequest()      |
//+------------------------------------------------------------------+
void OnTimer()
{
   datetime now = TimeCurrent();
   if(now < g_NextAllowedNetOp) return;

   switch(g_State)
   {
      case STATE_UNINITIALIZED:
      {
         Print("[Alped_Bridge] Attempting Handshake with FastAPI backend...");
         if(PerformHandshake())
         {
            g_State = STATE_SYNCING_SYMBOLS;
            g_BackoffSeconds = 1;
         }
         else
         {
            g_BackoffSeconds = MathMin(g_BackoffSeconds * 2, 60);
            g_NextAllowedNetOp = now + g_BackoffSeconds;
            PrintFormat("[Alped_Bridge] Handshake failed. Retrying in %d seconds...", g_BackoffSeconds);
         }
         break;
      }

      case STATE_SYNCING_SYMBOLS:
      {
         Print("[Alped_Bridge] Syncing symbol metadata...");
         if(SyncSymbolMetadata())
         {
            g_State = STATE_HISTORICAL_SYNC;
            g_CurrentSyncTfIdx = 0;
            g_BackoffSeconds = 1;
         }
         else
         {
            g_BackoffSeconds = MathMin(g_BackoffSeconds * 2, 60);
            g_NextAllowedNetOp = now + g_BackoffSeconds;
         }
         break;
      }

      case STATE_HISTORICAL_SYNC:
      {
         if(g_CurrentSyncTfIdx < g_SubscribedTfCount)
         {
            ENUM_TIMEFRAMES tf = g_SubscribedTFs[g_CurrentSyncTfIdx];
            string tf_name = g_TfNames[g_CurrentSyncTfIdx];
            PrintFormat("[Alped_Bridge] Checking sync status for %s...", tf_name);
            if(PerformTfHistoricalSync(tf, tf_name))
            {
               g_CurrentSyncTfIdx++;
            }
         }
         else
         {
            Print("[Alped_Bridge] All historical timeframes synced! Transitioning to STATE_LIVE_RUNNING.");
            g_State = STATE_LIVE_RUNNING;
            g_LastHeartbeatTime = now;
         }
         break;
      }

      case STATE_LIVE_RUNNING:
      {
         // 1. Flush Trade Position Events Queue
         FlushEventQueue();

         // 2. Flush Closed Candle Queue
         FlushCandleQueue();

         // 3. Periodic Account & Positions Heartbeat (every 60s)
         if(now - g_LastHeartbeatTime >= 60)
         {
            SendAccountHeartbeat();
            g_LastHeartbeatTime = now;
         }

         // 4. Send Live Bar 0 & Tick Snapshot (Zero Supabase writes)
         SendLiveMarketSnapshot();
         break;
      }
   }
}
//+------------------------------------------------------------------+
