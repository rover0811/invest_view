# pyright: reportMissingImports=false
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alert_service.agent.history import build_conversation_manager, load_history_messages, to_strands_messages
from alert_service.agent.market_analyst import build_market_analyst_agent
from alert_service.agent.session_repo import (
    archive_session,
    count_children,
    create_message,
    create_session,
    finalize_message,
    get_active_path,
    get_message,
    get_path_to_message,
    get_session,
    list_sessions,
    update_session,
)
from alert_service.agent.title import maybe_set_title
from alert_service.api.deps import current_user_id


router = APIRouter(prefix="/api/agent", tags=["agent_chat"])


class CreateSessionIn(BaseModel):
    ticker: str = Field(min_length=1)


class PatchSessionIn(BaseModel):
    title: str | None = None
    is_archived: bool | None = None


class ChatIn(BaseModel):
    text: str
    parent_id: uuid.UUID | None = None


class ChatSessionCreatedOut(BaseModel):
    session_id: uuid.UUID
    ticker: str
    created_at: datetime


class ChatSessionListOut(BaseModel):
    session_id: uuid.UUID
    ticker: str
    title: str | None
    updated_at: datetime


class ChatSessionOut(BaseModel):
    session_id: uuid.UUID
    ticker: str
    title: str | None
    is_archived: bool
    updated_at: datetime


class ChatMessageOut(BaseModel):
    message_id: uuid.UUID
    parent_id: uuid.UUID | None
    role: str
    content: str
    status: str
    created_at: datetime


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chat session not found")


