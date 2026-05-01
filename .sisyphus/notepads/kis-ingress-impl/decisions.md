# Decisions

## 2026-05-01 Task: Initial Analysis
- src layout: `services/kis_ingestion/src/kis_ingestion/`
- DI: plain factory in container.py, no framework
- __init__.py: only models/__init__.py has re-exports, no empty __init__.py elsewhere
- Subscription: bootstrap fixed from env, cap default 40, configurable
- Market switching: NEW_MKOP_CLS_CODE signal based, schedule only for bootstrap
- Avro schema: file placement only at schemas/stock-ticks.avsc
- Commit strategy: grouped commits per plan specification
