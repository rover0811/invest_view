
## Infrastructure Setup (Wave 0)
- **Docker Compose Services**:
  - `kafka`: Confluent Kafka 7.9.0 (KRaft mode)
  - `schema-registry`: Confluent Schema Registry 7.9.0 (URL: http://localhost:8081)
  - `postgres`: PostgreSQL 16-alpine (Port: 5432, DB: invest_view, User/Pass: postgres/postgres)
- **Internal Hostnames**:
  - Kafka: `kafka:29092`
  - Schema Registry: `schema-registry:8081`
  - Postgres: `postgres:5432`
- **Environment Variables**:
  - `SCHEMA_REGISTRY_URL=http://localhost:8081`
  - `DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/invest_view`

## Avro Schema Design (Task 0.5)
- **Schema File**: `schemas/stock-alerts.avsc`
- **Namespace**: `com.invest_view.events`
- **Record Name**: `StockAlert`
- **Key Design Choices**:
  - Used Avro **enums** for `market`, `alert_type`, and `severity` to enforce strict validation at the producer level.
  - Used `logicalType: timestamp-millis` for all timestamp fields (`observation_start_at`, `observation_end_at`, `triggered_at`) to ensure consistent time representation across Flink and Python services.
  - `trigger_values` is implemented as a flat `map<string, string>` to allow flexible metadata storage without complex nested schemas.
  - `source_tick_event_id` is a nullable union to support alerts that might not be directly tied to a single tick (e.g., time-based or aggregate alerts).
- **Verification**:
  - Successfully parsed with `fastavro.parse_schema`.
  - Round-trip test confirmed that `datetime` objects are correctly serialized to `long` and deserialized back to `datetime`.
  - Enum violation test confirmed that invalid enum symbols (e.g., 'UNKNOWN') raise a `ValueError` during serialization.

## Schema Registry Registration (Task 0.6)
- **Registration Script**: `scripts/register_schemas.py`
  - CLI flags: `--registry-url` (default `$SCHEMA_REGISTRY_URL` or `http://localhost:8081`), `--subject` (required), `--schema-file` (required)
  - Uses `confluent_kafka.schema_registry.SchemaRegistryClient` + `Schema(schema_str, schema_type="AVRO")`
  - Performs optional `client.test_compatibility(subject, schema)` when the subject already exists (logs PASS/FAIL, aborts on FAIL before register)
  - Exits 1 on `SchemaRegistryError`
- **Bash Wrapper**: `scripts/register_all_schemas.sh` (chmod +x, `set -euo pipefail`)
  - Registers `stock-ticks-value` -> `schemas/stock-ticks.avsc` and `stock-alerts-value` -> `schemas/stock-alerts.avsc`
  - Reads `SCHEMA_REGISTRY_URL` env var (default `http://localhost:8081`)
- **How to run from repo root**:
  - `bash scripts/register_all_schemas.sh`
  - The wrapper cd's into `services/kis_ingestion` and invokes `uv run python` so it uses that venv's `confluent-kafka[schemaregistry]` install.
- **Dependency Quirk (IMPORTANT)**:
  - Base `confluent-kafka` is NOT enough for `confluent_kafka.schema_registry` -- it imports `authlib` and other extras lazily at module load time. Without extras, you get `ModuleNotFoundError: No module named 'authlib'`.
  - **Fix**: pinned `confluent-kafka[avro,schemaregistry]>=2.3` in `services/kis_ingestion/pyproject.toml`. `uv sync` then pulls `authlib`, `avro`, `cachetools`, `cryptography`, `joserfc`, etc.
  - This also satisfies the prerequisite for T0.7 (kis_ingestion producer migration to Avro + SR).
- **Subject Naming**: Confluent TopicNameStrategy => `<topic>-value` (and `<topic>-key` if keyed). Tasks here use value subjects only.
- **Idempotency Verified**: running `register_all_schemas.sh` twice in a row returns identical schema IDs:
  - `stock-ticks-value` -> id 1 (both runs)
  - `stock-alerts-value` -> id 2 (both runs)
  - Compatibility check logs `PASS` on the second run since the schema is byte-identical to the registered version.
- **Compatibility Default**: BACKWARD (Confluent SR default). NOT changed to NONE -- the task explicitly forbids weakening compatibility.

## Producer Migration to Schema Registry (Task 0.7)
- **File**: `services/kis_ingestion/src/kis_ingestion/producer.py`
- **Approach**: Used plain `confluent_kafka.Producer` (NOT `SerializingProducer` — experimental per Confluent docs) plus `AvroSerializer` instantiated directly. The serializer is called explicitly in `publish()` with `SerializationContext(topic, MessageField.VALUE)`; result bytes are passed as `value=` to `producer.produce()`.
- **Why not SerializingProducer**: confluent-kafka docs flag it as experimental; the supported pattern is explicit `serializer(value, ctx)` + plain `Producer.produce()`.
- **Typing — Protocol shim pattern preserved**:
  - The existing file used `cast + import_module + Protocol` to handle the absence of type stubs for `confluent_kafka.*`. Continued the same pattern for the new imports (`SchemaRegistryClient`, `AvroSerializer`, `SerializationContext`, `MessageField`) to keep the file consistent and silence basedpyright `reportMissingImports`.
  - All four new symbols are bound at module top-level (via `cast(..., getattr(import_module(...), "Name"))`), which keeps `patch("kis_ingestion.producer.X")` working in tests.
- **Producer config additions (NFR-02/03)**:
  - `acks=all` — wait for all in-sync replicas
  - `enable.idempotence=true` — exactly-once producer semantics (deduplication + retry safety)
  - `auto.register.schemas=False` on the AvroSerializer `conf` — CI/operator pre-registers via `scripts/register_all_schemas.sh`; producer NEVER auto-registers.
- **Constructor signature**: `(bootstrap_servers, topic, schema_path, schema_registry_url)` — SR URL is now a required arg; container.py passes `config.schema_registry_url` (added to `KISConfig` as `schema_registry_url: str = "http://localhost:8081"`).
- **Wire format verified end-to-end against running SR**:
  - Real serializer output: magic byte `0x00`, schema ID `1` (4 bytes big-endian), Avro body. Confirms TopicNameStrategy resolution + ID-based downstream deserialization will work.
  - Evidence: `.sisyphus/evidence/task-0_7-wire-format.txt`
- **Test patching strategy**:
  - `patch("kis_ingestion.producer.Producer")` — replaces `Producer` callable; assert `acks=all`, `enable.idempotence=True` via `call_args.args[0]` (positional config dict).
  - `patch("kis_ingestion.producer.SchemaRegistryClient")` — replaces SR client constructor; assert it was called with `{"url": SR_URL}`.
  - `patch("kis_ingestion.producer.AvroSerializer")` — replaces serializer constructor; the returned mock is callable. Set `mock_serializer.return_value = b"..."` to fake serialization bytes; inspect `mock_serializer.call_args.args[0]` to verify the dict and `.args[1]` to verify `SerializationContext.topic` / `.field`.
  - Combined into one `mock_kafka_deps` pytest fixture to reduce per-test boilerplate (3 `with patch(...)` blocks were noisy).
- **Test simplification (avro_roundtrip)**: Real round-trip test removed (would require SR running for unit test); replaced with assertion that `AvroSerializer` instance was called with the correct `(tick_dict, SerializationContext)` and that the dict carries the expected `symbol` and `price`. End-to-end wire format separately verified by the integration script (above).
- **Files in this commit**: producer.py, config.py, container.py, tests/test_producer.py, evidence/*. pyproject.toml unchanged (already upgraded in T0.6 to `confluent-kafka[avro,schemaregistry]>=2.3`).
## Alert Service Scaffolding (T1)
- Created `services/alert_service/` as a uv workspace member.
- Package name: `alert-service`, Module name: `alert_service`.
- Using namespace packages (no `__init__.py` in `src/alert_service` or `tests`).
- Verified with `uv run --package alert-service python -c "print('ok')"`.
- Pytest collection verified (0 tests collected as expected).
- Removed obsolete `services/alert_serving/` directory and updated root `pyproject.toml` exclude list.

## Alert Service Alembic Setup (T3)
- **Alembic Configuration**:
  - `script_location = alembic`
  - `prepend_sys_path = .`
  - `sqlalchemy.url` is dynamically overridden in `env.py` via `ALERT_SERVICE_DATABASE_URL` or `DATABASE_URL` environment variables.
- **Async Migration Support**:
  - Implemented async migration runner in `env.py` using `async_engine_from_config` and `connection.run_sync(do_run_migrations)`.
  - This follows the standard SQLAlchemy/Alembic async cookbook pattern.
- **Schema Design**:
  - Created a dedicated `alert_service` schema to isolate service tables from the `public` schema.
  - **Tables**:
    - `users`: Minimal user profile (UUID PK, nickname, created_at).
    - `watchlist_items`: Composite PK on `(user_id, symbol)`. No surrogate ID.
    - `alert_events`: Stores triggered alerts. Includes CHECK constraints for `market` (KRX, NXT), `alert_type` (PRICE_ALERT, VI_IMMINENT, MOMENTUM_SHIFT, TRADING_HALT), and `severity` (INFO, WARNING, CRITICAL).
    - `notification_events`: Tracks delivery of alerts to users. Includes CHECK constraints for `delivery_status` (PENDING, SENT, FAILED) and `failure_reason` (no_connection, send_error).
- **Key Alembic Settings**:
  - `include_schemas=True` in `context.configure` is CRITICAL for managing the non-public `alert_service` schema.
  - `target_metadata = None` for now (T6 will update this once SQLAlchemy models are defined).
- **Verification**:
  - Successfully ran `upgrade head` and `downgrade base` against the local PostgreSQL container.
  - Verified table structures, indexes, and CHECK constraints via `psql`.
  - Evidence recorded in `.sisyphus/evidence/task-3-alembic-schema.txt` and `.sisyphus/evidence/task-3-alembic-downgrade.txt`.

## Alert Service Configuration (T4)
- **Pydantic Settings**:
  - Implemented `AlertServiceConfig` using `pydantic-settings`.
  - Used `env_prefix="ALERT_SERVICE_"` to isolate environment variables.
  - Set `env_file=None` to avoid automatic `.env` loading, favoring explicit environment variables.
- **Required Fields**:
  - `database_url`, `kafka_bootstrap_servers`, `jwt_secret`, and `schema_registry_url` are required (no defaults).
- **Verification**:
  - Unit tests in `tests/test_config.py` verify that missing required fields raise `ValidationError`.
  - Verified that environment variables correctly override defaults (e.g., `ALERT_SERVICE_KAFKA_TOPIC`).
  - Tests run with `PYTHONPATH=src` to ensure the `alert_service` package is discoverable.
  - Evidence recorded in `.sisyphus/evidence/task-4-config-load.txt` and `.sisyphus/evidence/task-4-config-missing.txt`.

## JWT Verification (Task 5)
- **Library**: PyJWT 2.x.
- **Implementation**: `JWTVerifier` class in `alert_service.auth.jwt`.
- **Key Design Choices**:
  - HS256 algorithm only (symmetric key).
  - `jwt.decode` requires `algorithms` as a list (e.g., `algorithms=["HS256"]`).
  - `ExpiredSignatureError` is caught separately from `InvalidTokenError` to provide more specific error messages, although it is a subclass of the latter.
  - `user_id_claim` is configurable (e.g., `sub` or `user_id`).
  - Resulting `user_id` is coerced to `str` to ensure consistency.
- **Testing**:
  - 6 unit tests covering: valid token, expired token, bad signature, missing claim, custom claim, and malformed token.
  - Tests use `PYTHONPATH=src` to correctly import the `alert_service` package when running from the service root.
- **Verification**:
  - All 6 tests passed.
  - Evidence: `.sisyphus/evidence/task-5-jwt-valid.txt`.

## Alert Service Configuration (T4)
- **Pydantic Settings**:
  - Implemented `AlertServiceConfig` using `pydantic-settings`.
  - Used `env_prefix="ALERT_SERVICE_"` to isolate environment variables.
  - Set `env_file=None` to avoid automatic `.env` loading, favoring explicit environment variables.
- **Required Fields**:
  - `database_url`, `kafka_bootstrap_servers`, `jwt_secret`, and `schema_registry_url` are required (no defaults).
- **Verification**:
  - Unit tests in `tests/test_config.py` verify that missing required fields raise `ValidationError`.
  - Verified that environment variables correctly override defaults (e.g., `ALERT_SERVICE_KAFKA_TOPIC`).
  - Tests run with `PYTHONPATH=src` to ensure the `alert_service` package is discoverable.
  - Evidence recorded in `.sisyphus/evidence/task-4-config-load.txt` and `.sisyphus/evidence/task-4-config-missing.txt`.

## Alert Service DB Models & Session (T6)
- **SQLAlchemy 2.x ORM**:
  - Implemented models using `DeclarativeBase`, `Mapped[...]`, and `mapped_column(...)`.
  - All tables explicitly use `{"schema": "alert_service"}` in `__table_args__` to match the Alembic migration.
  - **Tables**: `User`, `WatchlistItem`, `AlertEvent`, `NotificationEvent`.
  - **Constraints**: Mirrored all CHECK constraints from Alembic (market, alert_type, severity, delivery_status, failure_reason).
  - **WatchlistItem**: Uses a composite primary key on `(user_id, symbol)`.
- **Async Session Factory**:
  - Created `create_engine` and `create_session_factory` in `alert_service.db.session`.
  - Uses `create_async_engine` with `future=True` and `async_sessionmaker` with `AsyncSession`.
- **Alembic Integration**:
  - Updated `alembic/env.py` to set `target_metadata = Base.metadata`.
  - Added `sys.path` manipulation in `env.py` to ensure the `alert_service` package is importable during migrations.
- **Verification**:
  - Unit tests in `tests/test_db_models.py` verify metadata, composite PKs, and CHECK constraints without requiring a live DB.
  - Verified `alembic upgrade head` runs successfully with the new `target_metadata` linkage.
  - Evidence: `.sisyphus/evidence/task-6-orm-models.txt`.

## WebSocket Connection Registry (T8)
- **Implementation**: `ConnectionRegistry` in `alert_service.ws.registry`.
- **Storage**: In-memory `dict[str, set[WebSocketLike]]`.
- **Key Design Choices**:
  - Used `typing.Protocol` (`WebSocketLike`) to decouple the registry from FastAPI's `WebSocket` class, making it easier to test with fakes.
  - `send_to_user` handles broadcasting to multiple active connections for a single user.
  - Automatic cleanup: connections that raise exceptions during `send_json` are automatically removed from the registry.
  - Single-instance only: the registry is in-memory and does not support multi-instance scaling (e.g., via Redis pub/sub) in v1.
- **Verification**:
  - 9 unit tests covering: basic add/remove, multiple connections per user, no-op removals for unknown users/connections, and broadcast/failure handling in `send_to_user`.
  - Evidence: `.sisyphus/evidence/task-8-registry-tests.txt`.

## Alert Consumer (T7)
- **Files**: `kafka/__init__.py`, `kafka/consumer.py`, `tests/test_kafka_consumer.py`
- **Architecture**: plain `confluent_kafka.Consumer` (NOT `DeserializingConsumer` — experimental, same reasoning as T0.7 producer) + explicit `AvroDeserializer(...)` call in poll loop.
- **Threading model**:
  - `consumer.poll(timeout)` is BLOCKING/sync; run via `loop.run_in_executor(None, ...)` so the event loop stays responsive.
  - A bounded `asyncio.Queue(maxsize=1000)` decouples the sync poll thread from the async `on_message` dispatch task. Full queue → `await queue.put()` blocks the poll loop → natural backpressure to Kafka.
  - `_run_dispatch_loop` exit condition `while not stop_event.is_set() OR not queue.empty()` — drains the queue before exiting on shutdown.
- **Commit semantics**:
  - `enable.auto.commit=False`, `isolation.level=read_committed` (consumer config).
  - On `on_message` SUCCESS → manual `consumer.commit(message=msg, asynchronous=False)`.
  - On `on_message` FAILURE → DO NOT commit; will be redelivered on next session (alert events are idempotent per `event_id` so re-delivery is safe).
  - On deserialization failure (SerializationError, non-dict, tombstone) → commit + skip (no DLQ per spec).
- **Schema Registry**:
  - `AvroDeserializer(sr_client, schema_str, from_dict=lambda obj, ctx: obj)` — `from_dict` short-circuits Avro→Python class conversion and yields a plain dict.
  - Schema file path comes from `AlertServiceConfig.avro_schema_path` (default `schemas/stock-alerts.avsc`); SR URL from `config.schema_registry_url`.
  - Pre-registered by T0.6 (id=2); consumer does NOT auto-register.
- **Test strategy** (pure unit, no docker):
  - Fixture `consumer_patches` patches `Consumer`, `SchemaRegistryClient`, `AvroDeserializer`, `Path` at the `alert_service.kafka.consumer` module level.
  - For poll loop tests, drive iterations via a `side_effect` on `consumer.poll` that flips `stop_event` after N calls — `await consumer._run_poll_loop()` runs the real coroutine end-to-end, no `asyncio.wait_for` racing tricks.
  - For dispatch tests, put items directly on `consumer._queue` then pre-set `stop_event` — the loop condition `not stop_event.is_set() OR not queue.empty()` guarantees the message gets processed before exit.
  - `consumer._stop_event.set()` is called from inside a `run_in_executor` thread in some tests — safe because `asyncio.Event.set()` only mutates an internal flag and walks `_waiters` (empty in this code path — we only call `is_set()`, never `wait()`).
  - 7 tests, all pass: subscribe, none-msg no-op, partition-eof no-op, serialization-error commit+skip, dispatch success commit, dispatch handler-raise no-commit, stop event+close.
- **LSP / type-stub note**:
  - `confluent_kafka` ships no type stubs, so direct `from confluent_kafka import ...` triggers basedpyright `reportMissingImports` (4 errors) and cascading `reportUnknownXxx` warnings.
  - T0.7 producer.py worked around this with a `cast + import_module + Protocol` shim and is LSP-clean.
  - This task's spec mandates the simpler direct-import shape (matches the spec literally). Tests pass at runtime; LSP errors are spurious (no type stubs upstream). If a future task wants the producer's clean LSP profile, refactor consumer.py to the Protocol shim pattern — patch targets (`alert_service.kafka.consumer.Consumer`, `.AvroDeserializer`, etc.) stay the same so tests don't need to change.
- **`stop()` is sync** by design — designed to be wrapped in `anyio.to_thread.run_sync` by the caller (e.g., FastAPI shutdown handler). Sets `_stop_event` and calls `consumer.close()`. Background tasks see the event flip on their next iteration and exit cleanly.
- **`start()` log line oddity**: includes `self._consumer.list_topics(timeout=2.0)` in the log format args — this is a 2s metadata RPC on startup. Acceptable cost for a once-per-process operation; mocked in tests as a return-value MagicMock.

## Repository Layer (T9)
- **Files**: `repository/__init__.py`, `repository/users.py`, `repository/watchlist.py`, `repository/alert_events.py`, `repository/notifications.py`
- **Session ownership**: Each repository takes `session_factory: async_sessionmaker[AsyncSession]` and opens/commits sessions internally per-method. No transaction is ever held across method boundaries — each repo method is its own unit of work. Trade-off documented: callers cannot compose multi-repo atomic transactions in v1; if/when that's needed, refactor to a UoW pattern or pass session arg.
- **Idempotent upserts**:
  - `AlertEventRepository.upsert`: `pg_insert(AlertEvent).on_conflict_do_nothing(index_elements=["alert_event_id"]).returning(AlertEvent.alert_event_id)`. Returns `True` if new row inserted, `False` if duplicate (RETURNING is empty on conflict-do-nothing → `scalar_one_or_none()` is None).
  - `NotificationRepository.bulk_create_pending`: same `on_conflict_do_nothing` + `returning(notification_id)` pattern but using the unique constraint `notification_events_user_alert_uq` on `(user_id, alert_event_id)`. Returns the list of NEWLY inserted notification_ids only (replay-safe).
- **Watchlist duplicate handling**: SQLAlchemy `IntegrityError` (PK collision on composite `(user_id, symbol)`) is caught, `await session.rollback()` is called, and a domain-specific `WatchlistDuplicateError` is raised. Catching at the repo boundary keeps SQL exceptions from leaking to upper layers.
- **DELETE/UPDATE rowcount check**: `result.rowcount or 0` — asyncpg sometimes reports `None` instead of 0 for no-op DELETEs, so the `or 0` guard is required to make the boolean return contract deterministic.
- **testcontainers pattern**:
  - `postgres_container` fixture is session-scoped (sync fixture, started once, stopped at session end).
  - `db_engine` is function-scoped async fixture — creates schema + tables per-test, drops them after. Slightly slower than truncate-between-tests but guarantees full isolation.
  - URL transform: `postgres_container.get_connection_url()` returns `postgresql+psycopg2://...`; replace both `postgresql+psycopg2` and `postgresql://` prefixes with `postgresql+asyncpg://` to get an async-compatible URL.
  - `await conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS alert_service")` BEFORE `Base.metadata.create_all` — SQLAlchemy will not create the schema itself for explicit `{"schema": "..."}` table args.
- **Timezone gotcha (CRITICAL)**:
  - T6 models declare timestamp columns as plain `Mapped[datetime]` without `timezone=True` → SQLAlchemy maps them to PostgreSQL `TIMESTAMP WITHOUT TIME ZONE`.
  - asyncpg refuses to insert `datetime.now(timezone.utc)` (tz-aware) into TIMESTAMP-without-TZ columns with `DataError: can't subtract offset-naive and offset-aware datetimes`.
  - Fix in tests: `datetime.now(timezone.utc).replace(tzinfo=None)` — keep "logical UTC" but strip the tzinfo before passing to repository methods.
  - Future-proofing: when T10/T11 build pusher/routes, ensure they strip tz before persistence, or refactor T6 models to use `Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)` with explicit `TIMESTAMP(timezone=True)` if tz-aware storage is desired.
- **Verification**:
  - QA: 9/9 passed against testcontainers postgres:16-alpine (2.27s including container startup ≈ 5s on subsequent runs, ~30s first run for image pull).
  - Unit: 30/30 passed under `-m "not qa"` (existing test suite intact).
  - Evidence: `.sisyphus/evidence/task-9-repository-tests.txt`, `task-9-alert-upsert.txt`, `task-9-watchlist-filter.txt`, `task-9-notifications-pagination.txt`.

## FastAPI App, Routes & WebSocket (T11)
- **Files**: `api/__init__.py`, `api/app.py`, `api/deps.py`, `api/heartbeat.py`, `api/routes/{health,watchlist,notifications,ws}.py`, `api/schemas/{watchlist,notification}.py`, `tests/test_api.py`.
- **Container injection pattern**: `create_app(container)` attaches the wired objects (jwt_verifier, connection_registry, watchlist_repo, notification_repo, engine) onto `app.state.*`. Routes read them via `request.app.state.xxx` / `websocket.app.state.xxx`. This keeps the ASGI app importable without prematurely instantiating the heavy container (Kafka, Postgres engine) — T12 does the wiring.
- **`current_user_id` dependency**: accepts both `Authorization: Bearer ...` header and `?token=...` query (for cases like SSE/initial-load); returns `uuid.UUID`. UUID parse failure surfaces as 401 (not 500). The dependency raises `HTTPException(401)` rather than returning sentinel values so FastAPI handles the response envelope.
- **WebSocket auth pattern**:
  - JWT verification BEFORE `websocket.accept()` — `await websocket.close(code=1008)` works in CONNECTING state (Starlette sends a 403/close frame without ever accepting).
  - On valid token, `accept()` then `registry.add(user_id, websocket)`.
  - Server-push only: the receive loop calls `receive_text()` but discards content; its only purpose is detecting `WebSocketDisconnect`. We don't process inbound messages in v1.
  - `try/finally registry.remove` ensures cleanup on any exit path (normal disconnect, exception, server shutdown).
- **Heartbeat loop**:
  - Starlette doesn't send WS ping/pong frames automatically — application-level `{"type": "ping"}` JSON heartbeat every 25s detects dead connections.
  - `registry.send_to_user` already auto-removes connections that raise on send (from T8), so heartbeat is purely a liveness probe; no extra dead-conn pruning needed here.
  - Snapshot user_ids via `list(registry._by_user.keys())` before iterating — avoids "dict changed size during iteration" if a connection is added/removed mid-loop.
- **Lifespan ordering on shutdown**: cancel heartbeat → `anyio.to_thread.run_sync(consumer.stop)` (consumer.stop is sync by T7 design) → cancel consumer task → `await engine.dispose()`. Each step wrapped in try/except so a single failure doesn't skip the rest. `engine.dispose()` LAST so in-flight repo requests can finish during the previous teardown steps.
- **Pydantic ORM->API**: routes use `WatchlistItemOut.model_validate(item, from_attributes=True)` to convert SQLAlchemy rows to response models. `from_attributes=True` (pydantic v2 replacement for v1's `orm_mode`) is required since the DB rows are objects-with-attributes, not dicts.
- **Symbol validation**: regex `^[A-Z0-9]{6}$` enforced via Pydantic `@field_validator`. Length is also enforced by `Field(min_length=6, max_length=6)` which gives a cleaner 422 message for length-only violations. Tested via `test_watchlist_post_invalid_symbol_format`.
- **Watchlist PATCH return value**: after `set_notifications_enabled`, we re-fetch via `list_for_user` to return the updated row (the repo's UPDATE returns rowcount, not the row itself). 404 if the row vanished between the two calls (race-condition belt-and-suspenders).
- **PATCH response_model**: `WatchlistItemOut | None` — FastAPI accepts unions in response_model.
- **TestClient WebSocket gotchas**:
  - Without `?token=` query: FastAPI's `Query(...)` raises 422 internally; TestClient surfaces this as an exception inside `websocket_connect()`. `pytest.raises(Exception)` is the right catch since the exact exception type varies by FastAPI version (WebSocketDisconnect / httpx.HTTPStatusError / similar).
  - Invalid token: server sends close with code 1008. TestClient may or may not raise depending on whether the close frame arrives before the with-block setup completes. `try/except Exception` accommodates both.
  - Valid token: `registry.add` must be called once during the accept handshake, `registry.remove` once on context exit. Asserted via MagicMock counts (since FakeContainer.connection_registry is mocked, the real T8 registry isn't exercised here — that's T13's integration job).
- **FakeContainer test fixture pattern**: a pure-mock container that exposes the same attribute surface as the real T12 container. All async methods are `AsyncMock(...)`, sync ones are `MagicMock()`, and `engine.dispose` is async (`AsyncMock()`) to satisfy lifespan teardown. Reusing this in T12/T13 unit tests lets us validate the wiring without spinning up Kafka/Postgres.
- **LSP diagnostics on api/**: 8 spurious basedpyright errors (`reportMissingImports` for fastapi, `reportAttributeAccessIssue` for `anyio.to_thread`). Same root cause as the kis_ingestion confluent-kafka issue: LSP can't resolve packages installed in the alert-service uv venv (workspace member). Verified at runtime: `import fastapi` works, `anyio.to_thread.run_sync` is callable. Tests run from the alert-service venv and pass. No code change needed; document and move on.
- **Verification**:
  - 13/13 tests in `tests/test_api.py` pass (`uv run --package alert-service pytest tests/test_api.py -v`).
  - 43/43 tests pass in the full alert-service unit suite (`-m "not qa"`).
  - Evidence: `.sisyphus/evidence/task-11-api-tests.txt`.
- AlertPusher unit tests implemented with 100% coverage of core logic (duplicate handling, fanout, connection registry integration).

## Container and Entrypoint Implementation
- Wired all components using a plain `Container` class, following the factory pattern.
- Created `__main__.py` as the entrypoint for `uv run python -m alert_service`.
- Discovered that `AlertConsumer` requires a valid Avro schema path during initialization, which needs to be handled in tests by pointing to the workspace root `schemas/` directory.
- Verified that the DI graph builds correctly and the entrypoint imports without issues.

## Alert Serving Design Learnings

- **Single Instance Trade-off**: For v1, keeping the service single-instance simplifies WebSocket session management significantly by avoiding the need for a distributed registry (e.g., Redis Pub/Sub).
- **Delivery Status Tracking**: Tracking delivery status (PENDING, SENT, FAILED) in the database allows for reliable recovery and auditing of alerts.
- **Avro Logical Types**: Using  logical type in Avro ensures consistent time representation across Kafka and the Python service.
- **Idempotency**: Using a unique constraint on  in the  table ensures that users don't receive duplicate notifications for the same alert event.
- **Backpressure**: Implementing a bounded queue in the Kafka consumer prevents the service from being overwhelmed by a burst of alerts.

## Alert Serving Design Learnings

- **Single Instance Trade-off**: For v1, keeping the service single-instance simplifies WebSocket session management significantly by avoiding the need for a distributed registry (e.g., Redis Pub/Sub).
- **Delivery Status Tracking**: Tracking delivery status (PENDING, SENT, FAILED) in the database allows for reliable recovery and auditing of alerts.
- **Avro Logical Types**: Using `timestamp-millis` logical type in Avro ensures consistent time representation across Kafka and the Python service.
- **Idempotency**: Using a unique constraint on `(user_id, alert_event_id)` in the `notification_events` table ensures that users don't receive duplicate notifications for the same alert event.
- **Backpressure**: Implementing a bounded queue in the Kafka consumer prevents the service from being overwhelmed by a burst of alerts.
