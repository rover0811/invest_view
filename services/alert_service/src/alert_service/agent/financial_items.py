"""Korean financial item name constants for reference.financial_metrics.

Item names are itemNameKor (Korean only) as stored in the DB.
Period format: "YYYY-12" (e.g. "2025-12"). period_type: "Y" only. Unit: "천원".
"""

import re

STMT_TYPES = ("BAL", "INC", "CAS")

REVENUE = "매출액(수익)"
OPERATING_PROFIT = "영업이익"
NET_INCOME = "당기순이익"
EBITDA = "*EBITDA"
EBIT = "*EBIT"
EPS = "*주당순이익"

TOTAL_ASSETS = "자산총계"
TOTAL_LIABILITIES = "부채총계"
TOTAL_EQUITY = "자본총계"
CONTROLLING_EQUITY = "지배주주지분"
SHARES_OUTSTANDING = "발행주식수"

CASH_FROM_OPERATIONS = "*영업에서창출된현금흐름"

COMMON_ITEMS_BY_STMT: dict[str, list[str]] = {
    "INC": [REVENUE, OPERATING_PROFIT, NET_INCOME, EBITDA, EBIT, EPS],
    "BAL": [TOTAL_ASSETS, TOTAL_LIABILITIES, TOTAL_EQUITY],
    "CAS": [CASH_FROM_OPERATIONS],
}

PERIOD_TYPE_DEFAULT = "Y"
UNIT = "천원"

_SUFFIX_PARENS_RE = re.compile(r"\([^()]*\)$")
_SPACES_RE = re.compile(r"\s+")
DERIVED_METRIC_SUFFIXES = (
    "누적증감률",
    "누적증가율",
    "증가율",
    "성장률",
    "증감률",
    "변화율",
    "변동률",
    "변동",
    "변화",
    "추이",
    "지수화",
)

EXPLICIT_ITEM_ALIASES_BY_STMT: dict[str, dict[str, str]] = {
    "INC": {
        "매출": REVENUE,
        "매출액": REVENUE,
        "revenue": REVENUE,
        "영업이익": OPERATING_PROFIT,
        "당기순이익": NET_INCOME,
        "순이익": NET_INCOME,
        "ebitda": EBITDA,
        "ebit": EBIT,
        "eps": EPS,
        "주당순이익": EPS,
        "기본주당순이익": EPS,
    },
    "BAL": {
        "자산": TOTAL_ASSETS,
        "자산총계": TOTAL_ASSETS,
        "부채": TOTAL_LIABILITIES,
        "부채총계": TOTAL_LIABILITIES,
        "자본": TOTAL_EQUITY,
        "자본총계": TOTAL_EQUITY,
    },
    "CAS": {
        "영업현금흐름": CASH_FROM_OPERATIONS,
        "영업활동현금흐름": CASH_FROM_OPERATIONS,
        "영업에서창출된현금흐름": CASH_FROM_OPERATIONS,
    },
}


def _item_key(name: str) -> str:
    return _SPACES_RE.sub("", name.strip().lstrip("*").strip()).casefold()


def _generated_aliases(canonical: str) -> set[str]:
    base = canonical.strip().lstrip("*").strip()
    aliases = {canonical, base}
    suffix_removed = _SUFFIX_PARENS_RE.sub("", base).strip()
    if suffix_removed and suffix_removed != base:
        aliases.add(suffix_removed)
    return aliases


def _alias_map_for_stmt(stmt_type: str) -> dict[str, str]:
    stmt = stmt_type.upper()
    generated: dict[str, set[str]] = {}
    for canonical in COMMON_ITEMS_BY_STMT.get(stmt, []):
        for alias in _generated_aliases(canonical):
            generated.setdefault(_item_key(alias), set()).add(canonical)

    aliases = {key: next(iter(values)) for key, values in generated.items() if len(values) == 1}
    for alias, canonical in EXPLICIT_ITEM_ALIASES_BY_STMT.get(stmt, {}).items():
        aliases[_item_key(alias)] = canonical
    return aliases


def _strip_derived_suffix_words(name: str) -> str | None:
    stripped = name.strip()
    changed = False
    while stripped:
        for suffix in DERIVED_METRIC_SUFFIXES:
            if stripped.endswith(suffix):
                stripped = stripped[: -len(suffix)].strip()
                changed = True
                break
        else:
            break
    return stripped if changed and stripped else None


def resolve_item_names(stmt_type: str, item_names: list[str] | None) -> list[str] | None:
    if item_names is None:
        return None

    aliases = _alias_map_for_stmt(stmt_type)
    resolved: list[str] = []
    for raw in item_names:
        name = raw.strip()
        if not name:
            continue
        canonical = aliases.get(_item_key(name))
        if canonical is None:
            stripped_name = _strip_derived_suffix_words(name)
            if stripped_name is not None:
                canonical = aliases.get(_item_key(stripped_name))
        if canonical is None:
            canonical = name
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved
