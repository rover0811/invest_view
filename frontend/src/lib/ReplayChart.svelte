<script lang="ts">
  import { untrack } from 'svelte';
  import {
    CandlestickSeries,
    ColorType,
    CrosshairMode,
    LineSeries,
    LineStyle,
    TickMarkType,
    createChart,
    createSeriesMarkers,
  } from 'lightweight-charts';
  import type { CandlestickData, LineData, SeriesMarker, Time, UTCTimestamp } from 'lightweight-charts';
  import { computeYDomain } from './chartScale';
  import { fmtPrice } from './format';
  import {
    aggregateReplayBars,
    computeStateBands,
    formatReplayTime,
    layoutReplayAnnotationTags,
    planReplaySeriesUpdate,
    replayTimeToChartCoordinate,
    shouldRecomputeReplayOverlays,
    visibleBarsAt,
    type ReplayBar,
    type ReplayBarInterval,
    type ReplayData,
    type ReplayOverlayKey,
    type StateBand,
  } from './replay';

  type PlayheadFrameDriver = (playheadMs: number) => void;
  type RenderedBand = StateBand & { key: number; left: number; width: number };
  type RenderedThreshold = { key: number; label: string; y: number; className: string };
  type RenderedAnnotation = { key: number; label: string; x: number; y: number; className: string };
  type RenderedCurrentPrice = { label: string; y: number; className: string };

  let {
    data,
    playhead,
    registerPlayheadFrame,
  }: {
    data: ReplayData;
    playhead: string;
    registerPlayheadFrame?: (driver: PlayheadFrameDriver | null) => void;
  } = $props();

  let host = $state<HTMLDivElement>();
  let playheadElement = $state<HTMLDivElement>();
  let futureMaskElement = $state<HTMLDivElement>();
  let updateChart = (_playhead: string, _interval: ReplayBarInterval) => {};
  let priceScaleWidth = $state(0);
  let renderedBands = $state<RenderedBand[]>([]);
  let renderedThresholds = $state<RenderedThreshold[]>([]);
  let renderedAnnotations = $state<RenderedAnnotation[]>([]);
  let renderedCurrentPrice = $state<RenderedCurrentPrice | null>(null);
  let interval = $state<ReplayBarInterval>(3);
  let intervalSelected = false;

  const intervalOptions: Array<{ value: ReplayBarInterval; label: string }> = [
    { value: 1, label: '1분' },
    { value: 3, label: '3분' },
    { value: 5, label: '5분' },
  ];

  const lastTime = $derived(data.bars.at(-1)?.t ?? playhead);
  const lastMs = $derived(Date.parse(lastTime));
  const afterClose = $derived(Date.parse(playhead) > lastMs);
  const priceDomain = $derived.by(() => {
    const enter = data.thresholds.enter_pct / 100;
    const exit = data.thresholds.exit_pct / 100;
    const values = data.bars.flatMap((bar) => [bar.l, bar.h]);
    values.push(
      data.prev_close * (1 + enter),
      data.prev_close * (1 + exit),
      data.prev_close * (1 - exit),
      data.prev_close * (1 - enter),
    );
    return computeYDomain(values);
  });

  function toTimestamp(iso: string): UTCTimestamp {
    return Math.floor(Date.parse(iso) / 1000) as UTCTimestamp;
  }

  function toCandle(bar: ReplayBar): CandlestickData<UTCTimestamp> {
    return { time: toTimestamp(bar.t), open: bar.o, high: bar.h, low: bar.l, close: bar.c };
  }

  function timestampToDate(time: Time): Date {
    return new Date(Number(time) * 1000);
  }

  function formatAxisTick(time: Time, tickMarkType: TickMarkType): string {
    const date = timestampToDate(time);
    const dateParts = new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul',
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
    }).formatToParts(date);
    const part = (type: Intl.DateTimeFormatPartTypes) => dateParts.find((item) => item.type === type)?.value ?? '';
    if (tickMarkType === TickMarkType.Year) return part('year');
    if (tickMarkType === TickMarkType.Month) return `${part('month')}월`;
    if (tickMarkType === TickMarkType.DayOfMonth) return `${part('day')}일`;
    return new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date);
  }

  function formatCrosshairTime(time: Time): string {
    return formatReplayTime(timestampToDate(time).toISOString(), data.date);
  }

  function token(styles: CSSStyleDeclaration, name: string, fallback: string): string {
    return styles.getPropertyValue(name).trim() || fallback;
  }

  function selectInterval(nextInterval: ReplayBarInterval) {
    intervalSelected = true;
    interval = nextInterval;
  }

  function replaceArray<T>(target: T[], next: T[]) {
    target.splice(0, target.length, ...next);
  }

  $effect(() => {
    const mobile = window.matchMedia('(max-width: 720px)');
    const applyDefault = () => {
      if (!intervalSelected) interval = mobile.matches ? 5 : 3;
    };
    applyDefault();
    mobile.addEventListener('change', applyDefault);
    return () => mobile.removeEventListener('change', applyDefault);
  });

  $effect(() => {
    if (!host || !playheadElement || !futureMaskElement || data.bars.length === 0) return;

    const chartHost = host;
    const playheadNode = playheadElement;
    const futureMaskNode = futureMaskElement;
    const finalBar = data.bars[data.bars.length - 1];
    const fullDayDomain = untrack(() => priceDomain);
    const styles = getComputedStyle(document.documentElement);
    const positive = token(styles, '--color-positive', 'currentColor');
    const negative = token(styles, '--color-negative', 'currentColor');
    const flat = token(styles, '--color-flat', 'currentColor');
    const textSecondary = token(styles, '--text-secondary', 'currentColor');
    const surfaceRaised = token(styles, '--surface-raised', 'currentColor');
    const chartGrid = token(styles, '--chart-grid', 'currentColor');
    const borderSubtle = token(styles, '--border-subtle', 'currentColor');
    const fontSans = token(styles, '--font-sans', 'Pretendard, sans-serif');
    const transparent = 'transparent';
    const space1 = Number.parseFloat(token(styles, '--space-1', '4px'));
    const space2 = Number.parseFloat(token(styles, '--space-2', '8px'));
    const space3 = Number.parseFloat(token(styles, '--space-3', '12px'));
    const space6 = Number.parseFloat(token(styles, '--space-6', '24px'));
    const space8 = Number.parseFloat(token(styles, '--space-8', '32px'));
    const annotationWidth = space8 + space8;
    const annotationGap = space2;
    const annotationRowHeight = space8;
    const playheadLabelFallbackWidth = space8 + space6;
    const thresholdLabelReserve = space8 + space6;
    const labelSideHysteresis = space3;

    const chart = createChart(chartHost, {
      layout: {
        background: { type: ColorType.Solid, color: transparent },
        textColor: textSecondary,
        fontFamily: fontSans,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: chartGrid },
        horzLines: { color: chartGrid },
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { width: 1, color: borderSubtle, style: LineStyle.Dotted, labelBackgroundColor: surfaceRaised },
        horzLine: { width: 1, color: borderSubtle, style: LineStyle.Dotted, labelBackgroundColor: surfaceRaised },
      },
      localization: {
        timeFormatter: formatCrosshairTime,
        priceFormatter: (price: number) => fmtPrice(price),
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: formatAxisTick,
        borderColor: borderSubtle,
        barSpacing: 6,
        minBarSpacing: 2,
        rightOffset: 1,
      },
      rightPriceScale: { borderColor: borderSubtle },
      handleScale: false,
      handleScroll: false,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: positive,
      downColor: negative,
      borderUpColor: positive,
      borderDownColor: negative,
      wickUpColor: positive,
      wickDownColor: negative,
      priceLineVisible: false,
      lastValueVisible: false,
      autoscaleInfoProvider: () => ({
        priceRange: {
          minValue: fullDayDomain.min,
          maxValue: fullDayDomain.max,
        },
        margins: {
          above: space8 + space8 + space8,
          below: space6,
        },
      }),
    });
    const timeAnchor = chart.addSeries(LineSeries, {
      color: transparent,
      lineVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
      priceScaleId: '',
    });
    const anchorStart = Number(toTimestamp(data.bars[0].t));
    const anchorEnd = Number(toTimestamp(finalBar.t));

    const enter = data.thresholds.enter_pct / 100;
    const exit = data.thresholds.exit_pct / 100;
    const thresholdSpecs = [
      { price: data.prev_close * (1 + enter), label: `+${data.thresholds.enter_pct.toFixed(1)}%`, color: positive, style: LineStyle.Dashed, className: 'positive' },
      { price: data.prev_close * (1 + exit), label: `+${data.thresholds.exit_pct.toFixed(1)}%`, color: flat, style: LineStyle.Dotted, className: 'neutral' },
      { price: data.prev_close * (1 - exit), label: `-${data.thresholds.exit_pct.toFixed(1)}%`, color: flat, style: LineStyle.Dotted, className: 'neutral' },
      { price: data.prev_close * (1 - enter), label: `-${data.thresholds.enter_pct.toFixed(1)}%`, color: negative, style: LineStyle.Dashed, className: 'negative' },
    ];
    thresholdSpecs.forEach((line) => {
      candleSeries.createPriceLine({
        price: line.price,
        color: line.color,
        lineWidth: 1,
        lineStyle: line.style,
        axisLabelVisible: false,
        title: '',
      });
    });

    const markerApi = createSeriesMarkers(candleSeries, []);
    const replayBands = computeStateBands(data.states, finalBar.t);
    const playheadLabelNode = playheadNode.querySelector('span');
    const playheadLabelWidth = playheadLabelNode?.getBoundingClientRect().width
      ?? playheadLabelFallbackWidth;

    let latestPlayheadMs = Date.parse(untrack(() => playhead));
    let smoothPlayheadMs = latestPlayheadMs;
    let latestInterval = untrack(() => interval);
    let renderedBars: ReplayBar[] | null = null;
    let revealedStateCount = -1;
    let overlayKey: ReplayOverlayKey | null = null;
    let latestPriceScaleWidth = 0;
    let labelOnLeft = playheadNode.classList.contains('label-left');
    let chartWidth = chartHost.clientWidth;
    let chartHeight = chartHost.clientHeight;
    let resizeAnimationFrame = 0;
    let overlayAnimationFrame = 0;
    let pendingWidthFit = false;

    function setAnchorData(nextInterval: ReplayBarInterval) {
      const anchorData: LineData<UTCTimestamp>[] = [];
      const step = nextInterval * 60;
      for (let second = anchorStart; second <= anchorEnd; second += step) {
        anchorData.push({ time: second as UTCTimestamp, value: data.prev_close });
      }
      if (Number(anchorData.at(-1)?.time) !== anchorEnd) {
        anchorData.push({ time: anchorEnd as UTCTimestamp, value: data.prev_close });
      }
      timeAnchor.setData(anchorData);
    }

    function xForTimeMs(timeMs: number): number {
      const axisWidth = Math.max(chart.priceScale('right').width(), space8 + space8);
      return replayTimeToChartCoordinate(
        data.bars,
        new Date(timeMs).toISOString(),
        chartHost.clientWidth - axisWidth,
      );
    }

    function xForTime(iso: string): number {
      return xForTimeMs(Date.parse(iso));
    }

    function movePlayhead(nextPlayheadMs: number) {
      smoothPlayheadMs = nextPlayheadMs;
      const axisWidth = Math.max(chart.priceScale('right').width(), space8 + space8);
      const hostWidth = chartHost.clientWidth;
      const plotRight = Math.max(0, hostWidth - axisWidth);
      const nextPlayheadX = replayTimeToChartCoordinate(
        data.bars,
        new Date(nextPlayheadMs).toISOString(),
        plotRight,
      );
      const labelBoundary = plotRight - playheadLabelWidth - space2;
      const nextLabelOnLeft = labelOnLeft
        ? nextPlayheadX >= labelBoundary - labelSideHysteresis
        : nextPlayheadX > labelBoundary;
      const transform = `translate3d(${nextPlayheadX}px, 0, 0)`;
      playheadNode.style.transform = transform;
      futureMaskNode.style.transform = transform;
      if (nextLabelOnLeft !== labelOnLeft) {
        labelOnLeft = nextLabelOnLeft;
        playheadNode.classList.toggle('label-left', labelOnLeft);
        syncOverlays(nextPlayheadMs, revealedStatesAt(nextPlayheadMs));
      }
    }

    function revealedStatesAt(nextPlayheadMs: number) {
      return data.states.filter((state) => Date.parse(state.t) <= nextPlayheadMs);
    }

    function syncOverlays(nextPlayheadMs: number, revealedStates: ReplayData['states']) {
      const currentBar = data.bars.findLast((bar) => Date.parse(bar.t) <= nextPlayheadMs);
      const nextPriceScaleWidth = Math.max(chart.priceScale('right').width(), space8 + space8);
      const nextOverlayKey: ReplayOverlayKey = {
        width: chartHost.clientWidth,
        height: chartHost.clientHeight,
        priceScaleWidth: nextPriceScaleWidth,
        interval: latestInterval,
        revealedStateCount: revealedStates.length,
        currentBarTime: currentBar?.t ?? null,
        labelOnLeft,
      };
      if (!shouldRecomputeReplayOverlays(overlayKey, nextOverlayKey)) return;

      overlayKey = nextOverlayKey;
      const nextPlayheadX = xForTimeMs(nextPlayheadMs);
      if (latestPriceScaleWidth !== nextPriceScaleWidth) {
        latestPriceScaleWidth = nextPriceScaleWidth;
        priceScaleWidth = nextPriceScaleWidth;
      }
      const plotRight = Math.max(0, chartHost.clientWidth - nextPriceScaleWidth);
      const nextBands = replayBands.map((band, index) => {
        const left = xForTime(band.start);
        const right = xForTime(band.end);
        return { ...band, key: index, left, width: Math.max(0, right - left) };
      });
      replaceArray(renderedBands, nextBands);

      const currentPriceY = currentBar ? candleSeries.priceToCoordinate(currentBar.c) : null;
      const nextCurrentPrice = currentBar && currentPriceY != null
        ? {
            label: fmtPrice(currentBar.c),
            y: Number(currentPriceY),
            className: currentBar.c > data.prev_close
              ? 'positive'
              : currentBar.c < data.prev_close ? 'negative' : 'neutral',
          }
        : null;
      if (nextCurrentPrice == null) {
        renderedCurrentPrice = null;
      } else if (renderedCurrentPrice == null) {
        renderedCurrentPrice = nextCurrentPrice;
      } else {
        renderedCurrentPrice.label = nextCurrentPrice.label;
        renderedCurrentPrice.y = nextCurrentPrice.y;
        renderedCurrentPrice.className = nextCurrentPrice.className;
      }

      const minimumY = space6 + space2;
      const maximumY = Math.max(minimumY, chartHost.clientHeight - space6 - space6);
      const minimumGap = space2 + space3;
      const thresholdPoints = thresholdSpecs
        .flatMap((threshold) => {
          const coordinate = candleSeries.priceToCoordinate(threshold.price);
          return coordinate == null ? [] : [{ ...threshold, y: Number(coordinate) }];
        })
        .sort((a, b) => a.y - b.y);
      let previousY = minimumY - minimumGap;
      const nudged = thresholdPoints.map((threshold) => {
        const y = Math.max(minimumY, threshold.y, previousY + minimumGap);
        previousY = y;
        return { ...threshold, y };
      });
      const overflow = Math.max(0, (nudged.at(-1)?.y ?? maximumY) - maximumY);
      const nextThresholds = nudged.map((threshold, index) => ({
        key: index,
        label: threshold.label,
        y: Math.max(minimumY, threshold.y - overflow),
        className: threshold.className,
      }));
      replaceArray(renderedThresholds, nextThresholds);

      const annotationSpecs = revealedStates.map((state) => ({
        label: state.state === 'POSITIVE' ? '긍정 진입' : state.state === 'NEGATIVE' ? '부정 진입' : '중립 전환',
        anchorX: xForTime(state.t),
        className: state.state.toLowerCase(),
      }));
      const playheadLabelRange = labelOnLeft
        ? { start: nextPlayheadX - space2 - playheadLabelWidth, end: nextPlayheadX - space2, row: 0 }
        : { start: nextPlayheadX + space2, end: nextPlayheadX + space2 + playheadLabelWidth, row: 0 };
      const thresholdLabelRanges = Array.from({ length: annotationSpecs.length }, (_, row) => row)
        .flatMap((row) => {
          const tagTop = space1 + row * annotationRowHeight;
          const tagBottom = tagTop + space6;
          const overlapsThreshold = nextThresholds.some((threshold) => (
            threshold.y + space2 > tagTop && threshold.y - space2 < tagBottom
          ));
          return overlapsThreshold
            ? [{ start: plotRight - thresholdLabelReserve, end: plotRight, row }]
            : [];
        });
      const annotationLayout = layoutReplayAnnotationTags(
        annotationSpecs.map((annotation) => annotation.anchorX),
        annotationWidth,
        plotRight,
        annotationGap,
        [
          playheadLabelRange,
          ...thresholdLabelRanges,
        ],
      );

      const nextAnnotations = annotationSpecs.map((annotation, index) => ({
        key: index,
        label: annotation.label,
        x: annotationLayout[index].x,
        y: space1 + annotationLayout[index].row * annotationRowHeight,
        className: annotation.className,
      }));
      replaceArray(renderedAnnotations, nextAnnotations);
    }

    function syncStateDecorations(nextPlayheadMs: number, aggregatedBars: ReplayBar[], force = false) {
      const revealedStates = revealedStatesAt(nextPlayheadMs);
      if (!force && revealedStates.length === revealedStateCount) {
        syncOverlays(nextPlayheadMs, revealedStates);
        return;
      }

      const markers: SeriesMarker<UTCTimestamp>[] = revealedStates.flatMap((state) => {
        const markerBar = aggregatedBars.findLast((bar) => Date.parse(bar.t) <= Date.parse(state.t));
        if (!markerBar) return [];
        return [{
          time: toTimestamp(markerBar.t),
          position: state.state === 'NEGATIVE' ? 'belowBar' : 'aboveBar',
          color: state.state === 'POSITIVE' ? positive : state.state === 'NEGATIVE' ? negative : flat,
          shape: state.state === 'POSITIVE' ? 'arrowUp' : state.state === 'NEGATIVE' ? 'arrowDown' : 'circle',
        } satisfies SeriesMarker<UTCTimestamp>];
      });

      markerApi.setMarkers(markers);
      revealedStateCount = revealedStates.length;
      syncOverlays(nextPlayheadMs, revealedStates);
    }

    function syncSize(nextWidth: number, nextHeight: number) {
      const widthChanged = nextWidth !== chartWidth;
      const heightChanged = nextHeight !== chartHeight;
      if (!widthChanged && !heightChanged) return;

      chartWidth = nextWidth;
      chartHeight = nextHeight;
      pendingWidthFit ||= widthChanged;
      cancelAnimationFrame(resizeAnimationFrame);
      resizeAnimationFrame = requestAnimationFrame(() => {
        chart.resize(chartWidth, chartHeight);
        if (pendingWidthFit) {
          chart.timeScale().fitContent();
        }
        pendingWidthFit = false;
        movePlayhead(smoothPlayheadMs);
        syncOverlays(latestPlayheadMs, revealedStatesAt(latestPlayheadMs));
      });
    }

    updateChart = (nextPlayhead: string, nextInterval: ReplayBarInterval) => {
      const nextMs = Date.parse(nextPlayhead);
      const visibleBars = visibleBarsAt(data.bars, nextPlayhead);
      const aggregatedBars = aggregateReplayBars(visibleBars, nextInterval);
      const intervalChanged = nextInterval !== latestInterval;
      const seekedBackward = nextMs < latestPlayheadMs;
      const initialLoad = renderedBars == null;
      const plan = planReplaySeriesUpdate(renderedBars, aggregatedBars, intervalChanged || seekedBackward);

      if (plan.mode === 'replace') {
        candleSeries.setData(plan.bars.map(toCandle));
      } else if (plan.mode === 'update') {
        plan.bars.forEach((bar) => {
          candleSeries.update(toCandle(bar));
        });
      }

      renderedBars = aggregatedBars;
      latestPlayheadMs = nextMs;
      latestInterval = nextInterval;

      if (initialLoad || intervalChanged) {
        setAnchorData(nextInterval);
        chart.timeScale().fitContent();
      }

      movePlayhead(nextMs);
      syncStateDecorations(nextMs, aggregatedBars, initialLoad || intervalChanged);
    };

    const resizeObserver = new ResizeObserver(() => syncSize(chartHost.clientWidth, chartHost.clientHeight));
    resizeObserver.observe(chartHost);
    untrack(() => updateChart(new Date(latestPlayheadMs).toISOString(), latestInterval));
    registerPlayheadFrame?.(movePlayhead);
    overlayAnimationFrame = requestAnimationFrame(() => {
      movePlayhead(smoothPlayheadMs);
      syncOverlays(latestPlayheadMs, revealedStatesAt(latestPlayheadMs));
    });

    return () => {
      updateChart = () => {};
      registerPlayheadFrame?.(null);
      cancelAnimationFrame(resizeAnimationFrame);
      cancelAnimationFrame(overlayAnimationFrame);
      resizeObserver.disconnect();
      markerApi.detach();
      chart.remove();
    };
  });

  $effect(() => {
    const nextPlayhead = playhead;
    const nextInterval = interval;
    untrack(() => updateChart(nextPlayhead, nextInterval));
  });
