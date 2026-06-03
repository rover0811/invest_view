"""Korean financial item name constants for reference.financial_metrics.

Item names are itemNameKor (Korean only) as stored in the DB.
Period format: "YYYY-12" (e.g. "2025-12"). period_type: "Y" only. Unit: "천원".
"""

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

CASH_FROM_OPERATIONS = "*영업에서창출된현금흐름"

COMMON_ITEMS_BY_STMT: dict[str, list[str]] = {
    "INC": [REVENUE, OPERATING_PROFIT, NET_INCOME, EBITDA, EBIT, EPS],
    "BAL": [TOTAL_ASSETS, TOTAL_LIABILITIES, TOTAL_EQUITY],
    "CAS": [CASH_FROM_OPERATIONS],
}

PERIOD_TYPE_DEFAULT = "Y"
UNIT = "천원"
