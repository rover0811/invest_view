
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
