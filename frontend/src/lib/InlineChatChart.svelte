<script lang="ts">
  import type { ChartSpec } from './types';
  import {
    buildXIndex,
    collectYValues,
    computeYDomain,
    fmtCompact,
    niceTicks,
    scaleXLine,
    scaleY,
    selectXTickIndices,
  } from './chartScale';

  let { spec }: { spec: ChartSpec } = $props();

  const PALETTE = ['#4ec9b0', '#e0a64e', '#b58cf0', '#dc2e47', '#3182f6'];
  const H = 220;
  const PAD = { top: 12, right: 16, bottom: 28, left: 58 };

  let host = $state<HTMLElement>();
  let w = $state(440);

  $effect(() => {
    if (!host) return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect.width;
      if (cw && cw > 0) w = cw;
    });
    ro.observe(host);
    return () => ro.disconnect();
  });

  interface GridLine {
    y: number;
    label: string;
    zero: boolean;
  }
  interface XTick {
    x: number;
    label: string;
    anchor: 'start' | 'middle' | 'end';
  }
  interface Dot {
    cx: number;
    cy: number;
  }
  interface LinePath {
    color: string;
    d: string;
    dots: Dot[];
  }
  interface BarRect {
    x: number;
    y: number;
    width: number;
    height: number;
    color: string;
  }

  const model = $derived.by(() => {
    const isBar = spec.chart_type === 'bar';
    const series = spec.series ?? [];
    const { categories: cats, indexByLabel } = buildXIndex(spec);
    const hasData = series.some((s) => (s.points?.length ?? 0) > 0);

    const plotLeft = PAD.left;
    const plotTop = PAD.top;
    const plotWidth = Math.max(0, w - PAD.left - PAD.right);
    const plotHeight = H - PAD.top - PAD.bottom;

    const yValues = collectYValues(spec, isBar);
    const rawDomain = computeYDomain(yValues, isBar);
    const ticks = niceTicks(rawDomain.min, rawDomain.max, 4);
    const domainMin = ticks[0];
    const domainMax = ticks[ticks.length - 1];

    const gridlines: GridLine[] = ticks.map((t) => ({
      y: scaleY(t, domainMin, domainMax, plotTop, plotHeight),
      label: fmtCompact(t),
      zero: t === 0,
    }));

    const count = cats.length;
    const xticks: XTick[] = selectXTickIndices(count).map((i) => {
      const cx = isBar
        ? plotLeft + (i + 0.5) * (count > 0 ? plotWidth / count : plotWidth)
        : scaleXLine(i, count, plotLeft, plotWidth);
      const anchor: XTick['anchor'] = i === 0 ? 'start' : i === count - 1 ? 'end' : 'middle';
      return { x: cx, label: cats[i] ?? '', anchor };
    });

    const lines: LinePath[] = [];
    const bars: BarRect[] = [];

    if (isBar) {
      const bandWidth = count > 0 ? plotWidth / count : plotWidth;
      const groupWidth = bandWidth * 0.7;
      const seriesCount = Math.max(1, series.length);
      const barWidth = groupWidth / seriesCount;
      const baseline = scaleY(0, domainMin, domainMax, plotTop, plotHeight);
      series.forEach((s, j) => {
        (s.points ?? []).forEach((p) => {
          if (!Number.isFinite(p.y)) return;
          const col = indexByLabel.get(p.x);
          if (col === undefined) return;
          const bandLeft = plotLeft + col * bandWidth + (bandWidth - groupWidth) / 2;
          const x = bandLeft + j * barWidth;
          const yv = scaleY(p.y, domainMin, domainMax, plotTop, plotHeight);
          bars.push({
            x,
            y: Math.min(baseline, yv),
            width: Math.max(1, barWidth - 1.5),
            height: Math.max(0.5, Math.abs(baseline - yv)),
            color: PALETTE[j % PALETTE.length],
          });
        });
      });
    } else {
      series.forEach((s, j) => {
        const cols: { col: number; cy: number }[] = [];
        (s.points ?? []).forEach((p) => {
          if (!Number.isFinite(p.y)) return;
          const col = indexByLabel.get(p.x);
          if (col === undefined) return;
          cols.push({ col, cy: scaleY(p.y, domainMin, domainMax, plotTop, plotHeight) });
        });
        cols.sort((a, b) => a.col - b.col);
        const dots: Dot[] = [];
        let d = '';
        for (const { col, cy } of cols) {
          const cx = scaleXLine(col, count, plotLeft, plotWidth);
          d += `${d ? 'L' : 'M'}${cx.toFixed(1)} ${cy.toFixed(1)} `;
          dots.push({ cx, cy });
        }
        if (d) lines.push({ color: PALETTE[j % PALETTE.length], d: d.trim(), dots });
      });
    }

    const legend = series.map((s, j) => ({
      name: s.name,
      color: PALETTE[j % PALETTE.length],
    }));

    return {
      isBar,
      hasData,
      plotLeft,
      plotTop,
      plotWidth,
      plotHeight,
      axisBottom: plotTop + plotHeight,
      gridlines,
      xticks,
      lines,
      bars,
      legend,
    };
  });
