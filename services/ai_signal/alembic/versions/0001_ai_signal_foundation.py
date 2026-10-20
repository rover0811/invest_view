"""Create the AI signal shared contracts and reference seeds.

Revision ID: 0001_ai_signal_foundation
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_ai_signal_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ("reference", "bronze", "silver", "gold", "serving", "outbox")
ENTITY_TYPES = ("symbol", "sector", "market", "org", "index")
SYMBOLS = (
    ("005930", "삼성전자", "전기·전자", "0013"),
    ("000660", "SK하이닉스", "전기·전자", "0013"),
    ("005380", "현대차", "운송장비·부품", "0015"),
    ("035420", "NAVER", "IT 서비스", "0029"),
    ("035720", "카카오", "IT 서비스", "0029"),
    ("051910", "LG화학", "화학", "0008"),
    ("006400", "삼성SDI", "전기·전자", "0013"),
    ("068270", "셀트리온", "제약", "0009"),
    ("105560", "KB금융", "금융", "0021"),
    ("055550", "신한지주", "금융", "0021"),
    ("000270", "기아", "운송장비·부품", "0015"),
    ("012330", "현대모비스", "운송장비·부품", "0015"),
    ("005490", "POSCO홀딩스", "금속", "0011"),
    ("028260", "삼성물산", "유통", "0016"),
    ("066570", "LG전자", "전기·전자", "0013"),
    ("015760", "한국전력", "전기·가스", "0017"),
    ("032830", "삼성생명", "보험", "0025"),
    ("003550", "LG", "금융", "0021"),
    ("017670", "SK텔레콤", "통신", "0020"),
    ("096770", "SK이노베이션", "화학", "0008"),
    ("034730", "SK", "금융", "0021"),
    ("018260", "삼성에스디에스", "IT 서비스", "0029"),
    ("009150", "삼성전기", "전기·전자", "0013"),
    ("011200", "HMM", "운송·창고", "0019"),
    ("086790", "하나금융지주", "금융", "0021"),
    ("033780", "KT&G", "음식료·담배", "0005"),
    ("010130", "고려아연", "금속", "0011"),
    ("259960", "크래프톤", "IT 서비스", "0029"),
    ("316140", "우리금융지주", "금융", "0021"),
    ("024110", "기업은행", "금융", "0021"),
    ("138040", "메리츠금융지주", "금융", "0021"),
    ("030200", "KT", "통신", "0020"),
    ("090430", "아모레퍼시픽", "화학", "0008"),
    ("010950", "S-Oil", "화학", "0008"),
    ("011170", "롯데케미칼", "화학", "0008"),
    ("047050", "포스코인터내셔널", "유통", "0016"),
    ("002790", "아모레퍼시픽홀딩스", "화학", "0008"),
    ("161390", "한국타이어앤테크놀로지", "화학", "0008"),
    ("078930", "GS", "금융", "0021"),
    ("071050", "한국금융지주", "금융", "0021"),
)
SECTORS = {code: name for _, _, name, code in SYMBOLS}
ORGS = (
    "Micron",
    "CXMT",
    "Solidigm",
    "Hanmi Semiconductor",
    "ASML",
    "Applied Materials",
    "Tokyo Electron",
)


def _entity_id(entity_type: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"invest-view:entity:{entity_type}:{key}")


def _relation_id(from_id: UUID, to_id: UUID, relation_type: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"invest-view:relation:{from_id}:{to_id}:{relation_type}:1970-01-01",
    )


def _check(name: str, column: str, values: Sequence[str]) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    op.create_table(
        "news_title_raw",
        sa.Column("raw_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("source_doc_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("provider_code", sa.Text(), nullable=True),
        sa.Column("category_code", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        schema="bronze",
    )

    op.create_table(
        "entity",
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _check("entity_type_ck", "entity_type", ENTITY_TYPES),
        schema="reference",
    )
    op.create_table(
        "entity_symbol",
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column(
            "signal_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.UniqueConstraint("ticker", "market", name="entity_symbol_ticker_market_uq"),
        schema="reference",
    )
    op.create_table(
        "entity_sector",
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("taxonomy", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.UniqueConstraint("taxonomy", "code", name="entity_sector_taxonomy_code_uq"),
        schema="reference",
    )
    op.create_table(
        "entity_market",
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("market_code", sa.Text(), nullable=False, unique=True),
        schema="reference",
    )
    op.create_table(
        "entity_org",
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("organization_code", sa.Text(), nullable=True, unique=True),
        schema="reference",
    )
    op.create_table(
        "entity_index",
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("exchange_code", sa.Text(), nullable=False),
        sa.Column("index_kind", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "ticker", "exchange_code", name="entity_index_ticker_exchange_uq"
        ),
        schema="reference",
    )
    op.create_table(
        "entity_alias",
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("alias", sa.Text(), primary_key=True),
        sa.Column("match_rule", sa.Text(), nullable=False),
        _check(
            "entity_alias_match_rule_ck", "match_rule", ("exact", "word", "substring")
        ),
        schema="reference",
    )
    op.create_table(
        "relation_type_def",
        sa.Column("relation_type", sa.Text(), primary_key=True),
        sa.Column("from_entity_type", sa.Text(), nullable=False),
        sa.Column("to_entity_type", sa.Text(), nullable=False),
        sa.Column("is_symmetric", sa.Boolean(), nullable=False),
        sa.Column("is_time_varying", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        schema="reference",
    )

    op.create_table(
        "document",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_doc_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_precision", sa.Text(), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _check("document_source_ck", "source", ("KIS_NEWS", "REPORT")),
        _check(
            "document_published_precision_ck", "published_precision", ("SECOND", "DAY")
        ),
        sa.CheckConstraint("version >= 1", name="document_version_ck"),
        sa.UniqueConstraint(
            "source", "source_doc_id", name="document_source_identity_uq"
        ),
        schema="silver",
    )
    op.create_index("ix_document_dedup_key", "document", ["dedup_key"], schema="silver")
    op.create_table(
        "document_kis_news",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("silver.document.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider_code", sa.Text(), nullable=True),
        sa.Column("category_code", sa.Text(), nullable=True),
        sa.Column(
            "raw_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bronze.news_title_raw.raw_id"),
            nullable=False,
        ),
        schema="silver",
    )
    op.create_table(
        "document_revision",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("silver.document.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="document_revision_version_ck"),
        schema="silver",
    )
    op.create_table(
        "document_entity",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("silver.document.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id"),
            primary_key=True,
        ),
        sa.Column("link_method", sa.Text(), nullable=False),
        sa.Column("link_confidence", sa.Numeric(5, 4), nullable=False),
        _check(
            "document_entity_link_method_ck",
            "link_method",
            ("source_tag", "title_alias"),
        ),
        sa.CheckConstraint(
            "link_confidence >= 0 AND link_confidence <= 1",
            name="document_entity_link_confidence_ck",
        ),
        schema="silver",
    )
    op.create_table(
        "document_judgement",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("silver.document.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("direction", sa.SmallInteger(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("fail_reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("probability", sa.Numeric(5, 4), nullable=True),
        sa.CheckConstraint("version >= 1", name="document_judgement_version_ck"),
        sa.CheckConstraint(
            "direction IN (-1, 0, 1)", name="document_judgement_direction_ck"
        ),
        _check("document_judgement_scope_ck", "scope", ("MARKET", "SECTOR", "SYMBOL")),
        sa.CheckConstraint(
            "fail_reason IS NULL OR fail_reason IN ('LISTING_ONLY', 'AD', 'NO_EVIDENCE')",
            name="document_judgement_fail_reason_ck",
        ),
        sa.CheckConstraint(
            "probability IS NULL OR (probability >= 0 AND probability <= 1)",
            name="document_judgement_probability_ck",
        ),
        schema="silver",
    )

    op.create_table(
        "entity_relation",
        sa.Column("relation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "from_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id"),
            nullable=False,
        ),
        sa.Column(
            "to_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id"),
            nullable=False,
        ),
        sa.Column(
            "relation_type",
            sa.Text(),
            sa.ForeignKey("reference.relation_type_def.relation_type"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "evidence_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("silver.document.document_id"),
            nullable=True,
        ),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("probability", sa.Numeric(5, 4), nullable=True),
        _check(
            "entity_relation_source_ck",
            "source",
            ("KIS_SECTOR", "MANUAL", "LLM_EXTRACT"),
        ),
        sa.CheckConstraint(
            "from_entity_id <> to_entity_id", name="entity_relation_distinct_ck"
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="entity_relation_validity_ck",
        ),
        sa.CheckConstraint(
            "probability IS NULL OR (probability >= 0 AND probability <= 1)",
            name="entity_relation_probability_ck",
        ),
        sa.UniqueConstraint(
            "from_entity_id",
            "to_entity_id",
            "relation_type",
            "valid_from",
            name="entity_relation_identity_uq",
        ),
        schema="reference",
    )
    op.create_table(
        "index_value",
        sa.Column(
            "index_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity_index.entity_id"),
            primary_key=True,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(24, 8), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        schema="reference",
    )
    op.create_table(
        "us_etf_daily",
        sa.Column(
            "index_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity_index.entity_id"),
            primary_key=True,
        ),
        sa.Column("business_date", sa.Date(), primary_key=True),
        sa.Column("close", sa.Numeric(24, 8), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        schema="reference",
    )
    op.create_table(
        "index_constituent_weight",
        sa.Column(
            "index_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity_index.entity_id"),
            primary_key=True,
        ),
        sa.Column(
            "symbol_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity_symbol.entity_id"),
            primary_key=True,
        ),
        sa.Column("as_of_date", sa.Date(), primary_key=True),
        sa.Column("weight", sa.Numeric(12, 10), nullable=False),
        sa.Column("market_cap_100m_krw", sa.Numeric(24, 4), nullable=True),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "weight >= 0 AND weight <= 1", name="index_constituent_weight_range_ck"
        ),
        schema="reference",
    )

    op.create_table(
        "event",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("direction", sa.SmallInteger(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _check("event_type_ck", "event_type", ("TARGET_PRICE_CHANGE", "FLOW")),
        _check("event_scope_ck", "scope", ("MARKET", "SECTOR", "SYMBOL")),
        sa.CheckConstraint("direction IN (-1, 0, 1)", name="event_direction_ck"),
        sa.CheckConstraint("version >= 1", name="event_version_ck"),
        schema="gold",
    )
    op.create_table(
        "event_entity",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gold.event.event_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity.entity_id"),
            primary_key=True,
        ),
        sa.Column("role", sa.Text(), primary_key=True),
        sa.Column("decided_by", sa.Text(), nullable=False),
        _check("event_entity_role_ck", "role", ("primary", "affected", "actor")),
        _check("event_entity_decided_by_ck", "decided_by", ("rule", "llm", "human")),
        schema="gold",
    )
    op.create_table(
        "move",
        sa.Column("move_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("move_type", sa.Text(), nullable=False),
        sa.Column(
            "symbol_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity_symbol.entity_id"),
            nullable=False,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("return_pct", sa.Numeric(16, 8), nullable=False),
        sa.Column("price_state", sa.Text(), nullable=False),
        sa.Column("contrib_global", sa.Numeric(16, 8), nullable=True),
        sa.Column("contrib_market", sa.Numeric(16, 8), nullable=True),
        sa.Column("contrib_sector", sa.Numeric(16, 8), nullable=True),
        sa.Column("contrib_idio", sa.Numeric(16, 8), nullable=True),
        sa.Column("beta_method", sa.Text(), nullable=True),
        sa.Column("beta_as_of", sa.Date(), nullable=True),
        sa.Column("dominant_layer", sa.Text(), nullable=True),
        sa.Column("signal_status", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _check("move_type_ck", "move_type", ("INTRADAY_SPIKE", "DAILY")),
        _check(
            "move_price_state_ck", "price_state", ("POSITIVE", "NEUTRAL", "NEGATIVE")
        ),
        sa.CheckConstraint(
            "beta_method IS NULL OR beta_method IN ('OLS_4L', 'UNIT_4L')",
            name="move_beta_method_ck",
        ),
        sa.CheckConstraint(
            "dominant_layer IS NULL OR dominant_layer IN ('GLOBAL', 'MARKET', 'SECTOR', 'IDIO')",
            name="move_dominant_layer_ck",
        ),
        _check(
            "move_signal_status_ck",
            "signal_status",
            ("SHOWN", "HIDDEN", "NO_CANDIDATE"),
        ),
        sa.CheckConstraint("window_end >= window_start", name="move_window_ck"),
        sa.CheckConstraint("version >= 1", name="move_version_ck"),
        schema="gold",
    )
    op.create_index(
        "ix_move_symbol_window_end",
        "move",
        ["symbol_entity_id", "window_end"],
        schema="gold",
    )
    op.create_table(
        "symbol_beta",
        sa.Column(
            "symbol_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reference.entity_symbol.entity_id"),
            primary_key=True,
        ),
        sa.Column("as_of_date", sa.Date(), primary_key=True),
        sa.Column("method", sa.Text(), primary_key=True),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("n_obs", sa.Integer(), nullable=False),
        sa.Column("beta_global", sa.Numeric(16, 8), nullable=False),
        sa.Column("beta_global_sector", sa.Numeric(16, 8), nullable=True),
        sa.Column("beta_market", sa.Numeric(16, 8), nullable=False),
        sa.Column("beta_sector", sa.Numeric(16, 8), nullable=False),
        sa.Column("r2", sa.Numeric(10, 8), nullable=True),
        _check("symbol_beta_method_ck", "method", ("OLS_4L", "UNIT_4L")),
        sa.CheckConstraint(
            "window_days > 0 AND n_obs > 0", name="symbol_beta_observation_ck"
        ),
        schema="gold",
    )
    op.create_table(
        "signal",
        sa.Column(
            "move_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gold.move.move_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("headline_cause_rank", sa.SmallInteger(), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("probability", sa.Numeric(5, 4), nullable=True),
        sa.CheckConstraint("version >= 1", name="signal_version_ck"),
        _check(
            "signal_decided_by_ck", "decided_by", ("llm", "flow_rule", "decomposition")
        ),
        sa.CheckConstraint(
            "headline_cause_rank IS NULL OR headline_cause_rank BETWEEN 1 AND 3",
            name="signal_headline_rank_ck",
        ),
        sa.CheckConstraint(
            "probability IS NULL OR (probability >= 0 AND probability <= 1)",
            name="signal_probability_ck",
        ),
        schema="gold",
    )
    op.create_table(
        "signal_cause",
        sa.Column("move_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("rank", sa.SmallInteger(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("timing", sa.Text(), nullable=False),
        sa.Column("lag_seconds", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["move_id", "version"],
            ["gold.signal.move_id", "gold.signal.version"],
            ondelete="CASCADE",
        ),
        _check("signal_cause_scope_ck", "scope", ("MARKET", "SECTOR", "SYMBOL")),
        _check("signal_cause_timing_ck", "timing", ("LEADING", "LAGGING")),
        sa.CheckConstraint("rank BETWEEN 1 AND 3", name="signal_cause_rank_ck"),
        schema="gold",
    )
    op.create_table(
        "signal_cause_source",
        sa.Column("move_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("rank", sa.SmallInteger(), primary_key=True),
        sa.Column("source_type", sa.Text(), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.ForeignKeyConstraint(
            ["move_id", "version", "rank"],
            [
                "gold.signal_cause.move_id",
                "gold.signal_cause.version",
                "gold.signal_cause.rank",
            ],
            ondelete="CASCADE",
        ),
        _check("signal_cause_source_type_ck", "source_type", ("DOCUMENT", "EVENT")),
        schema="gold",
    )
    op.create_table(
        "signal_retry",
        sa.Column(
            "move_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gold.move.move_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "target_version >= 1 AND attempts >= 0", name="signal_retry_counts_ck"
        ),
        schema="gold",
    )

    op.create_table(
        "symbol_signal",
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column(
            "move_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gold.move.move_id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("headline_label", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="symbol_signal_version_ck"),
        _check(
            "symbol_signal_status_ck", "status", ("SHOWN", "HIDDEN", "NO_CANDIDATE")
        ),
        schema="serving",
    )
    op.create_table(
        "symbol_signal_cause",
        sa.Column(
            "symbol",
            sa.Text(),
            sa.ForeignKey("serving.symbol_signal.symbol", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rank", sa.SmallInteger(), primary_key=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("rank BETWEEN 1 AND 3", name="symbol_signal_cause_rank_ck"),
        sa.CheckConstraint(
            "source_count >= 1", name="symbol_signal_cause_source_count_ck"
        ),
        schema="serving",
    )
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregatetype", sa.Text(), nullable=False),
        sa.Column("aggregateid", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="outbox",
    )
    op.create_index(
        "ix_outbox_events_created_at", "events", ["created_at"], schema="outbox"
    )

    _seed_reference_data()


def _seed_reference_data() -> None:
    bind = op.get_bind()
    entity = sa.table(
        "entity",
        sa.column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.column("entity_type", sa.Text()),
        sa.column("name", sa.Text()),
        schema="reference",
    )
    entity_symbol = sa.table(
        "entity_symbol",
        sa.column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.column("ticker", sa.Text()),
        sa.column("market", sa.Text()),
        sa.column("signal_enabled", sa.Boolean()),
        schema="reference",
    )
    entity_sector = sa.table(
        "entity_sector",
        sa.column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.column("taxonomy", sa.Text()),
        sa.column("code", sa.Text()),
        sa.column("level", sa.SmallInteger()),
        schema="reference",
    )
    entity_market = sa.table(
        "entity_market",
        sa.column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.column("market_code", sa.Text()),
        schema="reference",
    )
    entity_org = sa.table(
        "entity_org",
        sa.column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.column("organization_code", sa.Text()),
        schema="reference",
    )
    entity_index = sa.table(
        "entity_index",
        sa.column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.column("ticker", sa.Text()),
        sa.column("exchange_code", sa.Text()),
        sa.column("index_kind", sa.Text()),
        schema="reference",
    )

    entity_rows = [
        {
            "entity_id": _entity_id("symbol", ticker),
            "entity_type": "symbol",
            "name": name,
        }
        for ticker, name, _, _ in SYMBOLS
    ]
    entity_rows += [
        {"entity_id": _entity_id("sector", code), "entity_type": "sector", "name": name}
        for code, name in sorted(SECTORS.items())
    ]
    entity_rows += [
        {"entity_id": _entity_id("market", code), "entity_type": "market", "name": name}
        for code, name in (("KOSPI", "KOSPI"), ("US", "United States"))
    ]
    entity_rows += [
        {"entity_id": _entity_id("org", name), "entity_type": "org", "name": name}
        for name in ORGS
    ]
    index_specs = [
        (code, "KRX", "DOMESTIC_INDEX", name) for code, name in sorted(SECTORS.items())
    ] + [
        ("0001", "KRX", "DOMESTIC_INDEX", "KOSPI"),
        ("SPY", "AMS", "US_ETF", "SPDR S&P 500 ETF Trust"),
        ("SOXX", "NAS", "US_ETF", "iShares Semiconductor ETF"),
        ("XLB", "AMS", "US_ETF", "Materials Select Sector SPDR Fund"),
    ]
    entity_rows += [
        {"entity_id": _entity_id("index", ticker), "entity_type": "index", "name": name}
        for ticker, _, _, name in index_specs
    ]
    bind.execute(entity.insert(), entity_rows)
    bind.execute(
        entity_symbol.insert(),
        [
            {
                "entity_id": _entity_id("symbol", ticker),
                "ticker": ticker,
                "market": "KOSPI",
                "signal_enabled": ticker in {"000660", "005930"},
            }
            for ticker, _, _, _ in SYMBOLS
        ],
    )
    bind.execute(
        entity_sector.insert(),
        [
            {
                "entity_id": _entity_id("sector", code),
                "taxonomy": "KOSPI",
                "code": code,
                "level": 1,
            }
            for code in sorted(SECTORS)
        ],
    )
    bind.execute(
        entity_market.insert(),
        [
            {"entity_id": _entity_id("market", "KOSPI"), "market_code": "KOSPI"},
            {"entity_id": _entity_id("market", "US"), "market_code": "US"},
        ],
    )
    bind.execute(
        entity_org.insert(),
        [
            {"entity_id": _entity_id("org", name), "organization_code": None}
            for name in ORGS
        ],
    )
    bind.execute(
        entity_index.insert(),
        [
            {
                "entity_id": _entity_id("index", ticker),
                "ticker": ticker,
                "exchange_code": exchange,
                "index_kind": kind,
            }
            for ticker, exchange, kind, _ in index_specs
        ],
    )

    relation_type = sa.table(
        "relation_type_def",
        sa.column("relation_type", sa.Text()),
        sa.column("from_entity_type", sa.Text()),
        sa.column("to_entity_type", sa.Text()),
        sa.column("is_symmetric", sa.Boolean()),
        sa.column("is_time_varying", sa.Boolean()),
        sa.column("description", sa.Text()),
        schema="reference",
    )
    bind.execute(
        relation_type.insert(),
        [
            {
                "relation_type": "BELONGS_TO",
                "from_entity_type": "symbol",
                "to_entity_type": "sector",
                "is_symmetric": False,
                "is_time_varying": True,
                "description": "Symbol belongs to a sector",
            },
            {
                "relation_type": "COMPETES_WITH",
                "from_entity_type": "symbol,org",
                "to_entity_type": "symbol,org",
                "is_symmetric": True,
                "is_time_varying": True,
                "description": "Entities compete in a market",
            },
            {
                "relation_type": "SUPPLIES_TO",
                "from_entity_type": "symbol,org",
                "to_entity_type": "symbol,org",
                "is_symmetric": False,
                "is_time_varying": True,
                "description": "Entity supplies products or equipment",
            },
            {
                "relation_type": "SUBSIDIARY_OF",
                "from_entity_type": "symbol,org",
                "to_entity_type": "symbol,org",
                "is_symmetric": False,
                "is_time_varying": True,
                "description": "Entity is a subsidiary",
            },
            {
                "relation_type": "PROXIED_BY",
                "from_entity_type": "sector",
                "to_entity_type": "index",
                "is_symmetric": False,
                "is_time_varying": False,
                "description": "Sector return is proxied by an index or ETF",
            },
        ],
    )

    relation = sa.table(
        "entity_relation",
        sa.column("relation_id", postgresql.UUID(as_uuid=True)),
        sa.column("from_entity_id", postgresql.UUID(as_uuid=True)),
        sa.column("to_entity_id", postgresql.UUID(as_uuid=True)),
        sa.column("relation_type", sa.Text()),
        sa.column("valid_from", sa.DateTime(timezone=True)),
        sa.column("valid_to", sa.DateTime(timezone=True)),
        sa.column("known_at", sa.DateTime(timezone=True)),
        sa.column("source", sa.Text()),
        sa.column("evidence_document_id", postgresql.UUID(as_uuid=True)),
        sa.column("decided_by", sa.Text()),
        sa.column("probability", sa.Numeric(5, 4)),
        schema="reference",
    )
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    relation_rows: list[dict[str, object]] = []

    def add_relation(from_id: UUID, to_id: UUID, relation_kind: str) -> None:
        if relation_kind == "COMPETES_WITH" and str(from_id) > str(to_id):
            from_id, to_id = to_id, from_id
        relation_rows.append(
            {
                "relation_id": _relation_id(from_id, to_id, relation_kind),
                "from_entity_id": from_id,
                "to_entity_id": to_id,
                "relation_type": relation_kind,
                "valid_from": epoch,
                "valid_to": None,
                "known_at": epoch,
                "source": "MANUAL",
                "evidence_document_id": None,
                "decided_by": "human",
                "probability": None,
            }
        )

    for ticker, _, _, sector_code in SYMBOLS:
        add_relation(
            _entity_id("symbol", ticker),
            _entity_id("sector", sector_code),
            "BELONGS_TO",
        )
    add_relation(
        _entity_id("sector", "0013"), _entity_id("index", "SOXX"), "PROXIED_BY"
    )
    add_relation(_entity_id("sector", "0008"), _entity_id("index", "XLB"), "PROXIED_BY")

    competitors = ("005930", "000660", "Micron", "CXMT")
    competitor_ids = [
        _entity_id("symbol", value) if value.isdigit() else _entity_id("org", value)
        for value in competitors
    ]
    for index, from_id in enumerate(competitor_ids):
        for to_id in competitor_ids[index + 1 :]:
            add_relation(from_id, to_id, "COMPETES_WITH")

    for supplier, customer in (
        ("Hanmi Semiconductor", "000660"),
        ("Hanmi Semiconductor", "Micron"),
        ("ASML", "005930"),
        ("ASML", "000660"),
        ("Applied Materials", "005930"),
        ("Applied Materials", "000660"),
        ("Tokyo Electron", "005930"),
        ("Tokyo Electron", "000660"),
    ):
        supplier_id = _entity_id("org", supplier)
        customer_id = (
            _entity_id("symbol", customer)
            if customer.isdigit()
            else _entity_id("org", customer)
        )
        add_relation(supplier_id, customer_id, "SUPPLIES_TO")

    for child_type, child, parent_type, parent in (
        ("org", "Solidigm", "symbol", "000660"),
        ("symbol", "018260", "symbol", "005930"),
        ("symbol", "032830", "symbol", "005930"),
        ("symbol", "066570", "symbol", "003550"),
        ("symbol", "051910", "symbol", "003550"),
        ("symbol", "017670", "symbol", "034730"),
        ("symbol", "096770", "symbol", "034730"),
        ("symbol", "090430", "symbol", "002790"),
    ):
        add_relation(
            _entity_id(child_type, child),
            _entity_id(parent_type, parent),
            "SUBSIDIARY_OF",
        )

    bind.execute(relation.insert(), relation_rows)


def downgrade() -> None:
    tables = (
        ("outbox", "events"),
        ("serving", "symbol_signal_cause"),
        ("serving", "symbol_signal"),
        ("gold", "signal_retry"),
        ("gold", "signal_cause_source"),
        ("gold", "signal_cause"),
        ("gold", "signal"),
        ("gold", "symbol_beta"),
        ("gold", "move"),
        ("gold", "event_entity"),
        ("gold", "event"),
        ("reference", "index_constituent_weight"),
        ("reference", "us_etf_daily"),
        ("reference", "index_value"),
        ("reference", "entity_relation"),
        ("silver", "document_judgement"),
        ("silver", "document_entity"),
        ("silver", "document_revision"),
        ("silver", "document_kis_news"),
        ("silver", "document"),
        ("reference", "relation_type_def"),
        ("reference", "entity_alias"),
        ("reference", "entity_index"),
        ("reference", "entity_org"),
        ("reference", "entity_market"),
        ("reference", "entity_sector"),
        ("reference", "entity_symbol"),
        ("reference", "entity"),
        ("bronze", "news_title_raw"),
    )
    for schema, table in tables:
        op.drop_table(table, schema=schema)
