# Learnings

## 2026-05-01 Task: Initial Analysis
- Root pyproject.toml has NO workspace config yet — needs `[tool.uv.workspace]` with `members = ["services/*"]`
- Existing models in `services/kis_ingestion/models/` — must move to `services/kis_ingestion/src/kis_ingestion/models/`
- ws_example.py uses `from models import ...` — must change to `from kis_ingestion.models import ...`
- TickRecord has 46 fields with `_FIELD_ORDER` and `parse_payload` — ParsedTick extends with 3 meta fields
- SubscribeMessage has `subscribe()` classmethod and `to_wire()` — used by ws_client
- constants.py has APPROVAL_URL, WS_URL, SIGN_LABELS, TR_IDS
- Design doc Section 7 has full 49-field contract table
- Python >=3.11, dependencies: pydantic, httpx, websockets, pydantic-settings
- No TickSink, no LoggingTickSink, no producer.py placeholder
- KISConfig is bootstrap input only, not core architecture component
