// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { flushSync } from 'svelte';
import { createTimeframeSelection } from './timeframeSelection.svelte';
import type { Candle } from './types';

function candle(time: number): Candle {
  return { time, open: 1, high: 1, low: 1, close: 1 };
}

interface Store {
  symbol: string;
  candles: Candle[];
  poll: number;
}

function drive(scenario: (ctx: { store: Store; tf: ReturnType<typeof createTimeframeSelection> }) => void): void {
  const store = $state<Store>({ symbol: '005930', candles: [candle(1)], poll: 0 });
  const stop = $effect.root(() => {
    const tf = createTimeframeSelection(
      () => store.symbol,
      () => store.candles,
    );
    scenario({ store, tf });
  });
  stop();
}

describe('createTimeframeSelection', () => {
  it('keeps the user-selected interval when the price poll re-propagates props (same symbol)', () => {
    let afterSelect = '';
    let afterPoll = '';
    drive(({ store, tf }) => {
      flushSync();

      tf.activeTimeframe = '1M';
      tf.activeCandles = [candle(10), candle(11)];
      flushSync();
      afterSelect = `${tf.activeTimeframe}/${tf.activeCandles.length}`;

      store.poll = 1;
      store.candles = [candle(2)];
      flushSync();
      afterPoll = `${tf.activeTimeframe}/${tf.activeCandles.length}`;
    });

    expect(afterSelect).toBe('1M/2');
    expect(afterPoll).toBe('1M/2');
  });

  it('resets to the 5m default only when the symbol actually changes (new stock)', () => {
    let mount = '';
    let afterSymbolChange = '';
    drive(({ store, tf }) => {
      flushSync();
      mount = tf.activeTimeframe;

      tf.activeTimeframe = '1w';
      flushSync();

      store.symbol = '035720';
      store.candles = [candle(5), candle(6), candle(7)];
      flushSync();
      afterSymbolChange = `${tf.activeTimeframe}/${tf.activeCandles.length}`;
    });

    expect(mount).toBe('5m');
    expect(afterSymbolChange).toBe('5m/3');
  });
});
