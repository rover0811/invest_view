# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportRedeclaration=false

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal, cast
from uuid import uuid4

import websockets.exceptions

from .approval_key_manager import KISApprovalKeyManager
from .market_session import KRXSessionAdapter, MarketSessionRouter, NXTSessionAdapter
from .models.requests import SubscribeMessage
from .producer import StockTickProducer
from .raw_parser import KISRawMessageParser, RawKISMessage
from .subscription_pool import KISSubscriptionPool
from .tick_parser import KISTickParser
from .ws_client import KISWebSocketClient


logger = logging.getLogger(__name__)
RECORD_FIELD_COUNT = 46
SUBSCRIBE_ACK_CODES = frozenset({"OPSP0000", "OPSP0002"})
UNSUBSCRIBE_ACK_CODES = frozenset({"OPSP0001", "OPSP0003"})
ControlOperation = Literal["subscribe", "unsubscribe"]


class KISConnectionManager:
    _approval_key_manager: KISApprovalKeyManager
    _ws_client: KISWebSocketClient
    _subscription_pool: KISSubscriptionPool
    _raw_parser: KISRawMessageParser
    _tick_parser: KISTickParser
    _market_router: MarketSessionRouter
    _producer: StockTickProducer | None
    _approval_key: str | None
    _session_id: str
    _sequence: int
    _running: bool
    _max_retries: int
    _base_delay: float
    _pending_control_ops: dict[tuple[str, str], ControlOperation]

    def __init__(
        self,
        approval_key_manager: KISApprovalKeyManager,
        ws_client: KISWebSocketClient,
        subscription_pool: KISSubscriptionPool,
        raw_parser: KISRawMessageParser,
        tick_parser: KISTickParser,
        market_router: MarketSessionRouter,
        producer: StockTickProducer | None = None,
    ) -> None:
        self._approval_key_manager = approval_key_manager
        self._ws_client = ws_client
        self._subscription_pool = subscription_pool
        self._raw_parser = raw_parser
        self._tick_parser = tick_parser
        self._market_router = market_router
        self._producer = producer

        self._approval_key: str | None = None
        self._session_id: str = str(uuid4())
        self._sequence: int = 0
        self._running: bool = False
        self._max_retries: int = 5
        self._base_delay: float = 1.0
        self._pending_control_ops: dict[tuple[str, str], ControlOperation] = {}

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def sequence(self) -> int:
        return self._sequence

    async def start(self) -> None:
        approval_key = await self._approval_key_manager.get_approval_key()
        await self._ws_client.connect()

        self._approval_key = approval_key
        self._session_id = str(uuid4())
        self._sequence = 0
        self._running = True
        self._pending_control_ops.clear()

        await self._subscribe_all()
        await self._receive_loop()

    async def stop(self) -> None:
        self._running = False
        self._pending_control_ops.clear()
        await self._ws_client.disconnect()

    async def _subscribe_all(self) -> None:
        if self._approval_key is None:
            raise RuntimeError("Approval key is not available")

        to_subscribe, to_unsubscribe = self._subscription_pool.diff()

        for tr_id, symbol in to_unsubscribe:
            await self._ws_client.send_unsubscribe(self._approval_key, tr_id, symbol)
            self._pending_control_ops[(tr_id, symbol)] = "unsubscribe"

        for tr_id, symbol in to_subscribe:
            await self._ws_client.send_subscribe(
                SubscribeMessage.subscribe(self._approval_key, tr_id, symbol)
            )
            self._pending_control_ops[(tr_id, symbol)] = "subscribe"

    async def _receive_loop(self) -> None:
        while self._running:
            try:
                raw = await self._ws_client.recv()
            except websockets.exceptions.ConnectionClosedOK:
                if not self._running:
                    break
                logger.info("WebSocket closed normally, reconnecting...")
                await self._reconnect()
                continue
            except (ConnectionError, OSError, RuntimeError, websockets.exceptions.ConnectionClosed) as exc:
                if not self._running:
                    break
                logger.warning("WebSocket receive failed, reconnecting: %s", exc)
                await self._reconnect()
                continue

            try:
                await self._handle_message(raw)
            except Exception:
                logger.exception("Failed to handle KIS message")

    async def _handle_message(self, raw: str) -> None:
        if self._raw_parser.is_pingpong(raw):
            await self._ws_client.send_pong(raw)
            return

        if self._raw_parser.is_json_response(raw):
            await self._handle_json_response(raw)
            return

        message = self._raw_parser.parse(raw)
        if message is None or message.encrypted:
            return

        await self._handle_data_message(message)

    async def _handle_market_switch(self, market_session_code: str) -> None:
        logger.info(
            "Ignoring unsupported market session code for market switching: %s",
            market_session_code,
        )

    async def _reconnect(self) -> None:
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            await asyncio.sleep(self._base_delay * attempt)

            try:
                if self._ws_client.connected:
                    await self._ws_client.disconnect()

                approval_key = await self._approval_key_manager.force_refresh()
                await self._ws_client.connect()

                self._approval_key = approval_key
                self._subscription_pool.clear_actual()
                self._pending_control_ops.clear()
                self._session_id = str(uuid4())
                self._sequence = 0

                await self._subscribe_all()
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Reconnect attempt %s/%s failed: %s",
                    attempt,
                    self._max_retries,
                    exc,
                )

        logger.critical("Reconnect failed after %s attempts", self._max_retries)
        raise RuntimeError("Failed to reconnect KIS WebSocket") from last_error

    async def _handle_json_response(self, raw: str) -> None:
        response = cast(dict[str, object], self._raw_parser.parse_json_response(raw))

        rt_cd = str(self._extract_response_value(response, "rt_cd") or "")
        tr_id = self._extract_response_value(response, "tr_id")
        symbol = self._extract_response_value(response, "tr_key")
        msg_cd = str(self._extract_response_value(response, "msg_cd") or "")
        msg1 = str(self._extract_response_value(response, "msg1") or "")

        if tr_id is None or symbol is None:
            logger.warning("KIS ACK missing tr_id/tr_key: %s", response)
            return

        pending_key = (tr_id, symbol)
        pending_operation = self._pending_control_ops.get(pending_key)
        response_operation = self._classify_control_response(msg_cd, msg1, rt_cd)

        if response_operation is None:
            if rt_cd == "0" and pending_operation is not None:
                response_operation = pending_operation
            else:
                logger.warning(
                    "KIS subscription response NACK or unknown ACK: %s", response
                )
                return

        _ = self._pending_control_ops.pop(pending_key, None)

        if pending_operation is not None and pending_operation != response_operation:
            logger.warning(
                "KIS control response mismatch: pending=%s response=%s payload=%s",
                pending_operation,
                response_operation,
                response,
            )

        if response_operation == "unsubscribe":
            self._subscription_pool.confirm_unsubscribed(tr_id, symbol)
            return

        self._subscription_pool.confirm_subscribed(tr_id, symbol)

    async def _handle_data_message(self, message: RawKISMessage) -> None:
        values = self._raw_parser.split_records(message.payload)
        received_at = datetime.now(timezone.utc).isoformat()

        for index in range(message.count):
            start = index * RECORD_FIELD_COUNT
            end = start + RECORD_FIELD_COUNT
            record_values = values[start:end]
            if len(record_values) != RECORD_FIELD_COUNT:
                logger.warning(
                    "Skipping incomplete tick record: expected=%s actual=%s",
                    RECORD_FIELD_COUNT,
                    len(record_values),
                )
                continue

            market_adapter = self._resolve_market_adapter_from_tr_id(message.tr_id)
            if market_adapter is None:
                logger.warning("Skipping tick with unsupported TR ID: %s", message.tr_id)
                continue

            tick = self._tick_parser.parse(
                raw_record_values=record_values,
                source_tr_id=message.tr_id,
                market=market_adapter.market_name,
                received_at=received_at,
            )
            self._sequence += 1
            if self._producer is not None:
                self._producer.publish(tick, self._session_id, self._sequence)

            await self._handle_market_switch(tick.market_session_code)
            logger.info(
                "Tick received session_id=%s sequence=%s symbol=%s market=%s price=%s",
                self._session_id,
                self._sequence,
                tick.symbol,
                tick.market,
                tick.price,
            )

    def _extract_response_value(self, data: dict[str, object], key: str) -> str | None:
        direct_value = data.get(key)
        if isinstance(direct_value, str):
            return direct_value

        for section_name in ("header", "body"):
            section = data.get(section_name)
            if not isinstance(section, dict):
                continue

            section_value = section.get(key)
            if isinstance(section_value, str):
                return section_value

            nested_input = section.get("input")
            if isinstance(nested_input, dict):
                nested_value = nested_input.get(key)
                if isinstance(nested_value, str):
                    return nested_value

        return None

    def _classify_control_response(
        self,
        msg_cd: str,
        msg1: str,
        rt_cd: str,
    ) -> ControlOperation | None:
        normalized_code = msg_cd.strip().upper()
        if normalized_code in SUBSCRIBE_ACK_CODES:
            return "subscribe"
        if normalized_code in UNSUBSCRIBE_ACK_CODES:
            return "unsubscribe"

        if rt_cd != "0":
            return None

        normalized_msg = " ".join(msg1.strip().upper().split())
        if normalized_msg.startswith("UNSUBSCRIBE"):
            return "unsubscribe"
        if normalized_msg.startswith("SUBSCRIBE"):
            return "subscribe"

        return None

    def _resolve_market_adapter_from_tr_id(
        self, tr_id: str
    ) -> KRXSessionAdapter | NXTSessionAdapter | None:
        normalized = tr_id.strip().upper()
        if normalized.startswith("H0ST"):
            return KRXSessionAdapter()
        if normalized.startswith("H0NX"):
            return NXTSessionAdapter()
        return None
