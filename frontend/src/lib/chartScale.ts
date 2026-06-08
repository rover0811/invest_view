import type { ChartSpec } from './types';

export interface Domain {
  min: number;
  max: number;
}

export function fmtCompact(n: number): string {
  if (!Number.isFinite(n)) return '';
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  const trim = (v: number) => {
    const oneDecimal = v.toFixed(1);
    return oneDecimal.endsWith('.0') ? oneDecimal.slice(0, -2) : oneDecimal;
  };
  if (abs >= 1e12) return `${sign}${trim(abs / 1e12)}조`;
  if (abs >= 1e8) return `${sign}${trim(abs / 1e8)}억`;
  if (abs >= 1e4) return `${sign}${Math.round(abs / 1e4).toLocaleString('ko-KR')}만`;
  return Math.round(n).toLocaleString('ko-KR');
}

export interface XIndex {
  categories: string[];
  indexByLabel: Map<string, number>;
}

// Shared columns = sorted union of all series' x labels (lexicographic is correct
// for zero-padded YYYY-MM), so a series missing a middle period stays right-aligned
// to its real column instead of shifting left by its local array index.
export function buildXIndex(spec: ChartSpec): XIndex {
  const labels = new Set<string>();
  for (const s of spec.series ?? []) {
    for (const p of s.points ?? []) {
      if (typeof p.x === 'string') labels.add(p.x);
    }
  }
  const categories = [...labels].sort();
  const indexByLabel = new Map<string, number>();
  categories.forEach((c, i) => indexByLabel.set(c, i));
  return { categories, indexByLabel };
}

export function collectYValues(spec: ChartSpec, includeZero = false): number[] {
  const out: number[] = [];
  for (const s of spec.series ?? []) {
    for (const p of s.points ?? []) {
      if (Number.isFinite(p.y)) out.push(p.y);
    }
  }
  if (includeZero && out.length) out.push(0);
  return out;
}

export function computeYDomain(values: number[], includeZero = false): Domain {
  if (!values.length) return { min: 0, max: 1 };
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (includeZero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (lo === hi) {
    if (lo === 0) return { min: 0, max: 1 };
    const pad = Math.abs(lo) * 0.2;
    return { min: lo - pad, max: hi + pad };
  }
  return { min: lo, max: hi };
}

function niceNum(range: number, round: boolean): number {
  if (range <= 0) return 1;
  const exp = Math.floor(Math.log10(range));
  const frac = range / Math.pow(10, exp);
  let nice: number;
  if (round) {
    if (frac < 1.5) nice = 1;
    else if (frac < 3) nice = 2;
    else if (frac < 7) nice = 5;
    else nice = 10;
  } else {
    if (frac <= 1) nice = 1;
    else if (frac <= 2) nice = 2;
    else if (frac <= 5) nice = 5;
    else nice = 10;
  }
  return nice * Math.pow(10, exp);
}

// "Nice" gridline ticks spanning [min,max]; first/last become the scaling domain
// so gridlines hug the plot edges. A zero-crossing domain always lands a tick on 0,
// giving line/bar charts a baseline exactly at zero.
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
    if (min === 0) return [0, 1];
    const step = Math.abs(min) / 2 || 1;
    return [min - step, min, min + step];
  }
  const range = niceNum(max - min, false);
  const step = niceNum(range / Math.max(1, count - 1), true);
  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = niceMin; v <= niceMax + step * 0.5; v += step) {
    ticks.push(Math.round(v / step) * step);
  }
  return ticks;
}

export function selectXTickIndices(count: number, maxLabels = 6): number[] {
  if (count <= 0) return [];
  if (count <= maxLabels) return Array.from({ length: count }, (_, i) => i);
  const step = (count - 1) / (maxLabels - 1);
  const seen = new Set<number>();
  for (let i = 0; i < maxLabels; i++) seen.add(Math.round(i * step));
  return [...seen].sort((a, b) => a - b);
}

export function scaleXLine(index: number, count: number, left: number, width: number): number {
  if (count <= 1) return left + width / 2;
  return left + (index / (count - 1)) * width;
}

export function scaleY(
  value: number,
  domainMin: number,
  domainMax: number,
  top: number,
  height: number,
): number {
  if (domainMax === domainMin) return top + height / 2;
  const t = (value - domainMin) / (domainMax - domainMin);
  return top + (1 - t) * height;
}
