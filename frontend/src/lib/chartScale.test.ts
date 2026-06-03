import { describe, it, expect } from 'vitest';
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

const spec: ChartSpec = {
  chart_type: 'line',
  title: '재무 추이',
  x_label: '기간',
  y_label: '천원',
  unit: '천원',
  series: [
    { name: '영업이익', points: [{ x: '2022-12', y: 43000000 }, { x: '2024-12', y: 57000000 }] },
    { name: '당기순이익', points: [{ x: '2022-12', y: 30000000 }] },
  ],
};

describe('fmtCompact', () => {
  it('abbreviates with Korean magnitudes', () => {
    expect(fmtCompact(57000000)).toBe('5,700만');
    expect(fmtCompact(1.2e8)).toBe('1.2억');
    expect(fmtCompact(3.4e12)).toBe('3.4조');
  });
  it('keeps small values plain and handles negatives/non-finite', () => {
    expect(fmtCompact(950)).toBe('950');
    expect(fmtCompact(-1.5e8)).toBe('-1.5억');
    expect(fmtCompact(NaN)).toBe('');
  });
});

describe('buildXIndex', () => {
  it('uses the sorted union of all series x labels as columns', () => {
    const sparse: ChartSpec = {
      ...spec,
      series: [
        {
          name: 'A',
          points: [{ x: '2022-12', y: 1 }, { x: '2023-12', y: 2 }, { x: '2024-12', y: 3 }],
        },
        { name: 'B', points: [{ x: '2022-12', y: 4 }, { x: '2024-12', y: 5 }] },
      ],
    };
    const { categories, indexByLabel } = buildXIndex(sparse);
    expect(categories).toEqual(['2022-12', '2023-12', '2024-12']);
    expect(indexByLabel.get('2022-12')).toBe(0);
    expect(indexByLabel.get('2023-12')).toBe(1);
    expect(indexByLabel.get('2024-12')).toBe(2);
  });

  it("maps a sparse series' last point to the rightmost shared column, not its local index", () => {
    const sparse: ChartSpec = {
      ...spec,
      series: [
        {
          name: 'A',
          points: [{ x: '2022-12', y: 1 }, { x: '2023-12', y: 2 }, { x: '2024-12', y: 3 }],
        },
        { name: 'B', points: [{ x: '2022-12', y: 4 }, { x: '2024-12', y: 5 }] },
      ],
    };
    const { indexByLabel } = buildXIndex(sparse);
    expect(indexByLabel.get('2024-12')).toBe(2);
    expect(indexByLabel.get('2024-12')).not.toBe(1);
  });
});

describe('collectYValues', () => {
  it('gathers finite ys and optionally folds in a zero baseline', () => {
    expect(collectYValues(spec)).toEqual([43000000, 57000000, 30000000]);
    expect(collectYValues(spec, true)).toContain(0);
  });
});

describe('computeYDomain', () => {
  it('returns 0..1 for empty input', () => {
    expect(computeYDomain([])).toEqual({ min: 0, max: 1 });
  });
  it('pads a flat domain', () => {
    expect(computeYDomain([100, 100])).toEqual({ min: 80, max: 120 });
  });
  it('keeps min..max for normal data', () => {
    expect(computeYDomain([43000000, 57000000])).toEqual({ min: 43000000, max: 57000000 });
  });
  it('folds zero in when requested (bars)', () => {
    expect(computeYDomain([20, 40], true)).toEqual({ min: 0, max: 40 });
  });
});

describe('niceTicks', () => {
  it('produces ascending ticks spanning the range', () => {
    const ticks = niceTicks(43000000, 57000000, 4);
    expect(ticks[0]).toBeLessThanOrEqual(43000000);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(57000000);
    for (let i = 1; i < ticks.length; i++) expect(ticks[i]).toBeGreaterThan(ticks[i - 1]);
  });
  it('lands a tick exactly on zero when data crosses zero', () => {
    expect(niceTicks(-50, 100, 4)).toContain(0);
  });
});

describe('selectXTickIndices', () => {
  it('returns all indices when few', () => {
    expect(selectXTickIndices(4)).toEqual([0, 1, 2, 3]);
  });
  it('thins down and always includes first and last', () => {
    const idx = selectXTickIndices(20, 6);
    expect(idx.length).toBeLessThanOrEqual(6);
    expect(idx[0]).toBe(0);
    expect(idx[idx.length - 1]).toBe(19);
  });
});

describe('scaleXLine / scaleY', () => {
  it('centers a single point and spans endpoints', () => {
    expect(scaleXLine(0, 1, 0, 100)).toBe(50);
    expect(scaleXLine(0, 3, 0, 100)).toBe(0);
    expect(scaleXLine(2, 3, 0, 100)).toBe(100);
  });
  it('inverts y so max sits at the top of the plot', () => {
    expect(scaleY(10, 0, 10, 0, 100)).toBe(0);
    expect(scaleY(0, 0, 10, 0, 100)).toBe(100);
    expect(scaleY(5, 0, 10, 0, 100)).toBe(50);
  });
});
