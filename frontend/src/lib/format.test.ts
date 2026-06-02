import { describe, it, expect } from 'vitest';
import { fmtPrice, fmtVol, pct, changeClass } from './format';

describe('pct', () => {
  it('formats an already-percent value (backend change_rate is in percent units)', () => {
    expect(pct(1.74)).toBe('+1.74%');
    expect(pct(28.21)).toBe('+28.21%');
  });

  it('shows minus sign for negative', () => {
    expect(pct(-0.8)).toBe('-0.80%');
  });

  it('omits sign when sign=false', () => {
    expect(pct(3.1, false)).toBe('3.10%');
  });
});

describe('changeClass', () => {
  it('is price-up for >= 0 and price-down for < 0', () => {
    expect(changeClass(0)).toBe('price-up');
    expect(changeClass(1.74)).toBe('price-up');
    expect(changeClass(-0.01)).toBe('price-down');
  });
});

describe('fmtPrice / fmtVol', () => {
  it('formats price with thousands separators', () => {
    expect(fmtPrice(322500)).toBe('322,500');
  });

  it('abbreviates large volume', () => {
    expect(fmtVol(5_720_582)).toBe('5.7M');
    expect(fmtVol(16_000)).toBe('16.0K');
  });
});
