"""Server-side AI signal headline assembly (doc 22 15.3, 15.4).

``headline_label`` from the model never contains the symbol name or the change
rate; the reader joins them here so the number always matches the live price.

Format:

- up: ``{label}{particle} {symbol_name} {rate}% 상승``
- down: ``{label}{particle} {symbol_name} {rate}% 하락``
- flat (rate rounds to 0.0): ``{label}에도 {symbol_name} 보합``

``rate`` is ``abs(change_rate)`` rounded half up to one decimal.

Particle (15.4) is chosen from the last character of the label: "로" when the
Hangul syllable has no final consonant or ends in ㄹ, otherwise "으로". A last
digit is read by its Korean name (0 영, 1 일, 2 이, 3 삼, 4 사, 5 오, 6 육,
7 칠, 8 팔, 9 구) and the same rule applies; any other character (Latin,
symbols) falls back to "로".
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_HANGUL_FIRST = 0xAC00
_HANGUL_LAST = 0xD7A3
_FINAL_CONSONANTS = 28
_FINAL_RIEUL = 8
_DIGIT_READINGS = "영일이삼사오육칠팔구"
_ONE_DECIMAL = Decimal("0.1")


def direction_particle(label: str) -> str:
    stripped = label.rstrip()
    if not stripped:
        return "로"
    last = stripped[-1]
    if last.isascii() and last.isdigit():
        last = _DIGIT_READINGS[int(last)]
    code = ord(last)
    if not _HANGUL_FIRST <= code <= _HANGUL_LAST:
        return "로"
    final = (code - _HANGUL_FIRST) % _FINAL_CONSONANTS
    return "로" if final in (0, _FINAL_RIEUL) else "으로"


def assemble_headline(label: str, symbol_name: str, change_rate: Decimal) -> str:
    label = label.rstrip()
    rate = abs(change_rate).quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP)
    if rate == 0:
        return f"{label}에도 {symbol_name} 보합"
    direction = "상승" if change_rate > 0 else "하락"
    return f"{label}{direction_particle(label)} {symbol_name} {rate}% {direction}"
