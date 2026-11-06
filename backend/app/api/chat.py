from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.agent.orchestrator import normalize_request_id
from app.responses.models import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    active_settings = request.app.state.settings
    if len(payload.question) > active_settings.max_question_length:
        raise HTTPException(
            status_code=422,
            detail="question exceeds the configured maximum length",
        )

    allowed, retry_after = request.app.state.rate_limiter.allow(_client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    if payload.request_id:
        request.state.request_id = normalize_request_id(payload.request_id)

    orchestrator = request.app.state.orchestrator
    return orchestrator.chat(
        payload.question,
        request_id=getattr(request.state, "request_id", None),
        session_id=payload.session_id,
    )
