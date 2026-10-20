"""Serving sink package (doc 22 15.2, 18.7)."""

from ai_signal.serving.sink import (
    SIGNAL_UPDATES_TOPIC,
    ServingSink,
    SignalUpdate,
    apply_update,
)

__all__ = ["SIGNAL_UPDATES_TOPIC", "ServingSink", "SignalUpdate", "apply_update"]
