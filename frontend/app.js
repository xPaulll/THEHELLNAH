/**
 * ALPED PUNYA V3 — LIVE MARKET CONTROL CENTER
 * Professional Reactive Market Terminal & Telemetry Engine
 * 
 * Strict Architecture Invariants:
 * 1. FAST ACQUISITION: MT5 ticks & Bar 0 forming candles streamed in real-time.
 * 2. CONTROLLED BROADCAST: Server throttles to ~4 updates/sec max.
 * 3. IN-MEMORY LIVE STATE: Zero Supabase writes for Bar 0 / ticks.
 * 4. CANONICAL CLOSED DATA: Supabase holds strictly Bar 1 closed candles.
 * 5. INCREMENTAL RENDERING: Historical candles loaded once; Bar 0 updated via candleSeries.update().
 */

(function () {
  'use strict';

  // Configuration & Constants
  const API_BASE = '/api/v1/dashboard';
  const MAX_EVENT_BUFFER = 500;
  const POLL_INTERVAL_MS = 2500;

  // Timeframe duration in seconds for countdown calculations
  const TF_SECONDS = {
    'M1': 60,
    'M5': 300,
    'M15': 900,
    'M30': 1800,
    'H1': 3600,
    'H4': 14400,
    'D1': 86400
  };

  // Application State
  const state = {
    currentView: 'overview',
    activeChartTf: 'M1',
    activeInspectorTf: 'M1',
    logServiceFilter: 'ALL',
    logLevelFilter: 'ALL',
    streamServiceFilter: 'ALL',
    streamLevelFilter: 'ALL',
    streamPaused: false,
    
    // Live Market Layer
    latestLiveSnapshot: null,
    activeBar0: null,
    livenessStatus: 'OFFLINE',
    livenessAge: 0,
    hasEverReceivedTick: false,
    
    // WebSocket & Latency
    ws: null,
    wsConnected: false,
    wsLatency: null,
    wsPingTimer: null,
    wsReconnectTimer: null,
    
    // System Event Stream Buffer (Bounded at MAX_EVENT_BUFFER)
    systemEventsBuffer: [],
    
    // Lightweight Charts Instances
    chart: null,
    candleSeries: null,
    historicalBarsLoaded: false,
    currentHistoricalTf: null,

    // Polling
    lastSummary: null,
    pollTimer: null,
    countdownTimer: null,
    isFetchingSummary: false
  };

  // Status badge styling helper
  function getStatusBadge(status) {
    const s = (status || 'UNKNOWN').toUpperCase();
    let cls = 'status-unknown';
    let icon = '⚪';

    if (s === 'ONLINE' || s === 'SYNC' || s === 'HEALTHY' || s === 'LIVE') {
      cls = 'status-online';
      icon = '🟢';
    } else if (s === 'DELAYED' || s === 'WARNING' || s === 'STALE') {
      cls = 'status-delayed';
      icon = '🟡';
    } else if (s === 'OFFLINE' || s === 'ERROR') {
      cls = 'status-offline';
      icon = '🔴';
    }

    return `<span class="status-pill ${cls}">${icon} ${s}</span>`;
  }

  // Format currency
  function formatCurrency(val) {
    if (val === null || val === undefined || isNaN(val)) return 'N/A';
    return '$' + Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Format price
  function formatPrice(val, digits = 2) {
    if (val === null || val === undefined || isNaN(val)) return '—';
    return Number(val).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  // Real-time Clock Updater (Local WIB UTC+7 + UTC)
  function updateClocks() {
    const now = new Date();

    // UTC
    const utcHours = String(now.getUTCHours()).padStart(2, '0');
    const utcMinutes = String(now.getUTCMinutes()).padStart(2, '0');
    const utcSeconds = String(now.getUTCSeconds()).padStart(2, '0');
    const elUtc = document.getElementById('clock-utc');
    if (elUtc) elUtc.textContent = `${utcHours}:${utcMinutes}:${utcSeconds}`;

    // WIB (UTC+7)
    const wibDate = new Date(now.getTime() + (7 * 60 * 60 * 1000));
    const wibHours = String(wibDate.getUTCHours()).padStart(2, '0');
    const wibMinutes = String(wibDate.getUTCMinutes()).padStart(2, '0');
    const wibSeconds = String(wibDate.getUTCSeconds()).padStart(2, '0');
    const elWib = document.getElementById('clock-wib');
    if (elWib) elWib.textContent = `${wibHours}:${wibMinutes}:${wibSeconds}`;
  }

  // =========================================================================
  // TRADINGVIEW LIGHTWEIGHT CHARTS INITIALIZATION & CONTROLLER
  // =========================================================================

  function initLightweightChart() {
    const container = document.getElementById('tv-chart-container');
    if (!container) return;

    // Verify LightweightCharts library loaded
    if (typeof LightweightCharts === 'undefined') {
      console.error('[Chart] LightweightCharts library not found.');
      container.innerHTML = '<div style="color:#ef5350; padding:2rem; text-align:center;">LightweightCharts library failed to load.</div>';
      return;
    }

    container.innerHTML = '';

    const chartOptions = {
      width: container.clientWidth,
      height: container.clientHeight || 420,
      layout: {
        background: { type: 'solid', color: '#0B0F17' },
        textColor: '#94A3B8',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace"
      },
      grid: {
        vertLines: { color: '#161F30' },
        horzLines: { color: '#161F30' }
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: {
          color: '#06B6D4',
          width: 1,
          style: LightweightCharts.LineStyle.Dashed
        },
        horzLine: {
          color: '#06B6D4',
          width: 1,
          style: LightweightCharts.LineStyle.Dashed
        }
      },
      rightPriceScale: {
        borderColor: '#1E293B',
        scaleMargins: { top: 0.1, bottom: 0.15 }
      },
      timeScale: {
        borderColor: '#1E293B',
        timeVisible: true,
        secondsVisible: false
      }
    };

    state.chart = LightweightCharts.createChart(container, chartOptions);

    state.candleSeries = state.chart.addCandlestickSeries({
      upColor: '#10B981',
      downColor: '#EF4444',
      borderUpColor: '#10B981',
      borderDownColor: '#EF4444',
      wickUpColor: '#10B981',
      wickDownColor: '#EF4444'
    });

    // Handle responsive resize
    window.addEventListener('resize', () => {
      if (state.chart && container) {
        state.chart.applyOptions({ width: container.clientWidth });
      }
    });

    // Load initial historical candles for default M1 timeframe
    loadChartCandles(state.activeChartTf);
  }

  // Load historical closed bars once from Supabase backend
  async function loadChartCandles(tf) {
    if (!state.candleSeries) return;

    state.currentHistoricalTf = tf;
    const statusText = document.getElementById('chart-tf-status-text');
    if (statusText) statusText.textContent = `${tf} · Loading historical bars...`;

    try {
      const resp = await fetch(`${API_BASE}/candles?symbol=XAUUSD.vx&timeframe=${tf}&limit=120`, { cache: 'no-store' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      if (data.candles && data.candles.length > 0) {
        // MANDATORY DIAGNOSTIC LOGGING
        console.log(`[Chart] Historical candles received for ${tf}:`, data.candles.length);
        console.log('[Chart] First raw candle:', data.candles[0]);
        console.log('[Chart] Last raw candle:', data.candles[data.candles.length - 1]);

        // Transform, validate, and convert timestamps strictly to Unix seconds
        const validBars = [];
        const seenTimes = new Set();

        data.candles.forEach(c => {
          let t = c.epoch !== undefined && c.epoch !== null ? Number(c.epoch) : null;
          if (!t && c.candle_time_utc) {
            t = Math.floor(new Date(c.candle_time_utc).getTime() / 1000);
          }

          // Strict validation: must be a positive number
          if (!t || isNaN(t) || t <= 0) {
            console.warn('[Chart] Skipping candle with invalid timestamp:', c);
            return;
          }

          const o = Number(c.open);
          const h = Number(c.high);
          const l = Number(c.low);
          const cl = Number(c.close);

          if (isNaN(o) || isNaN(h) || isNaN(l) || isNaN(cl)) return;

          validBars.push({ time: t, open: o, high: h, low: l, close: cl });
        });

        // Sort ascending strictly by time
        validBars.sort((a, b) => a.time - b.time);

        // Deduplicate any identical timestamps (Lightweight Charts requirement)
        const dedupedBars = [];
        for (const bar of validBars) {
          if (!seenTimes.has(bar.time)) {
            seenTimes.add(bar.time);
            dedupedBars.push(bar);
          }
        }

        console.log(`[Chart] Clean deduped bars sent to chart (${tf}):`, dedupedBars.length);
        state.candleSeries.setData(dedupedBars);
        state.historicalBarsLoaded = true;

        // Auto-fit content ONCE on load / timeframe switch (NEVER on every tick)
        state.chart.timeScale().fitContent();

        // If Bar 0 exists for this timeframe in memory, incrementally update it
        if (state.latestLiveSnapshot && state.latestLiveSnapshot.timeframes && state.latestLiveSnapshot.timeframes[tf]) {
          const b0 = state.latestLiveSnapshot.timeframes[tf];
          updateChartBar0(tf, b0);
        }

        updateStreamStatusIndicator();
      } else {
        // No historical bars found in DB
        state.candleSeries.setData([]);
        state.historicalBarsLoaded = true;
        updateStreamStatusIndicator();
      }
    } catch (err) {
      console.warn('[Chart] Error loading historical candles:', err);
      if (statusText) statusText.textContent = `${tf} · Error loading history`;
    }
  }

  // Update Bar 0 on Chart (Incremental series.update without full chart redraw)
  function updateChartBar0(tf, bar0) {
    if (!state.candleSeries || !state.historicalBarsLoaded) return;
    if (state.activeChartTf !== tf) return;

    let t = Number(bar0.time_epoch);
    if (!t || isNaN(t) || t <= 0) {
      if (bar0.time_utc) t = Math.floor(new Date(bar0.time_utc).getTime() / 1000);
    }

    if (!t || isNaN(t) || t <= 0) return;

    state.candleSeries.update({
      time: t,
      open: Number(bar0.open),
      high: Number(bar0.high),
      low: Number(bar0.low),
      close: Number(bar0.close)
    });
  }

  // =========================================================================
  // REAL-TIME WEBSOCKET SUBSCRIPTION LAYER (/ws/live)
  // =========================================================================

  function connectWebSocket() {
    if (state.ws) {
      try { state.ws.close(); } catch (e) {}
      state.ws = null;
    }

    const wsTag = document.getElementById('header-ws-tag');
    if (wsTag) {
      wsTag.textContent = 'WS: CONNECTING';
      wsTag.style.color = 'var(--yellow-main)';
      wsTag.style.borderColor = 'rgba(245, 158, 11, 0.4)';
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;

    console.log('[WebSocket] Connecting to:', wsUrl);

    try {
      state.ws = new WebSocket(wsUrl);
    } catch (err) {
      console.error('[WebSocket] Instantiation failed:', err);
      scheduleWsReconnect();
      return;
    }

    state.ws.onopen = () => {
      console.log('[WebSocket] Connected successfully.');
      state.wsConnected = true;
      if (wsTag) {
        wsTag.textContent = 'WS: CONNECTED';
        wsTag.style.color = 'var(--green-main)';
        wsTag.style.borderColor = 'rgba(16, 185, 129, 0.4)';
      }

      startWsPing();
      updateStreamStatusIndicator();
    };

    state.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleWsMessage(msg);
      } catch (err) {
        console.warn('[WebSocket] Invalid JSON received:', event.data);
      }
    };

    state.ws.onerror = (err) => {
      console.warn('[WebSocket] Error occurred:', err);
    };

    state.ws.onclose = () => {
      console.warn('[WebSocket] Connection closed.');
      state.wsConnected = false;
      state.wsLatency = null;
      clearInterval(state.wsPingTimer);

      if (wsTag) {
        wsTag.textContent = 'WS: RECONNECTING';
        wsTag.style.color = 'var(--red-main)';
        wsTag.style.borderColor = 'rgba(239, 68, 68, 0.4)';
      }

      const rttEl = document.getElementById('sys-ws-rtt');
      if (rttEl) rttEl.textContent = 'N/A';

      updateStreamStatusIndicator();
      scheduleWsReconnect();
    };
  }

  function scheduleWsReconnect() {
    clearTimeout(state.wsReconnectTimer);
    state.wsReconnectTimer = setTimeout(() => {
      connectWebSocket();
    }, 2000);
  }

  function startWsPing() {
    clearInterval(state.wsPingTimer);
    state.wsPingTimer = setInterval(() => {
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({
          type: 'PING',
          client_time: performance.now()
        }));
      }
    }, 3000);
  }

  // Handle Inbound WebSocket Messages
  function handleWsMessage(msg) {
    const type = (msg.type || '').toUpperCase();

    if (type === 'PONG') {
      if (msg.client_time !== undefined) {
        const rtt = Math.round(performance.now() - msg.client_time);
        state.wsLatency = rtt;
        const rttEl = document.getElementById('sys-ws-rtt');
        if (rttEl) rttEl.textContent = `${rtt} ms`;
      }
      return;
    }

    if (type === 'INIT' || type === 'LIVE_MARKET') {
      handleLiveMarketPayload(msg.data);
      return;
    }

    if (type === 'CANDLE_CLOSED') {
      handleCandleClosedEvent(msg.data);
      return;
    }

    if (type === 'SYSTEM_EVENT') {
      handleSystemEvent(msg.data);
      return;
    }
  }

  // Handle LIVE_MARKET Payload
  function handleLiveMarketPayload(data) {
    if (!data) return;
    state.latestLiveSnapshot = data;

    const tick = data.tick;
    const liveness = data.liveness || {};
    const tfs = data.timeframes || {};

    if (tick) state.hasEverReceivedTick = true;

    state.livenessStatus = liveness.status || 'OFFLINE';
    state.livenessAge = liveness.age_seconds || 0;

    // 1. Update Market Liveness Badge
    const badge = document.getElementById('market-liveness-badge');
    const priceLabel = document.getElementById('live-price-label');

    if (badge) {
      if (!state.hasEverReceivedTick && state.livenessAge === 0) {
        badge.className = 'badge-pill';
        badge.style.background = 'rgba(148, 163, 184, 0.15)';
        badge.style.color = '#94A3B8';
        badge.style.border = '1px solid rgba(148, 163, 184, 0.3)';
        badge.innerHTML = '⚪ WAITING FOR DATA';
      } else if (state.livenessStatus === 'LIVE') {
        badge.className = 'badge-pill badge-live';
        badge.innerHTML = '● LIVE';
      } else if (state.livenessStatus === 'STALE') {
        badge.className = 'badge-pill badge-stale';
        badge.innerHTML = `⚠️ STALE (${state.livenessAge}s)`;
      } else {
        badge.className = 'badge-pill badge-offline';
        badge.innerHTML = `🔴 OFFLINE (${state.livenessAge}s)`;
      }
    }

    // 2. Update Price & Label (Distinguish LIVE PRICE vs LAST KNOWN PRICE)
    const priceVal = document.getElementById('live-market-price');
    if (priceVal && tick) {
      priceVal.textContent = formatPrice(tick.bid, 2);
    }

    if (priceLabel) {
      if (!state.hasEverReceivedTick) {
        priceLabel.textContent = 'NO TICKS YET';
        priceLabel.style.color = 'var(--text-muted)';
      } else if (state.livenessStatus === 'LIVE') {
        priceLabel.textContent = 'LIVE BID';
        priceLabel.style.color = 'var(--green-main)';
      } else if (state.livenessStatus === 'STALE') {
        priceLabel.textContent = 'LAST KNOWN PRICE';
        priceLabel.style.color = 'var(--yellow-main)';
      } else {
        priceLabel.textContent = 'LAST KNOWN PRICE';
        priceLabel.style.color = 'var(--red-main)';
      }
    }

    // 3. Update Tick Metrics (Bid, Ask, Spread)
    if (tick) {
      const elBid = document.getElementById('live-bid');
      if (elBid) elBid.textContent = formatPrice(tick.bid, 2);
      const elAsk = document.getElementById('live-ask');
      if (elAsk) elAsk.textContent = formatPrice(tick.ask, 2);
      const elSpread = document.getElementById('live-spread');
      if (elSpread) elSpread.textContent = tick.spread !== undefined ? tick.spread : '—';
    }

    // 4. Update Active Timeframe Bar 0 Detail Strip & Chart
    const activeTf = state.activeChartTf;
    const b0 = tfs[activeTf];
    if (b0) {
      state.activeBar0 = b0;
      renderBar0Details(activeTf, b0);
      updateChartBar0(activeTf, b0);
    }

    // 5. Update Multi-Timeframe Status Chips
    updateTimeframeChips(tfs);

    // 6. Update Chart Stream Indicator Meta
    updateStreamStatusIndicator();

    // 7. Update System Telemetry MT5 Heartbeat Age
    const mt5HbAge = document.getElementById('sys-mt5-hb');
    if (mt5HbAge) {
      mt5HbAge.textContent = `${state.livenessAge}s`;
      mt5HbAge.className = 'stat-num mono ' + (state.livenessStatus === 'LIVE' ? 'text-good' : (state.livenessStatus === 'STALE' ? 'text-accent' : 'text-danger'));
    }
  }

  // Update Stream Status Indicator Meta on Chart Toolbar
  function updateStreamStatusIndicator() {
    const statusText = document.getElementById('chart-tf-status-text');
    const dot = document.querySelector('.chart-status-meta .meta-dot');
    if (!statusText) return;

    const tf = state.activeChartTf;

    if (!state.wsConnected) {
      statusText.textContent = `${tf} · WebSocket Disconnected`;
      if (dot) dot.style.background = '#EF4444';
      return;
    }

    if (!state.hasEverReceivedTick && state.livenessAge === 0) {
      statusText.textContent = `${tf} · Waiting for Live MT5 Data`;
      if (dot) dot.style.background = '#94A3B8';
      return;
    }

    if (state.livenessStatus === 'LIVE') {
      statusText.textContent = `${tf} · Realtime Stream Active`;
      if (dot) dot.style.background = '#10B981';
    } else if (state.livenessStatus === 'STALE') {
      statusText.textContent = `${tf} · Stale Stream (${state.livenessAge}s ago)`;
      if (dot) dot.style.background = '#F59E0B';
    } else {
      statusText.textContent = `${tf} · MT5 Stream Offline (${state.livenessAge}s ago)`;
      if (dot) dot.style.background = '#EF4444';
    }
  }

  // Render Bar 0 Forming Details Strip & Countdown
  function renderBar0Details(tf, b0) {
    const elTfName = document.getElementById('bar0-tf-name');
    if (elTfName) elTfName.textContent = tf;

    const elOpen = document.getElementById('bar0-open');
    if (elOpen) elOpen.textContent = formatPrice(b0.open, 2);

    const elHigh = document.getElementById('bar0-high');
    if (elHigh) elHigh.textContent = formatPrice(b0.high, 2);

    const elLow = document.getElementById('bar0-low');
    if (elLow) elLow.textContent = formatPrice(b0.low, 2);

    const elClose = document.getElementById('bar0-close');
    if (elClose) elClose.textContent = formatPrice(b0.close, 2);

    const elVol = document.getElementById('bar0-vol');
    if (elVol) elVol.textContent = b0.volume !== undefined ? Number(b0.volume).toLocaleString() : '—';

    updateBar0CountdownAndAge();
  }

  // Calculate Bar 0 Age and Candle Close Countdown
  function updateBar0CountdownAndAge() {
    const b0 = state.activeBar0;
    const tf = state.activeChartTf;
    if (!b0 || !b0.time_epoch) return;

    const nowEpoch = Math.floor(Date.now() / 1000);
    const duration = TF_SECONDS[tf] || 60;

    // 1. Age (elapsed since bar open)
    const elapsed = Math.max(0, nowEpoch - b0.time_epoch);
    const ageM = Math.floor(elapsed / 60);
    const ageS = elapsed % 60;
    const elAge = document.getElementById('bar0-age');
    if (elAge) elAge.textContent = `${String(ageM).padStart(2, '0')}:${String(ageS).padStart(2, '0')}`;

    // 2. Countdown to Candle Close
    const closeEpoch = b0.time_epoch + duration;
    const remaining = Math.max(0, closeEpoch - nowEpoch);
    const elCountdown = document.getElementById('bar0-countdown');

    if (elCountdown) {
      if (remaining === 0) {
        elCountdown.textContent = '00:00 (CLOSING)';
        elCountdown.className = 'b0-val mono text-accent font-bold';
      } else {
        const remH = Math.floor(remaining / 3600);
        const remM = Math.floor((remaining % 3600) / 60);
        const remS = remaining % 60;

        if (remH > 0) {
          elCountdown.textContent = `${String(remH).padStart(2, '0')}:${String(remM).padStart(2, '0')}:${String(remS).padStart(2, '0')}`;
        } else {
          elCountdown.textContent = `${String(remM).padStart(2, '0')}:${String(remS).padStart(2, '0')}`;
        }

        elCountdown.className = remaining <= 10 ? 'b0-val mono text-warn font-bold' : 'b0-val mono text-good font-bold';
      }
    }
  }

  // Update Multi-Timeframe Status Chips (M1..D1)
  function updateTimeframeChips(timeframes) {
    const list = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
    list.forEach(tf => {
      const chip = document.getElementById(`chip-${tf}`);
      if (!chip) return;

      const bar = timeframes ? timeframes[tf] : null;
      const statusSpan = chip.querySelector('.tf-chip-status');

      if (bar) {
        if (state.livenessStatus === 'LIVE') {
          statusSpan.textContent = '● FORMING';
          statusSpan.style.color = '#10B981';
        } else if (state.livenessStatus === 'STALE') {
          statusSpan.textContent = '⚠️ STALE';
          statusSpan.style.color = '#F59E0B';
        } else {
          statusSpan.textContent = '🔴 OFFLINE';
          statusSpan.style.color = '#EF4444';
        }
      } else {
        statusSpan.textContent = '○ WAITING';
        statusSpan.style.color = '#94A3B8';
      }
    });
  }

  // Handle CANDLE_CLOSED Event
  function handleCandleClosedEvent(candleData) {
    if (!candleData) return;

    // 1. If closed candle matches active chart timeframe, finalize it on chart
    if (state.candleSeries && candleData.timeframe === state.activeChartTf) {
      let t = Number(candleData.candle ? candleData.candle.candle_time_epoch : candleData.epoch);
      if (!t && candleData.candle && candleData.candle.candle_time_utc) {
        t = Math.floor(new Date(candleData.candle.candle_time_utc).getTime() / 1000);
      }

      if (t && !isNaN(t) && t > 0) {
        const c = candleData.candle || candleData;
        state.candleSeries.update({
          time: t,
          open: Number(c.open),
          high: Number(c.high),
          low: Number(c.low),
          close: Number(c.close)
        });
      }
    }

    // 2. Log system event
    const cObj = candleData.candle || candleData;
    handleSystemEvent({
      timestamp: cObj.candle_time_utc || new Date().toISOString(),
      timestamp_display: new Date().toLocaleTimeString('id-ID'),
      service: 'MT5_CANDLE',
      level: 'INFO',
      message: `[CANDLE_CLOSED] ${candleData.symbol} ${candleData.timeframe} closed at ${cObj.close} (Vol: ${cObj.volume || cObj.tick_volume || 'N/A'})`
    });

    // 3. If Market Data tab is currently active and inspecting this timeframe, refresh table
    if (state.currentView === 'market-data' && state.activeInspectorTf === candleData.timeframe) {
      fetchHistoricalCandles(state.activeInspectorTf);
    }
  }

  // Handle System Event Stream Message (Bounded at MAX_EVENT_BUFFER = 500)
  function handleSystemEvent(ev) {
    if (!ev) return;

    state.systemEventsBuffer.unshift(ev);
    if (state.systemEventsBuffer.length > MAX_EVENT_BUFFER) {
      state.systemEventsBuffer.pop();
    }

    const counter = document.getElementById('event-buffer-counter');
    if (counter) counter.textContent = `${state.systemEventsBuffer.length} / ${MAX_EVENT_BUFFER}`;

    if (!state.streamPaused) {
      renderSystemEventsStream();
    }
  }

  // Render System Event Stream (Filtered & Bounded)
  function renderSystemEventsStream() {
    const streamContainer = document.getElementById('system-events-stream');
    if (!streamContainer) return;

    const svcFlt = state.streamServiceFilter;
    const lvlFlt = state.streamLevelFilter;

    const filtered = state.systemEventsBuffer.filter(ev => {
      if (svcFlt !== 'ALL' && (ev.service || '').toUpperCase() !== svcFlt) return false;
      if (lvlFlt !== 'ALL' && (ev.level || '').toUpperCase() !== lvlFlt) return false;
      return true;
    });

    if (filtered.length === 0) {
      streamContainer.innerHTML = `<div class="event-item"><span class="event-msg">No events matching filters.</span></div>`;
      return;
    }

    let html = '';
    const displaySlice = filtered.slice(0, 100);
    displaySlice.forEach(ev => {
      const lvlCls = `event-level-${(ev.level || 'info').toLowerCase()}`;
      html += `<div class="event-item">
        <span class="event-time mono">${ev.timestamp_display || new Date(ev.timestamp).toLocaleTimeString('id-ID')}</span>
        <span class="event-level ${lvlCls}">${ev.level || 'INFO'}</span>
        <span class="event-service">${ev.service || 'SYS'}</span>
        <span class="event-msg">${ev.message || ''}</span>
      </div>`;
    });

    streamContainer.innerHTML = html;
  }

  // =========================================================================
  // REST SUMMARY & TELEMETRY ENGINE
  // =========================================================================

  async function fetchSummary() {
    if (state.isFetchingSummary) return;
    state.isFetchingSummary = true;

    try {
      const resp = await fetch(`${API_BASE}/summary`, { cache: 'no-store' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      state.lastSummary = data;
      renderSummary(data);
    } catch (err) {
      console.warn('[Dashboard] Summary fetch error:', err);
      renderFetchError(err);
    } finally {
      state.isFetchingSummary = false;
    }
  }

  function renderFetchError(err) {
    const pill = document.getElementById('pipeline-status-pill');
    if (pill) {
      pill.innerHTML = `<span class="pill-dot" style="background: var(--red-main); box-shadow: 0 0 8px var(--red-main);"></span>
                        <span class="pill-text text-danger">BACKEND OFFLINE</span>`;
    }
    const mt5Pill = document.getElementById('mt5-pill');
    if (mt5Pill) mt5Pill.outerHTML = getStatusBadge('OFFLINE');
    const faPill = document.getElementById('fastapi-pill');
    if (faPill) faPill.outerHTML = getStatusBadge('OFFLINE');
    const supPill = document.getElementById('supabase-pill');
    if (supPill) supPill.outerHTML = getStatusBadge('OFFLINE');
  }

  function renderSummary(data) {
    if (!data) return;

    const conns = data.connections || {};
    const mt5 = conns.mt5 || {};
    const fa = conns.fastapi || {};
    const sup = conns.supabase || {};
    const pipe = conns.pipeline || {};
    const dq = data.data_quality || {};
    const acc = data.account || {};

    // 1. Pipeline Status Pill
    const pill = document.getElementById('pipeline-status-pill');
    if (pill) {
      const pStatus = (pipe.status || 'UNKNOWN').toUpperCase();
      let dotCls = 'pulse-green';
      let textCls = '';
      if (pStatus === 'WARNING') { dotCls = ''; textCls = 'text-warn'; }
      if (pStatus === 'ERROR') { dotCls = ''; textCls = 'text-danger'; }

      pill.innerHTML = `<span class="pill-dot ${dotCls}" style="background: ${pStatus === 'HEALTHY' ? 'var(--green-main)' : (pStatus === 'WARNING' ? 'var(--yellow-main)' : 'var(--red-main)')}"></span>
                        <span class="pill-text ${textCls}">${pStatus}</span>`;
    }

    // Header session
    const mkt = data.market || {};
    const primarySym = mkt.symbols && mkt.symbols[0] ? mkt.symbols[0] : null;
    const elSession = document.getElementById('header-session-tag');
    if (elSession && primarySym && primarySym.session) {
      elSession.textContent = `SESSION: ${primarySym.session.replace(/,/g, ' | ')}`;
    }

    // 2. System Status Cards
    const elMt5Pill = document.getElementById('mt5-pill');
    if (elMt5Pill) elMt5Pill.outerHTML = getStatusBadge(mt5.status);
    const elMt5Hb = document.getElementById('mt5-heartbeat');
    if (elMt5Hb) elMt5Hb.textContent = mt5.age_display ? `${mt5.age_display} ago` : 'N/A';
    const elMt5Broker = document.getElementById('mt5-broker');
    if (elMt5Broker) elMt5Broker.textContent = `${mt5.broker || 'N/A'} [${mt5.environment || 'DEMO'}]`;
    const elMt5Ea = document.getElementById('mt5-ea-id');
    if (elMt5Ea) elMt5Ea.textContent = `${mt5.ea_identifier || 'N/A'} (v${mt5.ea_version || '3.0.0'})`;

    const elFaPill = document.getElementById('fastapi-pill');
    if (elFaPill) elFaPill.outerHTML = getStatusBadge(fa.status);
    const elFaUptime = document.getElementById('fastapi-uptime');
    if (elFaUptime) elFaUptime.textContent = fa.uptime_display || 'N/A';

    const elSupPill = document.getElementById('supabase-pill');
    if (elSupPill) elSupPill.outerHTML = getStatusBadge(sup.status);
    const elSupLat = document.getElementById('supabase-latency');
    if (elSupLat) elSupLat.textContent = sup.latency_ms !== null ? `${sup.latency_ms} ms` : 'N/A';

    const elPipePill = document.getElementById('pipeline-pill');
    if (elPipePill) elPipePill.outerHTML = getStatusBadge(pipe.status);
    const elPipeTotal = document.getElementById('pipeline-total-candles');
    if (elPipeTotal) elPipeTotal.textContent = `${(dq.total_candles || 0).toLocaleString()} bars`;

    // 3. Data Quality Stats
    const elDqMissing = document.getElementById('dq-missing');
    if (elDqMissing) elDqMissing.textContent = dq.missing_candles || 0;
    const elDqRec = document.getElementById('dq-recovered');
    if (elDqRec) elDqRec.textContent = dq.recovered_candles || 0;
    const elDqScore = document.getElementById('dq-score');
    if (elDqScore) elDqScore.textContent = `${dq.health_score_pct || 100}%`;
    const elDqVal = document.getElementById('dq-last-validation');
    if (elDqVal) {
      elDqVal.textContent = dq.last_validation_time ? new Date(dq.last_validation_time).toLocaleTimeString('id-ID') : 'Continuous';
    }
    const elDqRej = document.getElementById('dq-rejected');
    if (elDqRej) elDqRej.textContent = dq.rejected_candles || 0;
    const elDqDup = document.getElementById('dq-duplicates');
    if (elDqDup) elDqDup.textContent = dq.duplicate_candles || 0;

    // Data Quality View Stats
    const elDqPageTotal = document.getElementById('dq-page-total');
    if (elDqPageTotal) elDqPageTotal.textContent = (dq.total_candles || 0).toLocaleString();
    const elDqPageGaps = document.getElementById('dq-page-gaps');
    if (elDqPageGaps) elDqPageGaps.textContent = dq.missing_candles || 0;
    const elDqPageRec = document.getElementById('dq-page-rec');
    if (elDqPageRec) elDqPageRec.textContent = dq.recovered_candles || 0;

    // Timeframe Distribution in DQ view
    const elDqDistTbody = document.getElementById('dq-distribution-tbody');
    if (elDqDistTbody && primarySym && primarySym.timeframes) {
      const tfs = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
      let dHtml = '';
      tfs.forEach(tf => {
        const item = primarySym.timeframes[tf] || {};
        const count = item.count ? Number(item.count).toLocaleString() : '0';
        const latestTime = item.candle_time_wib || '—';
        const freshness = item.age_display ? `${item.age_display} ago` : '—';
        const badge = getStatusBadge(item.status);
        dHtml += `<tr>
          <td class="mono font-bold">${tf}</td>
          <td class="mono">${count} bars</td>
          <td class="mono">${latestTime}</td>
          <td class="mono">${freshness}</td>
          <td>${badge}</td>
        </tr>`;
      });
      elDqDistTbody.innerHTML = dHtml;
    }

    // 4. Account Telemetry (Accurate — No fake numbers)
    const elBal = document.getElementById('acc-balance');
    if (elBal) elBal.textContent = acc.balance !== null && acc.balance !== undefined ? formatCurrency(acc.balance) : 'N/A';
    const elEq = document.getElementById('acc-equity');
    if (elEq) elEq.textContent = acc.equity !== null && acc.equity !== undefined ? formatCurrency(acc.equity) : 'N/A';
    const elPnl = document.getElementById('acc-pnl');
    if (elPnl) {
      const pnlVal = acc.floating_pnl;
      if (pnlVal !== null && pnlVal !== undefined) {
        elPnl.textContent = (pnlVal >= 0 ? '+' : '') + formatCurrency(pnlVal);
        elPnl.className = 'stat-num mono ' + (pnlVal > 0 ? 'text-good' : (pnlVal < 0 ? 'text-danger' : ''));
      } else {
        elPnl.textContent = 'N/A';
        elPnl.className = 'stat-num mono';
      }
    }
    const elPosNote = document.getElementById('acc-positions-note');
    if (elPosNote) elPosNote.textContent = acc.positions_count !== undefined ? `${acc.positions_count} active open positions` : 'N/A';
    const elFMar = document.getElementById('acc-free-margin');
    if (elFMar) elFMar.textContent = acc.free_margin !== null && acc.free_margin !== undefined ? formatCurrency(acc.free_margin) : 'N/A';
    const elMar = document.getElementById('acc-margin');
    if (elMar) elMar.textContent = acc.margin !== null && acc.margin !== undefined ? formatCurrency(acc.margin) : 'N/A';
    const elAccAge = document.getElementById('acc-snapshot-age');
    if (elAccAge) elAccAge.textContent = acc.last_update_display || 'N/A';

    // 5. System View Telemetry
    const elSysMt5 = document.getElementById('sys-mt5-detail');
    if (elSysMt5) {
      elSysMt5.textContent = `${mt5.status || 'OFFLINE'} | ${mt5.broker || 'DEMO'} | Heartbeat: ${mt5.age_display || '--'}`;
    }
    const elSysFa = document.getElementById('sys-fastapi-detail');
    if (elSysFa) {
      elSysFa.textContent = `Port: ${fa.port || 8000} | Uptime: ${fa.uptime_display || '--'}`;
    }
    const elSysSup = document.getElementById('sys-supabase-detail');
    if (elSysSup) {
      elSysSup.textContent = `Status: ${sup.status || 'OFFLINE'} | Ping: ${sup.latency_ms !== null ? sup.latency_ms + ' ms' : '--'}`;
    }
    const elSupPing = document.getElementById('sys-supabase-ping');
    if (elSupPing) {
      elSupPing.textContent = sup.latency_ms !== null ? `${sup.latency_ms} ms` : 'N/A';
    }

    // 6. Ingest initial recent events from summary into system buffer if empty
    if (state.systemEventsBuffer.length === 0 && data.recent_events) {
      data.recent_events.forEach(ev => state.systemEventsBuffer.push(ev));
      const counter = document.getElementById('event-buffer-counter');
      if (counter) counter.textContent = `${state.systemEventsBuffer.length} / ${MAX_EVENT_BUFFER}`;
      renderSystemEventsStream();
    }

    // 7. Footer Timestamps
    const elFootDash = document.getElementById('footer-dashboard-update');
    if (elFootDash) elFootDash.textContent = new Date().toLocaleTimeString('id-ID');
    const elFootMkt = document.getElementById('footer-market-update');
    if (elFootMkt) {
      elFootMkt.textContent = data.last_market_data_received ? new Date(data.last_market_data_received).toLocaleTimeString('id-ID') : 'Active';
    }
  }

  // =========================================================================
  // HISTORICAL CANDLE INSPECTOR & LOGS
  // =========================================================================

  async function fetchHistoricalCandles(tf) {
    state.activeInspectorTf = tf;
    const tbody = document.getElementById('historical-candles-tbody');
    if (tbody) tbody.innerHTML = `<tr><td colspan="11" class="loading-cell">Loading ${tf} historical closed bars from Supabase...</td></tr>`;

    try {
      const resp = await fetch(`${API_BASE}/candles?symbol=XAUUSD.vx&timeframe=${tf}&limit=50`, { cache: 'no-store' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      if (!data.candles || data.candles.length === 0) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="11" class="loading-cell">No closed bars found for timeframe ${tf}.</td></tr>`;
        return;
      }

      let html = '';
      data.candles.forEach(c => {
        html += `<tr>
          <td class="mono text-muted">${c.id}</td>
          <td class="mono font-bold">${c.candle_time_wib}</td>
          <td class="mono text-muted">${c.candle_time_display}</td>
          <td class="mono">${formatPrice(c.open, 2)}</td>
          <td class="mono">${formatPrice(c.high, 2)}</td>
          <td class="mono">${formatPrice(c.low, 2)}</td>
          <td class="mono font-bold">${formatPrice(c.close, 2)}</td>
          <td class="mono">${Number(c.volume).toLocaleString()}</td>
          <td class="mono">${c.spread}</td>
          <td class="mono" style="font-size: 0.7rem;">${c.session ? c.session.replace(/,/g, ' ') : '—'}</td>
          <td><span class="status-pill status-sync">CLOSED</span></td>
        </tr>`;
      });
      if (tbody) tbody.innerHTML = html;
    } catch (err) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="11" class="loading-cell text-danger">Error loading ${tf} candles: ${err.message}</td></tr>`;
    }
  }

  async function fetchFullLogs() {
    const tbody = document.getElementById('full-logs-tbody');
    if (tbody) tbody.innerHTML = `<tr><td colspan="4" class="loading-cell">Loading system audit logs...</td></tr>`;

    try {
      const svc = state.logServiceFilter;
      const lvl = state.logLevelFilter;
      const resp = await fetch(`${API_BASE}/logs?limit=100&service=${svc}&level=${lvl}`, { cache: 'no-store' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      if (!data.logs || data.logs.length === 0) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="4" class="loading-cell">No logs matching filter criteria.</td></tr>`;
        return;
      }

      let html = '';
      data.logs.forEach(l => {
        const lvlCls = `event-level-${(l.level || 'info').toLowerCase()}`;
        html += `<tr>
          <td class="mono text-muted">${l.timestamp_display || '--:--:--'}</td>
          <td><span class="event-level ${lvlCls}">${l.level || 'INFO'}</span></td>
          <td class="mono font-bold">${l.service || 'SYS'}</td>
          <td>${l.message || ''}</td>
        </tr>`;
      });
      if (tbody) tbody.innerHTML = html;
    } catch (err) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="4" class="loading-cell text-danger">Error loading logs: ${err.message}</td></tr>`;
    }
  }

  // =========================================================================
  // NAVIGATION & EVENT LISTENERS SETUP
  // =========================================================================

  function setupNavigation() {
    const navButtons = document.querySelectorAll('.sidebar-nav .nav-item:not(.disabled)');
    const viewTitle = document.getElementById('current-view-title');

    navButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const view = btn.getAttribute('data-view');
        if (!view) return;

        navButtons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));
        const targetPanel = document.getElementById(`view-${view}`);
        if (targetPanel) targetPanel.classList.add('active');

        state.currentView = view;

        const titles = {
          'overview': 'MISSION CONTROL — LIVE MARKET',
          'market-data': 'MARKET DATA INSPECTOR',
          'data-quality': 'DATA QUALITY & TIMELINE AUDIT',
          'system': 'SYSTEM TOPOLOGY & LATENCY TELEMETRY',
          'logs': 'SYSTEM EVENT AUDIT LOGS'
        };
        if (viewTitle) viewTitle.textContent = titles[view] || 'MARKET DATA CENTER';

        if (view === 'overview' && state.chart) {
          const container = document.getElementById('tv-chart-container');
          if (container) state.chart.applyOptions({ width: container.clientWidth });
        } else if (view === 'market-data') {
          fetchHistoricalCandles(state.activeInspectorTf);
        } else if (view === 'system') {
          renderSystemEventsStream();
        } else if (view === 'logs') {
          fetchFullLogs();
        }
      });
    });
  }

  // Timeframe selector on Candlestick Chart Header
  function setupChartTfButtons() {
    const chartTfBtns = document.querySelectorAll('.chart-tf-btn');
    chartTfBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const tf = btn.getAttribute('data-chart-tf');
        if (!tf) return;

        chartTfBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        state.activeChartTf = tf;

        // Switch chart timeframe without page reload
        loadChartCandles(tf);

        // Update Bar 0 header label
        const elTfName = document.getElementById('bar0-tf-name');
        if (elTfName) elTfName.textContent = tf;

        // Immediately update Bar 0 display if available in memory
        if (state.latestLiveSnapshot && state.latestLiveSnapshot.timeframes && state.latestLiveSnapshot.timeframes[tf]) {
          state.activeBar0 = state.latestLiveSnapshot.timeframes[tf];
          renderBar0Details(tf, state.latestLiveSnapshot.timeframes[tf]);
        }
      });
    });
  }

  // Timeframe selector on Market Data view
  function setupInspectorTfButtons() {
    const tfBtns = document.querySelectorAll('.tf-btn');
    tfBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const tf = btn.getAttribute('data-tf');
        if (!tf) return;
        tfBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        fetchHistoricalCandles(tf);
      });
    });
  }

  // System Stream Controls (Filters, Pause, Clear)
  function setupSystemStreamControls() {
    const btnPause = document.getElementById('btn-pause-stream');
    if (btnPause) {
      btnPause.addEventListener('click', () => {
        state.streamPaused = !state.streamPaused;
        btnPause.textContent = state.streamPaused ? '▶️ Resume Stream' : '⏸️ Pause Stream';
        btnPause.style.borderColor = state.streamPaused ? 'var(--yellow-main)' : 'var(--border-color)';
        if (!state.streamPaused) {
          renderSystemEventsStream();
        }
      });
    }

    const btnClear = document.getElementById('btn-clear-stream');
    if (btnClear) {
      btnClear.addEventListener('click', () => {
        state.systemEventsBuffer = [];
        const counter = document.getElementById('event-buffer-counter');
        if (counter) counter.textContent = `0 / ${MAX_EVENT_BUFFER}`;
        renderSystemEventsStream();
      });
    }

    const selSvc = document.getElementById('sys-filter-service');
    if (selSvc) {
      selSvc.addEventListener('change', (e) => {
        state.streamServiceFilter = e.target.value;
        renderSystemEventsStream();
      });
    }

    const selLvl = document.getElementById('sys-filter-level');
    if (selLvl) {
      selLvl.addEventListener('change', (e) => {
        state.streamLevelFilter = e.target.value;
        renderSystemEventsStream();
      });
    }
  }

  // Logs View Filters
  function setupLogFilters() {
    const selSvc = document.getElementById('log-filter-service');
    const selLvl = document.getElementById('log-filter-level');

    if (selSvc) {
      selSvc.addEventListener('change', (e) => {
        state.logServiceFilter = e.target.value;
        fetchFullLogs();
      });
    }
    if (selLvl) {
      selLvl.addEventListener('change', (e) => {
        state.logLevelFilter = e.target.value;
        fetchFullLogs();
      });
    }
  }

  // Manual Refresh Button
  function setupRefreshButton() {
    const btn = document.getElementById('btn-manual-refresh');
    if (btn) {
      btn.addEventListener('click', () => {
        fetchSummary();
        loadChartCandles(state.activeChartTf);
        if (state.currentView === 'market-data') fetchHistoricalCandles(state.activeInspectorTf);
        if (state.currentView === 'logs') fetchFullLogs();
      });
    }
  }

  // Polling Engine for Summary Background
  function startPolling() {
    fetchSummary();
    state.pollTimer = setInterval(() => {
      fetchSummary();
      if (state.currentView === 'logs') fetchFullLogs();
    }, POLL_INTERVAL_MS);

    // 1-second countdown and age updater
    state.countdownTimer = setInterval(() => {
      updateBar0CountdownAndAge();
    }, 1000);
  }

  // =========================================================================
  // BOOTSTRAP INITIALIZATION
  // =========================================================================

  document.addEventListener('DOMContentLoaded', () => {
    setupNavigation();
    setupChartTfButtons();
    setupInspectorTfButtons();
    setupSystemStreamControls();
    setupLogFilters();
    setupRefreshButton();
    updateClocks();
    setInterval(updateClocks, 1000);

    // Initialize Chart & WebSocket Stream
    initLightweightChart();
    connectWebSocket();

    // Start background summary polling
    startPolling();
  });
})();
