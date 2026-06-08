import { untrack } from 'svelte';
import type { Candle } from './types';
import type { CandleInterval } from './api';

export interface TimeframeSelection {
  activeTimeframe: CandleInterval;
  activeCandles: Candle[];
}

export function createTimeframeSelection(
  getSymbol: () => string,
  getCandles: () => Candle[],
): TimeframeSelection {
  let activeTimeframe = $state<CandleInterval>('5m');
  let activeCandles = $state<Candle[]>([]);
  let lastSymbol: string | undefined = undefined;

  // Reset to the 5m default ONLY when the stock symbol actually changes. The 5s
  // realtime poll re-propagates props to <Chart>; keying the reset on the symbol
  // VALUE (and reading candles untracked) means a price/snapshot poll can never
  // wipe the user's selected 일/주/월 interval — only navigating to a new stock does.
  $effect(() => {
    const symbol = getSymbol();
    if (symbol === lastSymbol) return;
    lastSymbol = symbol;
    activeTimeframe = '5m';
    activeCandles = untrack(getCandles);
  });

  return {
    get activeTimeframe() {
      return activeTimeframe;
    },
    set activeTimeframe(value: CandleInterval) {
      activeTimeframe = value;
    },
    get activeCandles() {
      return activeCandles;
    },
    set activeCandles(value: Candle[]) {
      activeCandles = value;
    },
  };
}
