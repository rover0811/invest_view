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

from confluent_kafka.schema_registry import Schema, SchemaRegistryClient
from confluent_kafka.schema_registry.error import SchemaRegistryError


DEFAULT_REGISTRY_URL = "http://localhost:8081"


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
        "--subject",
        required=True,
        help="Subject name, e.g. 'stock-ticks-value' (TopicNameStrategy).",
    )
    parser.add_argument(
        "--schema-file",
        required=True,
        type=Path,
        help="Path to a .avsc Avro schema file.",
    )
    return parser.parse_args()


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


def main() -> int:
    args = _parse_args()

    schema_path: Path = args.schema_file
    if not schema_path.is_file():
        print(f"ERROR: schema file not found: {schema_path}", file=sys.stderr)
        return 1

    schema_str = schema_path.read_text(encoding="utf-8")
    schema = Schema(schema_str, schema_type="AVRO")

    client = SchemaRegistryClient({"url": args.registry_url})

    try:
        if _subject_exists(client, args.subject):
            try:
                compatible = client.test_compatibility(args.subject, schema)
                status = "PASS" if compatible else "FAIL"
                print(f"Compatibility check for {args.subject}: {status}")
                if not compatible:
                    print(
                        f"ERROR: schema is NOT compatible with existing version of {args.subject}; aborting before register.",
                        file=sys.stderr,
                    )
                    return 1
            except SchemaRegistryError as exc:
                # Compatibility checks may fail for transient reasons; surface and
                # continue to register, which itself will enforce compatibility.
                print(
                    f"WARNING: compatibility check failed for {args.subject}: {exc}",
                    file=sys.stderr,
                )
    except SchemaRegistryError:
        return 1

    try:
        schema_id = client.register_schema(args.subject, schema)
    except SchemaRegistryError as exc:
        print(
            f"ERROR: failed to register {args.subject}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"Registered {args.subject} -> schema id {schema_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