</script>

<section class="replay-chart panel" aria-label="가격 상태 차트">
  <header class="chart-head">
    <div class="chart-copy">
      <span class="eyebrow">가격 상태</span>
      <strong>전일 종가 대비 상태 변화를 재생합니다.</strong>
    </div>
    <div class="chart-tools">
      <div class="interval-control" aria-label="봉 간격">
        {#each intervalOptions as option}
          <button
            type="button"
            class:active={interval === option.value}
            aria-pressed={interval === option.value}
            onclick={() => selectInterval(option.value)}
          >{option.label}</button>
        {/each}
      </div>
      <span class="price-range tnum">{fmtPrice(priceDomain.min)}원에서 {fmtPrice(priceDomain.max)}원</span>
    </div>
  </header>
  <div class="chart-stage" class:after-close={afterClose}>
    <div class="band-layer" aria-hidden="true">
      {#each renderedBands as band (band.key)}
        <span
          class="state-band {band.state.toLowerCase()}"
          style="left: {band.left}px; width: {band.width}px;"
        ></span>
      {/each}
    </div>
    <div bind:this={host} class="chart-host"></div>
    <div bind:this={futureMaskElement} class="future-mask" aria-hidden="true"></div>
    <div class="after-close-dim" aria-hidden="true"></div>
    {#each renderedThresholds as threshold (threshold.key)}
      <span
        class="threshold-label {threshold.className}"
        style="top: {threshold.y}px; right: calc({priceScaleWidth}px + var(--space-2));"
        aria-hidden="true"
      >{threshold.label}</span>
    {/each}
    {#if renderedCurrentPrice}
      <span
        class="current-price-label {renderedCurrentPrice.className}"
        style="top: {renderedCurrentPrice.y}px; width: max({priceScaleWidth}px, calc(var(--space-8) + var(--space-8)));"
        aria-hidden="true"
      >{renderedCurrentPrice.label}</span>
    {/if}
    {#each renderedAnnotations as annotation (annotation.key)}
      <span
        class="state-annotation {annotation.className}"
        style="left: {annotation.x}px; top: {annotation.y}px;"
        aria-hidden="true"
      >{annotation.label}</span>
    {/each}
    <div bind:this={playheadElement} class="playhead" aria-hidden="true">
      <span>
        <strong>{formatReplayTime(playhead, data.date)}</strong>
        {#if afterClose}<small>장 마감 뒤</small>{/if}
      </span>
    </div>
  </div>
</section>

<style>
  .replay-chart {
    box-sizing: border-box;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .chart-head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-3) var(--space-4);
    border-bottom: 1px solid var(--border-subtle);
  }

  .chart-copy {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .chart-tools {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }

  .interval-control {
    display: flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    background: var(--surface-floor);
  }

  .interval-control button {
    padding: var(--space-1) var(--space-2);
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--text-tertiary);
    font-family: var(--font-sans);
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
  }

  .interval-control button:hover,
  .interval-control button.active {
    background: var(--surface-raised);
    color: var(--text-primary);
  }

  .interval-control button:focus-visible {
    outline: 2px solid var(--brand);
    outline-offset: 2px;
  }

  .eyebrow,
  .price-range {
    font-size: 11px;
    color: var(--text-tertiary);
  }

  .chart-head strong {
    font-size: 14px;
    color: var(--text-primary);
  }

  .chart-stage {
    position: relative;
    min-height: 360px;
    flex: 1;
    overflow: hidden;
  }

  .chart-host,
  .band-layer,
  .future-mask,
  .after-close-dim {
    position: absolute;
    inset: 0;
  }

  .threshold-label,
  .current-price-label,
  .state-annotation {
    position: absolute;
    z-index: 4;
    pointer-events: none;
    white-space: nowrap;
  }

  .threshold-label,
  .current-price-label {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
  }

  .threshold-label {
    transform: translateY(-50%);
    padding: 2px var(--space-1);
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--surface-body) 92%, transparent);
  }

  .current-price-label {
    right: 0;
    box-sizing: border-box;
    transform: translateY(-50%);
    padding: 2px var(--space-1);
    color: var(--text-on-brand);
    text-align: right;
  }

  .current-price-label.positive {
    background: var(--color-positive);
  }

  .current-price-label.neutral {
    background: var(--color-flat);
  }

  .current-price-label.negative {
    background: var(--color-negative);
  }

  .threshold-label.positive,
  .state-annotation.positive {
    color: var(--color-positive);
  }

  .threshold-label.neutral,
  .state-annotation.neutral {
    color: var(--text-secondary);
  }

  .threshold-label.negative,
  .state-annotation.negative {
    color: var(--color-negative);
  }

  .state-annotation {
    box-sizing: border-box;
    width: calc(var(--space-8) + var(--space-8));
    padding: var(--space-1) var(--space-2);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--surface-body) 94%, transparent);
    font-size: 10px;
    font-weight: 700;
    text-align: center;
  }

  .chart-host {
    z-index: 1;
  }

  .band-layer {
    z-index: 0;
    bottom: var(--space-8);
  }

  .state-band {
    position: absolute;
    top: 0;
    bottom: 0;
    border-left: 1px solid var(--border-subtle);
  }

  .state-band.positive {
    background: color-mix(in srgb, var(--color-positive) 9%, transparent);
  }

  .state-band.neutral {
    background: color-mix(in srgb, var(--color-flat) 8%, transparent);
  }

  .state-band.negative {
    background: color-mix(in srgb, var(--color-negative) 10%, transparent);
  }

  .future-mask {
    z-index: 2;
    right: 0;
    width: auto;
    box-sizing: border-box;
    border-left: 1px solid var(--text-secondary);
    background: color-mix(in srgb, var(--surface-body) 84%, transparent);
    pointer-events: none;
    will-change: transform;
  }

  .after-close-dim {
    z-index: 2;
    visibility: hidden;
    background: color-mix(in srgb, var(--surface-body) 32%, transparent);
    pointer-events: none;
  }

  .chart-stage.after-close .after-close-dim {
    visibility: visible;
  }

  .playhead {
    position: absolute;
    z-index: 3;
    top: 0;
    bottom: var(--space-8);
    left: 0;
    width: 1px;
    background: transparent;
    pointer-events: none;
    will-change: transform;
  }

  .playhead span {
    position: absolute;
    top: var(--space-2);
    left: var(--space-2);
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    white-space: nowrap;
  }

  .playhead span strong,
  .playhead span small {
    display: block;
  }

  .playhead span strong {
    font: inherit;
  }

  .playhead span small {
    margin-top: var(--space-1);
    color: var(--text-secondary);
    font-family: var(--font-sans);
    font-size: 9px;
    font-weight: 700;
  }

  :global(.playhead.label-left) span {
    right: var(--space-2);
    left: auto;
  }

  @media (max-width: 720px) {
    .chart-head {
      align-items: flex-start;
      flex-direction: column;
    }

    .chart-tools {
      width: 100%;
      justify-content: space-between;
    }

    .chart-stage {
      min-height: 300px;
    }
  }
</style>
