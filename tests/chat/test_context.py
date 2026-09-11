"""Chat context aggregation regression tests."""

from types import SimpleNamespace

from app.chat.context import build_chat_context


def test_student_context_formats_profile_indicator_objects(
    db_session, profile_feedback
):
    conversation = SimpleNamespace(
        scope_type="student",
        class_id="C1",
        student_id="S1",
    )

    context = build_chat_context(
        db_session,
        conversation,
        "2026-09-01",
        "2026-09-07",
    )

    assert "【高频进步项】" in context.data_block
    assert "主动检查（2 次）" in context.data_block
