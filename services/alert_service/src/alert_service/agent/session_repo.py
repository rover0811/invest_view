from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_CREATE_SESSION_SQL = text(
    """
    INSERT INTO agent.chat_sessions (session_id, user_id, ticker)
    VALUES (:sid, :uid, :ticker)
    RETURNING session_id, ticker, created_at
    """
)

_LIST_SESSIONS_SQL = text(
    """
    SELECT session_id, ticker, title, updated_at
    FROM agent.chat_sessions
    WHERE user_id = :uid AND is_archived = false
    ORDER BY updated_at DESC
    """
)

_GET_SESSION_SQL = text(
    """
    SELECT session_id, ticker, title, is_archived, updated_at
    FROM agent.chat_sessions
    WHERE session_id = :sid AND user_id = :uid
    """
)

_ACTIVE_PATH_SQL = text(
    """
    WITH RECURSIVE leaf AS (
      SELECT message_id
      FROM agent.chat_messages
      WHERE session_id = :sid
      ORDER BY created_at DESC
      LIMIT 1
    ), path AS (
      SELECT m.*
      FROM agent.chat_messages m
      JOIN leaf ON m.message_id = leaf.message_id
      UNION ALL
      SELECT p.*
      FROM agent.chat_messages p
      JOIN path ON p.message_id = path.parent_id
    )
    SELECT message_id, parent_id, role, content, status, created_at
    FROM path
    ORDER BY created_at ASC
    """
)

_PATH_TO_MESSAGE_SQL = text(
    """
    WITH RECURSIVE path AS (
      SELECT m.*
      FROM agent.chat_messages m
      WHERE m.session_id = :sid AND m.message_id = :mid
      UNION ALL
      SELECT p.*
      FROM agent.chat_messages p
      JOIN path ON p.message_id = path.parent_id
      WHERE p.session_id = :sid
    )
    SELECT message_id, parent_id, role, content, status, created_at
    FROM path
    ORDER BY created_at ASC
    """
)

_GET_MESSAGE_SQL = text(
    """
    SELECT message_id, session_id, parent_id, role, content, status, created_at
    FROM agent.chat_messages
    WHERE session_id = :sid AND message_id = :mid
    """
)

_COUNT_CHILDREN_SQL = text(
    """
    SELECT count(*) AS n
    FROM agent.chat_messages
    WHERE session_id = :sid AND parent_id = :pid
    """
)

_FIRST_COMPLETE_QA_FOR_UNTITLED_SESSION_SQL = text(
    """
    SELECT u.content AS first_question, a.content AS first_answer
    FROM agent.chat_sessions s
    JOIN agent.chat_messages u ON u.session_id = s.session_id
    JOIN agent.chat_messages a ON a.session_id = s.session_id AND a.parent_id = u.message_id
    WHERE s.session_id = :sid
      AND s.title IS NULL
      AND u.role = 'user'
      AND u.status = 'complete'
      AND a.role = 'assistant'
      AND a.status = 'complete'
    ORDER BY u.created_at ASC, a.created_at ASC
    LIMIT 1
    """
)

_SET_SESSION_TITLE_IF_NULL_SQL = text(
    """
    UPDATE agent.chat_sessions
    SET title = :title, updated_at = now()
    WHERE session_id = :sid AND title IS NULL
    RETURNING session_id, ticker, title, is_archived, updated_at
    """
)

_UPDATE_SESSION_TITLE_SQL = text(
    """
    UPDATE agent.chat_sessions
    SET title = :title, updated_at = now()
    WHERE session_id = :sid AND user_id = :uid
    RETURNING session_id, ticker, title, is_archived, updated_at
    """
)

_UPDATE_SESSION_ARCHIVED_SQL = text(
    """
    UPDATE agent.chat_sessions
    SET is_archived = :is_archived, updated_at = now()
    WHERE session_id = :sid AND user_id = :uid
    RETURNING session_id, ticker, title, is_archived, updated_at
    """
)

_UPDATE_SESSION_TITLE_AND_ARCHIVED_SQL = text(
    """
    UPDATE agent.chat_sessions
    SET title = :title, is_archived = :is_archived, updated_at = now()
    WHERE session_id = :sid AND user_id = :uid
    RETURNING session_id, ticker, title, is_archived, updated_at
    """
)

_ARCHIVE_SESSION_SQL = text(
    """
    UPDATE agent.chat_sessions
    SET is_archived = true, updated_at = now()
    WHERE session_id = :sid AND user_id = :uid
    RETURNING session_id
    """
)

_CREATE_MESSAGE_SQL = text(
    """
    INSERT INTO agent.chat_messages
      (message_id, session_id, parent_id, role, content, status, tool_trace, usage, error)
    VALUES
      (
        :mid,
        :sid,
        :pid,
        :role,
        :content,
        :status,
        CAST(:tool_trace AS jsonb),
        CAST(:usage AS jsonb),
        CAST(:error AS jsonb)
      )
    RETURNING message_id, parent_id, role, content, status, created_at
    """
)