</script>

<figure class="icc" bind:this={host}>
  <figcaption class="icc-head">
    <span class="icc-title">{spec.title}</span>
    {#if spec.unit}<span class="icc-unit">단위: {spec.unit}</span>{/if}
  </figcaption>

  {#if model.legend.length > 0}
    <div class="icc-legend">
      {#each model.legend as item}
        <span class="icc-legend-item">
          <span class="icc-swatch" style="background:{item.color}"></span>
          {item.name}
        </span>
      {/each}
    </div>
  {/if}

  {#if model.hasData}
    <svg
      class="icc-svg"
      viewBox="0 0 {w} {H}"
      width="100%"
      height={H}
      role="img"
      aria-label={spec.title}
    >
      {#each model.gridlines as g}
        <line
          class="icc-grid"
          class:icc-grid-zero={g.zero}
          x1={model.plotLeft}
          y1={g.y}
          x2={model.plotLeft + model.plotWidth}
          y2={g.y}
        />
        <text class="icc-ytick" x={model.plotLeft - 8} y={g.y + 3} text-anchor="end">{g.label}</text>
      {/each}

      <line
        class="icc-axis"
        x1={model.plotLeft}
        y1={model.plotTop}
        x2={model.plotLeft}
        y2={model.axisBottom}
      />
      <line
        class="icc-axis"
        x1={model.plotLeft}
        y1={model.axisBottom}
        x2={model.plotLeft + model.plotWidth}
        y2={model.axisBottom}
      />

      {#if model.isBar}
        {#each model.bars as b}
          <rect x={b.x} y={b.y} width={b.width} height={b.height} fill={b.color} rx="1.5" />
        {/each}
      {:else}
        {#each model.lines as ln}
          <path d={ln.d} fill="none" stroke={ln.color} stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
          {#each ln.dots as dot}
            <circle cx={dot.cx} cy={dot.cy} r="2.5" fill={ln.color} />
          {/each}
        {/each}
      {/if}

      {#each model.xticks as t}
        <text class="icc-xtick" x={t.x} y={model.axisBottom + 16} text-anchor={t.anchor}>{t.label}</text>
      {/each}
    </svg>
  {:else}
    <div class="icc-empty">데이터 없음</div>
  {/if}

  {#if spec.x_label}<div class="icc-xlabel">{spec.x_label}</div>{/if}
</figure>

<style>
  .icc {
    margin: 8px 0 2px;
    padding: 10px 12px 8px;
    background: var(--surface-floor);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
  }
  .icc-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: 6px;
  }
  .icc-title {
    font-size: 12px;
    font-weight: 700;
    color: var(--text-primary);
  }
  .icc-unit {
    flex-shrink: 0;
    font-size: 10px;
    color: var(--text-tertiary);
  }
  .icc-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    margin-bottom: 4px;
  }
  .icc-legend-item {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    color: var(--text-secondary);
  }
  .icc-swatch {
    width: 10px;
    height: 3px;
    border-radius: 2px;
  }
  .icc-svg {
    display: block;
    font-family: var(--font-sans);
  }
  .icc-grid {
    stroke: var(--border-subtle);
    stroke-width: 1;
  }
  .icc-grid-zero {
    stroke: var(--border-strong);
  }
  .icc-axis {
    stroke: var(--border-strong);
    stroke-width: 1;
  }
  .icc-ytick {
    fill: var(--text-tertiary);
    font-size: 10px;
  }
  .icc-xtick {
    fill: var(--text-tertiary);
    font-size: 10px;
  }
  .icc-xlabel {
    margin-top: 2px;
    text-align: center;
    font-size: 10px;
    color: var(--text-tertiary);
  }
  .icc-empty {
    height: 80px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    color: var(--text-tertiary);
  }
</style>
