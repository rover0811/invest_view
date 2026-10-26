// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { parseHash } from './stores.svelte';

describe('parseHash', () => {
  beforeEach(() => {
    window.location.hash = '#/';
  });

  it('parses the signal replay route without changing stock routes', () => {
    window.location.hash = '#/replay/000660/2026-10-20';
    expect(parseHash()).toEqual({
      view: 'replay',
      symbol: '000660',
      date: '2026-10-20',
    });

    window.location.hash = '#/stocks/005930?tab=events';
    expect(parseHash()).toEqual({
      view: 'stock',
      code: '005930',
      tab: 'events',
      section: undefined,
    });
  });

  it('rejects malformed replay dates and missing symbols', () => {
    window.location.hash = '#/replay//2026-10-20';
    expect(parseHash()).toEqual({ view: 'home' });

    window.location.hash = '#/replay/000660/20-10-2026';
    expect(parseHash()).toEqual({ view: 'home' });

    window.location.hash = '#/replay/..%2Fadmin/2026-10-20';
    expect(parseHash()).toEqual({ view: 'home' });

    window.location.hash = '#/replay/000660/2026-99-99';
    expect(parseHash()).toEqual({ view: 'home' });
  });
});