_FINALIZE_MESSAGE_SQL = text(
    """
    UPDATE agent.chat_messages
    SET content = :content, status = :status, error = CAST(:error AS jsonb)
    WHERE message_id = :mid
    """
)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


async def create_session(session: AsyncSession, user_id: uuid.UUID, ticker: str) -> dict[str, Any]:
    session_id = uuid.uuid4()
    result = await session.execute(
        _CREATE_SESSION_SQL,
        {"sid": session_id, "uid": user_id, "ticker": ticker},
    )
    row = result.one()
    return _row_dict(row)


async def list_sessions(session: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    result = await session.execute(_LIST_SESSIONS_SQL, {"uid": user_id})
    return [_row_dict(row) for row in result.all()]


async def get_session(session: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID) -> dict[str, Any] | None:
    result = await session.execute(_GET_SESSION_SQL, {"uid": user_id, "sid": session_id})
    row = result.first()
    return _row_dict(row) if row is not None else None


async def get_active_path(session: AsyncSession, session_id: uuid.UUID) -> list[dict[str, Any]]:
    result = await session.execute(_ACTIVE_PATH_SQL, {"sid": session_id})
    return [_row_dict(row) for row in result.all()]


async def get_path_to_message(
    session: AsyncSession,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
) -> list[dict[str, Any]]:
    result = await session.execute(_PATH_TO_MESSAGE_SQL, {"sid": session_id, "mid": message_id})
    return [_row_dict(row) for row in result.all()]


async def get_message(
    session: AsyncSession,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
) -> dict[str, Any] | None:
    result = await session.execute(_GET_MESSAGE_SQL, {"sid": session_id, "mid": message_id})
    row = result.first()
    return _row_dict(row) if row is not None else None


async def count_children(
    session: AsyncSession,
    session_id: uuid.UUID,
    parent_id: uuid.UUID,
) -> int:
    result = await session.execute(_COUNT_CHILDREN_SQL, {"sid": session_id, "pid": parent_id})
    return int(result.scalar_one())


async def get_first_complete_qa_for_untitled_session(
    session: AsyncSession,
    session_id: uuid.UUID,
) -> dict[str, Any] | None:
    result = await session.execute(_FIRST_COMPLETE_QA_FOR_UNTITLED_SESSION_SQL, {"sid": session_id})
    row = result.first()
    return _row_dict(row) if row is not None else None


async def set_session_title_if_null(
    session: AsyncSession,
    session_id: uuid.UUID,
    title: str,
) -> dict[str, Any] | None:
    result = await session.execute(_SET_SESSION_TITLE_IF_NULL_SQL, {"sid": session_id, "title": title})
    row = result.first()
    await session.commit()
    return _row_dict(row) if row is not None else None


async def update_session(
    session: AsyncSession,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    title: str | None = None,
    title_provided: bool = False,
    is_archived: bool | None = None,
    is_archived_provided: bool = False,
) -> dict[str, Any] | None:
    params = {"uid": user_id, "sid": session_id, "title": title, "is_archived": is_archived}
    if title_provided and is_archived_provided:
        sql = _UPDATE_SESSION_TITLE_AND_ARCHIVED_SQL
    elif title_provided:
        sql = _UPDATE_SESSION_TITLE_SQL
    elif is_archived_provided:
        sql = _UPDATE_SESSION_ARCHIVED_SQL
    else:
        return await get_session(session, user_id, session_id)

    result = await session.execute(sql, params)
    row = result.first()
    return _row_dict(row) if row is not None else None


async def archive_session(session: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID) -> bool:
    result = await session.execute(_ARCHIVE_SESSION_SQL, {"uid": user_id, "sid": session_id})
    return result.first() is not None


async def create_message(
    session: AsyncSession,
    session_id: uuid.UUID,
    role: str,
    content: str,
    parent_id: uuid.UUID | None = None,
    status: str = "complete",
    tool_trace: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message_id = uuid.uuid4()
    result = await session.execute(
        _CREATE_MESSAGE_SQL,
        {
            "mid": message_id,
            "sid": session_id,
            "pid": parent_id,
            "role": role,
            "content": content,
            "status": status,
            "tool_trace": json.dumps(tool_trace) if tool_trace is not None else None,
            "usage": json.dumps(usage) if usage is not None else None,
            "error": json.dumps(error) if error is not None else None,
        },
    )
    row = result.one()
    await session.commit()
    return _row_dict(row)


async def finalize_message(
    session: AsyncSession,
    message_id: uuid.UUID,
    content: str,
    status: str,
    error: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        _FINALIZE_MESSAGE_SQL,
        {
            "mid": message_id,
            "content": content,
            "status": status,
            "error": json.dumps(error) if error is not None else None,
        },
    )
    await session.commit()