@router.post("/sessions", status_code=status.HTTP_201_CREATED, response_model=ChatSessionCreatedOut)
async def create_chat_session(
    payload: CreateSessionIn,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> dict[str, object]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        row = await create_session(session, user_id, payload.ticker)
        await session.commit()
    return row


@router.get("/sessions", response_model=list[ChatSessionListOut])
async def list_chat_sessions(
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> list[dict[str, object]]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        return await list_sessions(session, user_id)


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
async def get_chat_messages(
    session_id: uuid.UUID,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> list[dict[str, object]]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        if await get_session(session, user_id, session_id) is None:
            raise _not_found()
        return await get_active_path(session, session_id)


@router.post("/sessions/{session_id}/stream")
async def stream_chat_session(
    session_id: uuid.UUID,
    body: ChatIn,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> StreamingResponse:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        sess = await get_session(session, user_id, session_id)
        if sess is None:
            raise _not_found()

        active_path = await get_active_path(session, session_id)
        parent_id = body.parent_id or (active_path[-1]["message_id"] if active_path else None)
        history = await load_history_messages(session_factory, session_id)
        user_message = await create_message(
            session,
            session_id,
            "user",
            body.text,
            parent_id=parent_id,
            status="complete",
        )
        assistant_message = await create_message(
            session,
            session_id,
            "assistant",
            "",
            parent_id=user_message["message_id"],
            status="streaming",
        )

    ticker = sess["ticker"]
    config = request.app.state.config
    assistant_id = assistant_message["message_id"]

    async def gen():
        accumulated: list[str] = []
        stream_status = "complete"
        err: dict[str, str] | None = None
        cancelled = False
        try:
            agent = build_market_analyst_agent(
                config,
                messages=history,
                conversation_manager=build_conversation_manager(),
            )
            async for event in agent.stream_async(
                body.text,
                invocation_state={"current_ticker": ticker, "session_factory": session_factory},
            ):
                if await request.is_disconnected():
                    stream_status = "interrupted"
                    break
                if isinstance(event, dict) and "data" in event and isinstance(event["data"], str):
                    token = event["data"]
                    accumulated.append(token)
                    yield f"event: token\ndata: {json.dumps({'text': token})}\n\n"
        except asyncio.CancelledError:
            stream_status = "interrupted"
            cancelled = True
            raise
        except Exception as exc:
            stream_status = "error"
            err = {"message": str(exc)}
            yield f"event: error\ndata: {json.dumps(err)}\n\n"
        finally:
            if stream_status == "complete" and "".join(accumulated).strip() == "":
                stream_status = "error"
                err = {"message": "빈 응답이 생성되었습니다"}
            async with session_factory() as session:
                await finalize_message(session, assistant_id, "".join(accumulated), stream_status, error=err)
            if stream_status != "error" and not cancelled:
                payload = {"message_id": str(assistant_id), "status": stream_status}
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                if stream_status == "complete":
                    await maybe_set_title(session_factory, session_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/messages/{message_id}/regenerate")
async def regenerate_chat_message(
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> StreamingResponse:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        sess = await get_session(session, user_id, session_id)
        if sess is None:
            raise _not_found()

        target_message = await get_message(session, session_id, message_id)
        if target_message is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chat message not found")
        if target_message["role"] != "assistant":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message is not regeneratable")
        if target_message["parent_id"] is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="assistant message has no parent")

        parent_message = await get_message(session, session_id, target_message["parent_id"])
        if parent_message is None or parent_message["role"] != "user":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="assistant parent is not a user message")
        history_path = await get_path_to_message(session, session_id, parent_message["message_id"])
        history_path = history_path[:-1]
        regenerated_message = await create_message(
            session,
            session_id,
            "assistant",
            "",
            parent_id=parent_message["message_id"],
            status="streaming",
        )
        sibling_count = await count_children(session, session_id, parent_message["message_id"])

    ticker = sess["ticker"]
    config = request.app.state.config
    prompt = parent_message["content"]
    history = to_strands_messages(history_path)
    assistant_id = regenerated_message["message_id"]

    async def gen():
        accumulated: list[str] = []
        stream_status = "complete"
        err: dict[str, str] | None = None
        cancelled = False
        try:
            agent = build_market_analyst_agent(
                config,
                messages=history,
                conversation_manager=build_conversation_manager(),
            )
            async for event in agent.stream_async(
                prompt,
                invocation_state={"current_ticker": ticker, "session_factory": session_factory},
            ):
                if await request.is_disconnected():
                    stream_status = "interrupted"
                    break
                if isinstance(event, dict) and "data" in event and isinstance(event["data"], str):
                    token = event["data"]
                    accumulated.append(token)
                    yield f"event: token\ndata: {json.dumps({'text': token})}\n\n"
        except asyncio.CancelledError:
            stream_status = "interrupted"
            cancelled = True
            raise
        except Exception as exc:
            stream_status = "error"
            err = {"message": str(exc)}
            yield f"event: error\ndata: {json.dumps(err)}\n\n"
        finally:
            if stream_status == "complete" and "".join(accumulated).strip() == "":
                stream_status = "error"
                err = {"message": "빈 응답이 생성되었습니다"}
            async with session_factory() as session:
                await finalize_message(session, assistant_id, "".join(accumulated), stream_status, error=err)
            if stream_status != "error" and not cancelled:
                payload = {
                    "message_id": str(assistant_id),
                    "status": stream_status,
                    "sibling_count": sibling_count,
                }
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.patch("/sessions/{session_id}", response_model=ChatSessionOut)
async def patch_chat_session(
    session_id: uuid.UUID,
    payload: PatchSessionIn,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> dict[str, object]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        row = await update_session(
            session,
            user_id,
            session_id,
            title=payload.title,
            title_provided="title" in payload.model_fields_set,
            is_archived=payload.is_archived,
            is_archived_provided="is_archived" in payload.model_fields_set,
        )
        if row is None:
            raise _not_found()
        await session.commit()
    return row


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: uuid.UUID,
    request: Request,
    user_id: Annotated[uuid.UUID, Depends(current_user_id)],
) -> None:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        archived = await archive_session(session, user_id, session_id)
        if not archived:
            raise _not_found()
        await session.commit()
