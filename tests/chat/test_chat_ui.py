from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_chat_messages_share_metadata_body_structure():
    template = (ROOT / "app/templates/chat/index.html").read_text(encoding="utf-8")
    script = (ROOT / "app/static/chat.js").read_text(encoding="utf-8")

    assert template.count('class="msg-meta"') >= 3
    assert 'class="channel-chip chip"' in template
    assert 'meta.className = "msg-meta"' in script
    assert 'className = "channel-chip chip"' in script
    assert 'class="msg user"' in template


def test_chat_css_limits_reading_column_and_aligns_messages():
    css = (ROOT / "app/static/app.css").read_text(encoding="utf-8")

    assert ".chat-reading-column" in css
    assert "max-width: 760px" in css
    assert ".msg.user" in css
    assert "justify-content: flex-end" in css
    assert "prefers-reduced-motion" in css
