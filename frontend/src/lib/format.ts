export function fmtPrice(n: number): string {
  return Math.round(n).toLocaleString('ko-KR');
}

export function fmtVol(n: number): string {
  if (n >= 1e6) {
    return (n / 1e6).toFixed(1) + 'M';
  }
  if (n >= 1e3) {
    return (n / 1e3).toFixed(1) + 'K';
  }
  return Math.round(n).toLocaleString('ko-KR');
}

export function pct(n: number, sign: boolean = true): string {
  const prefix = sign && n >= 0 ? '+' : '';
  return prefix + n.toFixed(2) + '%';
}

export function changeClass(n: number): string {
  return n >= 0 ? 'price-up' : 'price-down';
}
