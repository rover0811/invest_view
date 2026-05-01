APPROVAL_URL = "https://openapi.koreainvestment.com:9443/oauth2/Approval"
WS_URL = "ws://ops.koreainvestment.com:21000"

# 모의투자 시 아래 두 줄로 교체
# APPROVAL_URL = "https://openapivts.koreainvestment.com:29443/oauth2/Approval"
# WS_URL = "ws://ops.koreainvestment.com:31000"

SIGN_LABELS: dict[str, str] = {
    "1": "++(상한)",
    "2": "+(상승)",
    "3": " (보합)",
    "4": "--(하한)",
    "5": "-(하락)",
}

TR_IDS = {
    "krx_tick": "H0STCNT0",
    "nxt_tick": "H0NXCNT0",
    "krx_hoga": "H0STASP0",
    "nxt_hoga": "H0NXASP0",
}
