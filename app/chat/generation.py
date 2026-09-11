from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable, Iterator

from sqlalchemy.orm import Session

from app.chat import service
from app.chat.providers import ChatProvider


@dataclass(frozen=True)
class GenerationEvent:
    name: str
    payload: dict[str, Any]


def stream_generation(
    db: Session,
    prepared: service.PreparedSend,
    provider: ChatProvider,
    *,
    timeout_seconds: float = 165,
    clock: Callable[[], float] = monotonic,
) -> Iterator[GenerationEvent]:
    yield GenerationEvent(
        "meta",
        {
            "user_message_id": prepared.user_message.message_id,
            "assistant_message_id": prepared.assistant_message_id,
            "model": prepared.model,
        },
    )
    started = clock()
    full_text = ""
    try:
        for delta in provider.stream(prepared.messages):
            if clock() - started >= timeout_seconds:
                service.save_failed_message(db, prepared, full_text, "生成超时")
                yield GenerationEvent(
                    "error",
                    {"message": "生成超时，请发送“重试”", "retryable": True},
                )
                return
            full_text += delta
            yield GenerationEvent("delta", {"text": delta})
    except GeneratorExit:
        service.save_failed_message(db, prepared, full_text, "用户已停止")
        raise
    except Exception:  # noqa: BLE001 - converted into a stable transport event
        if full_text:
            service.save_failed_message(db, prepared, full_text, "模型服务中断")
            message = "模型服务中断，请稍后重试"
        else:
            service.save_failed_message(db, prepared, "", "模型服务暂时不可用")
            message = "模型服务暂时不可用，请重试"
        yield GenerationEvent("error", {"message": message, "retryable": True})
        return

    cited = service.parse_citations(full_text, prepared.context.sources)
    service.save_assistant_message(db, prepared, full_text, cited)
    yield GenerationEvent(
        "sources", {"items": service.sources_payload(prepared.context.sources, cited)}
    )
    yield GenerationEvent("done", {"message_id": prepared.assistant_message_id})
