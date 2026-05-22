"""Test fixtures for alert_service tests.

Specific fixtures (postgres_container, mock_kafka_producer, etc.) are
added by their introducing tasks (T6/T9/T7/T8/T11). T1 only provides
the src-layout sys.path bootstrap so test modules can ``from alert_service.* import``
without requiring an editable install (matches the kis_ingestion test pattern).
"""
import sys
from pathlib import Path

# src layout: make ``services/alert_service/src/`` importable so tests can
# do ``from alert_service.config import ...`` regardless of pytest CWD.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
