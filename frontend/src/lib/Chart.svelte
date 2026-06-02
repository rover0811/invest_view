<script lang="ts">
  import {
    createChart,
    CandlestickSeries,
    HistogramSeries,
    LineSeries,
    CrosshairMode,
    LineStyle,
    ColorType,
    TickMarkType
  } from 'lightweight-charts';
  import type {
    CandlestickData,
    HistogramData,
    LineData,
    Logical,
    MouseEventParams,
    Time,
    UTCTimestamp
  } from 'lightweight-charts';
  import { fmtPrice, fmtVol } from './format';
  import { getCandles, type CandleInterval } from './api';
  import type { Candle, Snapshot, TimelineEvent } from './types';

  let { candles, timeline, snapshot, symbol }: { candles: Candle[]; timeline: TimelineEvent[]; snapshot: Snapshot; symbol: string } = $props();

  let container = $state<HTMLDivElement>();
  let activeTimeframe = $state<CandleInterval>('5m');
  let activeCandles = $state<Candle[]>([]);
  let loadError = $state(false);

  $effect(() => {
    activeCandles = candles;
    activeTimeframe = '5m';
  });
  let legendItems = $state<LegendItem[]>([]);
  let eventTicks = $state<EventTick[]>([]);
  let tooltip = $state<TooltipState>({ visible: false, left: 0, top: 0, label: '', kindClass: '' });
  let measure = $state<MeasureState>({ visible: false, left: 0, width: 0, direction: 'up' });

  type LegendItem = {
    label: string;
    value: string;
    className?: string;
  };

  type EventTick = {
    key: string;
    left: number;
    kind: 'alert' | 'pattern';
    label: string;
    kindClass: 'price-up' | 'price-down';
  };

  type TooltipState = {
    visible: boolean;
    left: number;
    top: number;
    label: string;
    kindClass: '' | 'price-up' | 'price-down';
  };

  type MeasureState = {
    visible: boolean;
    left: number;
    width: number;
    direction: 'up' | 'down';
  };

  type ChartCandle = CandlestickData<UTCTimestamp>;
  type ChartVolume = HistogramData<UTCTimestamp>;
  type ChartLine = LineData<UTCTimestamp>;

  // design-tokens와 동기화
  const T = {
    positive: '#dc2e47',
    negative: '#3182f6',
    flat: '#8a8f98',
    textSecondary: '#a0a4ad',
    textTertiary: '#6b6f78',
    chartGrid: 'rgba(42, 46, 57, 0.5)',
    borderSubtle: 'rgba(214, 224, 239, 0.09)',
    surfaceRaised: '#26262c',
    crosshairLine: 'rgba(214, 224, 239, 0.28)',
    transparent: 'rgba(0,0,0,0)',
    ma5: '#e0a64e',
    ma20: '#4ec9b0',
    ma60: '#b58cf0',
    vwap: '#8a8f98',
    positiveVolume: 'rgba(220, 46, 71, 0.5)',
    negativeVolume: 'rgba(49, 130, 246, 0.5)'
  } as const;

  const EVENT_LABELS: Record<string, string> = {
    GOLDEN_CROSS: '골든크로스',
    DEAD_CROSS: '데드크로스',
    RSI_OVERBOUGHT: 'RSI 과매수',
    RSI_OVERSOLD: 'RSI 과매도',
    MACD_BULLISH: 'MACD 매수',
    MACD_BEARISH: 'MACD 매도',
    PRICE_ALERT: '가격 알림',
    VI_IMMINENT: 'VI 임박'
  };

  const timeframes: { value: CandleInterval; label: string }[] = [
    { value: '5m', label: '5분' },
    { value: '1d', label: '일' },
    { value: '1w', label: '주' },
    { value: '1M', label: '월' }
  ];

  let fitChartContent = () => {};

  // chart `time` is a UTC epoch second but the data is bucketed on the KST wall
  // clock; shifting by +9h and reading UTC getters yields the KST calendar values.
  function kstParts(time: Time): { date: string; time: string } {
    const d = new Date((Number(time) + 9 * 3600) * 1000);
    const p = (n: number) => String(n).padStart(2, '0');
    return {
      date: `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`,
      time: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`
    };
  }

  function kstDate(time: Time): Date {
    return new Date((Number(time) + 9 * 3600) * 1000);
  }

  // Korean brokerage convention (Toss/미래에셋): axis ticks show only the boundary
  // unit, not a full timestamp — year ticks show the year, month ticks the month,
  // day ticks the day, intraday ticks the time. Lightweight Charts hands us the
  // unit via tickMarkType, so each label stays compact instead of "YYYY-MM-DD HH:mm".
  function formatAxisTick(time: Time, tickMarkType: TickMarkType): string {
    const d = kstDate(time);
    switch (tickMarkType) {
      case TickMarkType.Year:
        return `${d.getUTCFullYear()}`;
      case TickMarkType.Month:
        return `${d.getUTCMonth() + 1}월`;
      case TickMarkType.DayOfMonth:
        return `${d.getUTCDate()}일`;
      default:
        return kstParts(time).time;
    }
  }

  function formatCrosshairTime(time: Time): string {
    const { date, time: hm } = kstParts(time);
    return hm === '00:00' ? date : `${date} ${hm}`;
  }

  function asTime(time: number): UTCTimestamp {
    return time as UTCTimestamp;
  }

  function toTimeKey(time: Time | undefined): number | undefined {
    if (time == null) {
      return undefined;
    }
    const key = Number(time);
    return Number.isFinite(key) ? key : undefined;
  }

  function toChartCandle(candle: Candle): ChartCandle {
    return {
      time: asTime(candle.time),
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close
    };
  }

  function fromChartCandle(data: ChartCandle): Candle {
    return {
      time: Number(data.time),
      open: data.open,
      high: data.high,
      low: data.low,
      close: data.close
    };
  }

  function isChartCandle(data: unknown): data is ChartCandle {
    return typeof data === 'object' && data !== null && 'open' in data && 'high' in data && 'low' in data && 'close' in data;
  }

  function isChartVolume(data: unknown): data is ChartVolume {
    return typeof data === 'object' && data !== null && 'value' in data;
  }

  function calculateMA(source: Candle[], period: number): ChartLine[] {
    const maData: ChartLine[] = [];
    for (let i = period - 1; i < source.length; i += 1) {
      let sum = 0;
      for (let j = 0; j < period; j += 1) {
        sum += source[i - j].close;
      }
      maData.push({ time: asTime(source[i].time), value: sum / period });
    }
    return maData;
  }

  function renderLegend(candle: Candle | undefined, volume: number | undefined) {
    if (!candle) {
      legendItems = [];
      return;
    }

    const up = candle.close >= candle.open;
    const className = up ? 'price-up' : 'price-down';
    const change = ((candle.close - candle.open) / candle.open) * 100;
    const sign = change >= 0 ? '+' : '';

    legendItems = [
      { label: '시', value: fmtPrice(candle.open) },
      { label: '고', value: fmtPrice(candle.high) },
      { label: '저', value: fmtPrice(candle.low) },
      { label: '종', value: fmtPrice(candle.close), className },
      { label: '거래량', value: volume != null ? fmtVol(volume) : '-' },
      { label: '등락', value: sign + change.toFixed(2) + '%', className }
    ];
  }

  function renderMeasureLegend(startClose: number, endClose: number, bars: number) {
    const change = ((endClose - startClose) / startClose) * 100;
    const up = change >= 0;
    const className = up ? 'price-up' : 'price-down';
    const sign = up ? '+' : '';
    const diff = Math.round(endClose - startClose);

    legendItems = [
      { label: '구간', value: sign + change.toFixed(2) + '%', className },
      { label: '변화', value: sign + diff.toLocaleString('ko-KR'), className },
      { label: '시작', value: fmtPrice(startClose) },
      { label: '종료', value: fmtPrice(endClose) },
      { label: '기간', value: bars + '개봉' }
    ];
  }

  function hideTooltip() {
    tooltip = { visible: false, left: 0, top: 0, label: '', kindClass: '' };
  }

  function showEventTooltip(tick: EventTick) {
    if (!container) {
      return;
    }

    tooltip = {
      visible: true,
      left: tick.left,
      top: container.clientHeight - 8,
      label: tick.label,
      kindClass: tick.kindClass
    };
  }

  async function selectTimeframe(timeframe: CandleInterval) {
    if (timeframe === activeTimeframe) {
      return;
    }
    activeTimeframe = timeframe;
    loadError = false;
    try {
      activeCandles = await getCandles(symbol, timeframe);
    } catch {
      loadError = true;
    }
  }

  $effect(() => {
    if (!container) {
      return;
    }

    const chartContainer = container;
    let measuring = false;
    let measureStart: { x: number } | undefined;
    let disposed = false;
    const allCandles = activeCandles;
    const latestCandle = allCandles.at(-1);
    const volumeByTime = new Map<number, number>();
    const markerByTime = new Map<number, TimelineEvent>();

    timeline.forEach((event) => {
      markerByTime.set(event.time, event);
    });

    const fontSans = getComputedStyle(document.documentElement).getPropertyValue('--font-sans').trim() || 'Pretendard, system-ui, -apple-system, sans-serif';

    const chart = createChart(chartContainer, {
      layout: {
        background: { type: ColorType.Solid, color: T.transparent },
        textColor: T.textSecondary,
        fontFamily: fontSans,
        attributionLogo: false
      },
      grid: {
        vertLines: { color: T.chartGrid },
        horzLines: { color: T.chartGrid }
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: {
          width: 1,
          color: T.crosshairLine,
          style: LineStyle.Dotted,
          labelBackgroundColor: T.surfaceRaised
        },
        horzLine: {
          width: 1,
          color: T.crosshairLine,
          style: LineStyle.Dotted,
          labelBackgroundColor: T.surfaceRaised
        }
      },
      localization: {
        timeFormatter: formatCrosshairTime
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: formatAxisTick
      },
      rightPriceScale: {
        borderColor: T.borderSubtle
      }
    });

    document.fonts.ready.then(() => {
      if (!disposed) {
        chart.applyOptions({
          layout: { fontFamily: fontSans }
        });
      }
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: T.positive,
      downColor: T.negative,
      borderUpColor: T.positive,
      borderDownColor: T.negative,
      wickUpColor: T.positive,
      wickDownColor: T.negative
    });

    const volumeSeries = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: 'volume' },
        priceScaleId: ''
      },
      1
    );

    const ma5Series = chart.addSeries(LineSeries, {
      color: T.ma5,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });

    const ma20Series = chart.addSeries(LineSeries, {
      color: T.ma20,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });

    const ma60Series = chart.addSeries(LineSeries, {
      color: T.ma60,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });

    const vwapSeries = chart.addSeries(LineSeries, {
      color: T.vwap,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      title: 'VWAP'
    });

    candleSeries.setData(allCandles.map(toChartCandle));

    const volumeData: ChartVolume[] = [];
    const vwapData: ChartLine[] = [];
    let cumulativeVolume = 0;
    let cumulativeTypicalVolume = 0;

    allCandles.forEach((candle) => {
      const isUp = candle.close >= candle.open;
      const bodySize = Math.abs(candle.close - candle.open);
      const synthVol = 1000 + bodySize * 10;

      volumeByTime.set(candle.time, synthVol);
      volumeData.push({
        time: asTime(candle.time),
        value: synthVol,
        color: isUp ? T.positiveVolume : T.negativeVolume
      });

      const typicalPrice = (candle.high + candle.low + candle.close) / 3;
      cumulativeVolume += synthVol;
      cumulativeTypicalVolume += typicalPrice * synthVol;

      vwapData.push({
        time: asTime(candle.time),
        value: cumulativeTypicalVolume / cumulativeVolume
      });
    });

    volumeSeries.setData(volumeData);
    vwapSeries.setData(vwapData);
    ma5Series.setData(calculateMA(allCandles, 5));
    ma20Series.setData(calculateMA(allCandles, 20));
    ma60Series.setData(calculateMA(allCandles, 60));

    const panes = chart.panes();
    if (panes.length > 1) {
      panes[1].setStretchFactor(0.2);
    }

    if (snapshot.vi_trigger_price) {
      candleSeries.createPriceLine({
        price: snapshot.vi_trigger_price,
        color: T.textTertiary,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'VI'
      });
    }

    function buildEventLane() {
      const timeScale = chart.timeScale();
      eventTicks = timeline.flatMap((event, index) => {
        const x = timeScale.timeToCoordinate(asTime(event.time));
        if (x == null) {
          return [];
        }

        const kind = event.event_kind === 'alert' ? 'alert' : 'pattern';
        return [
          {
            key: event.time + '-' + event.event_type + '-' + index,
            left: x,
            kind,
            label: EVENT_LABELS[event.event_type] || event.event_type,
            kindClass: kind === 'alert' ? 'price-up' : 'price-down'
          }
        ];
      });
    }

    function syncSize() {
      chart.resize(chartContainer.clientWidth, chartContainer.clientHeight);
      buildEventLane();
    }

    const resizeObserver = new ResizeObserver((entries) => {
      if (entries.length === 0 || entries[0].target !== chartContainer) {
        return;
      }
      syncSize();
    });

    function clearMeasure() {
      measuring = false;
      measureStart = undefined;
      measure = { visible: false, left: 0, width: 0, direction: 'up' };
      chart.applyOptions({ handleScroll: true, handleScale: true });
      renderLegend(latestCandle, latestCandle ? volumeByTime.get(latestCandle.time) : undefined);
    }

    function handleCrosshairMove(param: MouseEventParams<Time>) {
      if (measuring) {
        return;
      }

      if (!param.point || !param.time) {
        hideTooltip();
        renderLegend(latestCandle, latestCandle ? volumeByTime.get(latestCandle.time) : undefined);
        return;
      }

      const candleData = param.seriesData.get(candleSeries);
      const volumePoint = param.seriesData.get(volumeSeries);
      const key = toTimeKey(param.time);

      if (isChartCandle(candleData)) {
        renderLegend(fromChartCandle(candleData), isChartVolume(volumePoint) ? volumePoint.value : key != null ? volumeByTime.get(key) : undefined);
      }

      const event = key != null ? markerByTime.get(key) : undefined;
      if (!event) {
        hideTooltip();
        return;
      }

      const label = EVENT_LABELS[event.event_type] || event.event_type;
      const kindClass = event.event_kind === 'alert' ? 'price-up' : 'price-down';
      const tooltipWidth = 120;
      const tooltipHeight = 32;
      let left = param.point.x + 14;
      let top = param.point.y + 14;
      if (left > chartContainer.clientWidth - tooltipWidth) {
        left = param.point.x - 14 - tooltipWidth;
      }
      if (top > chartContainer.clientHeight - tooltipHeight) {
        top = param.point.y - 14 - tooltipHeight;
      }
      tooltip = { visible: true, left, top, label, kindClass };
    }

    function handleMeasureStart(event: MouseEvent) {
      if (!event.shiftKey) {
        return;
      }

      event.preventDefault();
      const rect = chartContainer.getBoundingClientRect();
      measureStart = { x: event.clientX - rect.left };
      measuring = true;
      hideTooltip();
      chart.applyOptions({ handleScroll: false, handleScale: false });
    }

    function handleMeasureMove(event: MouseEvent) {
      if (!measuring || !measureStart || allCandles.length === 0) {
        return;
      }

      const rect = chartContainer.getBoundingClientRect();
      const endX = event.clientX - rect.left;
      const timeScale = chart.timeScale();
      const logicalStart = timeScale.coordinateToLogical(measureStart.x);
      const logicalEnd = timeScale.coordinateToLogical(endX);
      if (logicalStart == null || logicalEnd == null) {
        return;
      }

      const clamp = (index: number) => Math.max(0, Math.min(allCandles.length - 1, Math.round(index)));
      let startIndex = clamp(Number(logicalStart));
      let endIndex = clamp(Number(logicalEnd));
      if (startIndex === endIndex) {
        measure = { visible: false, left: 0, width: 0, direction: 'up' };
        return;
      }
      if (startIndex > endIndex) {
        const nextStart = endIndex;
        endIndex = startIndex;
        startIndex = nextStart;
      }

      const startClose = allCandles[startIndex].close;
      const endClose = allCandles[endIndex].close;
      const change = ((endClose - startClose) / startClose) * 100;
      const direction = change >= 0 ? 'up' : 'down';
      const bars = endIndex - startIndex;
      const xStart = timeScale.logicalToCoordinate(startIndex as Logical);
      const xEnd = timeScale.logicalToCoordinate(endIndex as Logical);
      if (xStart == null || xEnd == null) {
        return;
      }

      const left = Math.min(xStart, xEnd);
      const right = Math.max(xStart, xEnd);
      measure = { visible: true, left, width: right - left, direction };
      renderMeasureLegend(startClose, endClose, bars);
    }

    function handleMeasureEnd() {
      if (measuring) {
        clearMeasure();
      }
    }

    resizeObserver.observe(chartContainer);
    chart.subscribeCrosshairMove(handleCrosshairMove);
    chart.timeScale().subscribeVisibleTimeRangeChange(buildEventLane);
    chartContainer.addEventListener('mousedown', handleMeasureStart);
    chartContainer.addEventListener('mousemove', handleMeasureMove);
    window.addEventListener('mouseup', handleMeasureEnd);

    renderLegend(latestCandle, latestCandle ? volumeByTime.get(latestCandle.time) : undefined);
    fitChartContent = () => chart.timeScale().fitContent();
    syncSize();
    chart.timeScale().fitContent();
    buildEventLane();

    return () => {
      disposed = true;
      fitChartContent = () => {};
      eventTicks = [];
      hideTooltip();
      measure = { visible: false, left: 0, width: 0, direction: 'up' };
      resizeObserver.disconnect();
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      chart.timeScale().unsubscribeVisibleTimeRangeChange(buildEventLane);
      chartContainer.removeEventListener('mousedown', handleMeasureStart);
      chartContainer.removeEventListener('mousemove', handleMeasureMove);
      window.removeEventListener('mouseup', handleMeasureEnd);
      chart.remove();
    };
  });
</script>

<section class="chart-panel">
  <div class="panel-header">
    <div class="ohlc-legend tnum" data-testid="ohlc-legend">
      {#each legendItems as item}
        <span class="item">
          <span class="label">{item.label}</span>
          <span class={item.className}>{item.value}</span>
        </span>
      {/each}
    </div>
  </div>

  <div class="timeframe-row" aria-label="차트 기간 선택">
    {#each timeframes as timeframe}
      <button
        class:active={activeTimeframe === timeframe.value}
        class="timeframe-btn"
        type="button"
        data-tf={timeframe.value}
        onclick={() => selectTimeframe(timeframe.value)}
      >
        {timeframe.label}
      </button>
    {/each}
    {#if loadError}
      <span class="tf-error" data-testid="tf-error">불러오기 실패</span>
    {/if}
  </div>

  <div bind:this={container} class="chart-container">
    <div class="ma-legend" data-testid="ma-legend">
      <span class="ma-item ma5"><i></i>MA5</span>
      <span class="ma-item ma20"><i></i>MA20</span>
      <span class="ma-item ma60"><i></i>MA60</span>
      <span class="ma-item vwap"><i></i>VWAP</span>
    </div>
    <div
      class="marker-tooltip"
      data-testid="marker-tooltip"
      style="display: {tooltip.visible ? 'block' : 'none'}; left: {tooltip.left}px; top: {tooltip.top}px;"
    >
      {#if tooltip.kindClass}
        <span class={tooltip.kindClass}>●</span>
      {/if}
      {tooltip.label}
    </div>
    <div
      class="measure-rect {measure.direction}"
      data-testid="measure-rect"
      style="display: {measure.visible ? 'block' : 'none'}; left: {measure.left}px; width: {measure.width}px;"
    ></div>
  </div>

  <div class="event-lane" data-testid="event-lane">
    <span class="event-lane-label">이벤트</span>
    <span class="event-lane-hint">⇧ Shift + 드래그 = 구간 등락률 측정</span>
    {#each eventTicks as tick (tick.key)}
      <button
        class="event-tick {tick.kind}"
        type="button"
        aria-label={tick.label}
        data-testid="event-tick"
        style="left: {tick.left}px;"
        onmouseenter={() => showEventTooltip(tick)}
        onmouseleave={hideTooltip}
      ></button>
    {/each}
  </div>
</section>

<style>
  .chart-panel {
    background-color: var(--surface-body);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    width: 100%;
    height: 460px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: var(--space-3) var(--space-4);
    border-bottom: 1px solid var(--border-subtle);
  }

  .timeframe-row {
    display: flex;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-4);
    background-color: var(--surface-floor);
    border-bottom: 1px solid var(--border-subtle);
  }

  .timeframe-btn {
    background: transparent;
    border: none;
    color: var(--text-tertiary);
    font-family: var(--font-sans);
    font-size: 13px;
    font-weight: 500;
    padding: 4px 12px;
    border-radius: 12px;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .timeframe-btn:hover {
    color: var(--text-secondary);
    background-color: var(--surface-overlay);
  }

  .timeframe-btn.active {
    color: var(--text-primary);
    background-color: var(--surface-raised);
  }

  .tf-error {
    align-self: center;
    margin-left: auto;
    font-size: 11px;
    color: var(--color-negative);
  }

  .chart-container {
    flex: 1;
    min-height: 0;
    position: relative;
    width: 100%;
  }

  .ohlc-legend {
    font-size: 12px;
    color: var(--text-primary);
    display: flex;
    gap: var(--space-3);
    align-items: baseline;
    min-height: 16px;
  }

  .ohlc-legend .item {
    color: var(--text-primary);
  }

  .ohlc-legend .label {
    color: var(--text-tertiary);
    margin-right: 4px;
    font-weight: 500;
  }

  .marker-tooltip {
    position: absolute;
    background: var(--surface-overlay);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    padding: 6px 10px;
    font-size: 12px;
    color: var(--text-primary);
    pointer-events: none;
    z-index: 10;
    box-shadow: 0 4px 12px color-mix(in srgb, var(--surface-floor) 80%, transparent);
    white-space: nowrap;
  }

  .measure-rect {
    position: absolute;
    top: 0;
    bottom: 0;
    pointer-events: none;
    z-index: 9;
    border-left: 1px dashed var(--border-strong);
    border-right: 1px dashed var(--border-strong);
  }

  .measure-rect.up {
    background: color-mix(in srgb, var(--color-positive) 8%, transparent);
    border-color: color-mix(in srgb, var(--color-positive) 55%, transparent);
  }

  .measure-rect.down {
    background: color-mix(in srgb, var(--color-negative) 8%, transparent);
    border-color: color-mix(in srgb, var(--color-negative) 55%, transparent);
  }

  .ma-legend {
    position: absolute;
    top: var(--space-2);
    left: var(--space-3);
    z-index: 8;
    display: flex;
    gap: var(--space-3);
    pointer-events: none;
    font-size: 11px;
  }

  .ma-item {
    display: flex;
    align-items: center;
    gap: 5px;
    color: var(--text-secondary);
  }

  .ma-item i {
    display: inline-block;
    width: 10px;
    height: 2px;
    border-radius: 1px;
  }

  .ma-item.ma5 i { background: var(--ma5); }
  .ma-item.ma20 i { background: var(--ma20); }
  .ma-item.ma60 i { background: var(--ma60); }
  .ma-item.vwap i { background: var(--vwap); }

  .event-lane {
    position: relative;
    height: 32px;
    flex: 0 0 auto;
    border-top: 1px solid var(--border-strong);
    background: var(--surface-body);
  }

  .event-lane-label {
    position: absolute;
    left: var(--space-3);
    top: 50%;
    transform: translateY(-50%);
    font-size: 11px;
    font-weight: 600;
    color: var(--text-secondary);
    letter-spacing: 0.02em;
  }

  .event-lane-hint {
    position: absolute;
    right: var(--space-4);
    top: 50%;
    transform: translateY(-50%);
    font-size: 11px;
    color: var(--text-tertiary);
  }

  .event-tick {
    position: absolute;
    top: 50%;
    transform: translate(-50%, -50%);
    width: 10px;
    height: 10px;
    padding: 0;
    border-radius: 3px;
    cursor: pointer;
    border: 1px solid var(--surface-body);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--surface-floor) 70%, transparent);
  }

  .event-tick.alert {
    background: var(--color-positive);
  }

  .event-tick.pattern {
    background: var(--color-negative);
  }
</style>
