#!/usr/bin/env python3
"""Register an Avro schema with Confluent Schema Registry.

Usage:
    python scripts/register_schemas.py \\
        --registry-url http://localhost:8081 \\
        --subject stock-ticks-value \\
        --schema-file schemas/stock-ticks.avsc

Subject naming follows the Confluent TopicNameStrategy: ``<topic>-value``.
Registration is idempotent: re-registering the same schema returns the same
schema ID. When the subject already exists, a BACKWARD compatibility check
is performed (Schema Registry default) and the result is logged.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from confluent_kafka.schema_registry import (
    ConfigCompatibilityLevel,
    Schema,
    SchemaRegistryClient,
    ServerConfig,
)
from confluent_kafka.schema_registry.error import SchemaRegistryError

DEFAULT_REGISTRY_URL = "http://localhost:8081"
COMPATIBILITY = "BACKWARD"
REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SUBJECTS = {
    "news-raw-value": "schemas/news-raw.avsc",
    "flow-raw-value": "schemas/flow-raw.avsc",
    "opinion-raw-value": "schemas/opinion-raw.avsc",
    "price-moves-value": "schemas/price-moves.avsc",
    "signal-requests-value": "schemas/signal-requests.avsc",
    "signal-updates-value": "schemas/signal-updates.avsc",
    "signal-dlq-value": "schemas/signal-requests.avsc",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register an Avro schema with Confluent Schema Registry.",
    )
    parser.add_argument(
        "--registry-url",
        default=os.environ.get("SCHEMA_REGISTRY_URL", DEFAULT_REGISTRY_URL),
        help=(
            "Schema Registry base URL. Defaults to $SCHEMA_REGISTRY_URL or "
            f"{DEFAULT_REGISTRY_URL}."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Register every AI signal topic subject.",
    )
    parser.add_argument(
        "--subject",
        help="Subject name, e.g. 'stock-ticks-value' (TopicNameStrategy).",
    )
    parser.add_argument(
        "--schema-file",
        type=Path,
        help="Path to a .avsc Avro schema file.",
    )
    args = parser.parse_args()
    if args.all and (args.subject or args.schema_file):
        parser.error("--all cannot be combined with --subject or --schema-file")
    if not args.all and (not args.subject or not args.schema_file):
        parser.error("--subject and --schema-file are required unless --all is used")
    return args


def _subject_exists(client: SchemaRegistryClient, subject: str) -> bool:
    try:
        subjects = client.get_subjects()
    except SchemaRegistryError as exc:
        print(
            f"ERROR: failed to list subjects from registry: {exc}",
            file=sys.stderr,
        )
        raise
    return subject in subjects


def _register(
    client: SchemaRegistryClient,
    subject: str,
    schema_path: Path,
) -> int:
    if not schema_path.is_file():
        print(f"ERROR: schema file not found: {schema_path}", file=sys.stderr)
        return 1

    schema_str = schema_path.read_text(encoding="utf-8")
    schema = Schema(schema_str, schema_type="AVRO")

    try:
        client.set_config(
            subject_name=subject,
            config=ServerConfig(compatibility=ConfigCompatibilityLevel.BACKWARD),
        )
        if _subject_exists(client, subject):
            try:
                compatible = client.test_compatibility(subject, schema)
                status = "PASS" if compatible else "FAIL"
                print(f"Compatibility check for {subject}: {status}")
                if not compatible:
                    print(
                        f"ERROR: schema is NOT compatible with existing version of {subject}; aborting before register.",
                        file=sys.stderr,
                    )
                    return 1
            except SchemaRegistryError as exc:
                # Compatibility checks may fail for transient reasons; surface and
                # continue to register, which itself will enforce compatibility.
                print(
                    f"WARNING: compatibility check failed for {subject}: {exc}",
                    file=sys.stderr,
                )
    except SchemaRegistryError as exc:
        print(
            f"ERROR: failed to set {COMPATIBILITY} compatibility for {subject}: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        schema_id = client.register_schema(subject, schema)
    except SchemaRegistryError as exc:
        print(
            f"ERROR: failed to register {subject}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"Registered {subject} ({COMPATIBILITY}) -> schema id {schema_id}")
    return 0


def main() -> int:
    args = _parse_args()
    client = SchemaRegistryClient({"url": args.registry_url})
    registrations = (
        [(subject, REPO_ROOT / path) for subject, path in SCHEMA_SUBJECTS.items()]
        if args.all
        else [(args.subject, args.schema_file)]
    )
    return max(_register(client, subject, path) for subject, path in registrations)


if __name__ == "__main__":
    sys.exit(main())
