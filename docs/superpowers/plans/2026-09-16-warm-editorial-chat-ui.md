# Warm Editorial Chat UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Rework the Teaching Agent chat page into a readable warm editorial layout without changing chat behavior, APIs, data, or the current branch.

**Architecture:** Keep the existing server-rendered Jinja page and vanilla JavaScript flow. Normalize historical and live messages to one DOM shape with an avatar, metadata line, and body, then use chat-scoped CSS for alignment, readable widths, responsive behavior, focus states, and reduced motion.

**Tech Stack:** FastAPI/Jinja templates, vanilla JavaScript, existing CSS variables, pytest.

---

### Task 1: Add a regression test for the message DOM contract

**Files:**
- Create: `tests/chat/test_chat_ui.py`
- Test: `app/templates/chat/index.html`
- Test: `app/static/chat.js`

- [ ] **Step 1: Write the failing test**

Add a source-level regression test that reads the template and JavaScript and asserts historical and live messages use the shared structure: `.msg-meta`, `.msg-body`, `.channel-chip` inside metadata, and no top-level channel chip sibling in the historical message blocks.

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_chat_messages_share_metadata_body_structure():
    template = (ROOT / "app/templates/chat/index.html").read_text(encoding="utf-8")
    script = (ROOT / "app/static/chat.js").read_text(encoding="utf-8")

    assert template.count('class="msg-meta"') >= 3
    assert 'class="channel-chip chip"' in template
    assert 'class="msg-meta"' in script
    assert 'className = "channel-chip chip"' in script
    assert 'class="msg user"' in template


def test_chat_css_limits_reading_column_and_aligns_messages():
    css = (ROOT / "app/static/app.css").read_text(encoding="utf-8")

    assert ".chat-reading-column" in css
    assert "max-width: 760px" in css
    assert ".msg.user" in css
    assert "justify-content: flex-end" in css
    assert "prefers-reduced-motion" in css
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/chat/test_chat_ui.py -q`

Expected: FAIL because the existing template and script do not yet contain the shared metadata structure or reading-column CSS.

### Task 2: Normalize the server-rendered message markup

**Files:**
- Modify: `app/templates/chat/index.html:132-177`

- [ ] **Step 1: Replace each historical message row with the shared shape**

Use this structure for user, failed assistant, and normal assistant messages:

```html
<div class="msg user">
  <div class="msg-avatar" aria-hidden="true">我</div>
  <div class="msg-content">
    <div class="msg-meta"><span class="msg-role">我</span>{% if row.channel_label %}<span class="chip channel-chip">{{ row.channel_label }}</span>{% endif %}</div>
    <div class="bubble">{% for p in row.paragraphs %}<p>{{ p }}</p>{% endfor %}</div>
  </div>
</div>
```

For assistant rows use `助` and keep `.msg-body` as the body wrapper so existing source, retry, copy, and edit hooks continue to work. Put channel labels in `.msg-meta`, never between avatar and body.

- [ ] **Step 2: Add a centered reading wrapper around the message list**

Keep `id="messages"` on the scroll container and add `.chat-reading-column` around the existing loop, so the container remains the same JavaScript target while the content gets a readable maximum width.

- [ ] **Step 3: Run the focused test**

Run: `pytest tests/chat/test_chat_ui.py -q`

Expected: the first test still fails on CSS, while its markup assertions pass.

### Task 3: Normalize live streaming message markup

**Files:**
- Modify: `app/static/chat.js:80-137`

- [ ] **Step 1: Update `createUserBubble`**

Build `.msg.user > .msg-avatar + .msg-content > .msg-meta + .bubble`, adding `我` to `.msg-role` and `网页` to a `channel-chip` in the metadata row. Do not change the returned element’s classes or text behavior.

- [ ] **Step 2: Update `createAssistantBubble`**

Build `.msg.assistant > .msg-avatar + .msg-content > .msg-meta + .msg-body`, adding `助` to `.msg-role` and `网页` to the channel chip. Return the same `element`, `body`, `status`, `bubble`, and `sources` fields consumed by the rest of the file.

- [ ] **Step 3: Run the focused test**

Run: `pytest tests/chat/test_chat_ui.py -q`

Expected: the markup and JavaScript structure assertions pass; CSS assertions remain the only failures.

### Task 4: Apply the warm editorial layout and responsive states

**Files:**
- Modify: `app/static/app.css:721-1170`

- [ ] **Step 1: Tighten the page frame and sidebar**

Set the chat gap to `1.25rem`, sidebar basis to `240px`, and use the existing paper, line, ink, and accent variables. Keep the existing drawer behavior under 800px.

- [ ] **Step 2: Style the centered reading column and message alignment**

Set `.chat-reading-column` to `width: min(100%, 760px)` and center it. Make `.msg` a two-column row with a 28px avatar, `.msg-content` width limited to `min(100%, 680px)`, user rows right-aligned, assistant rows left-aligned, and user/assistant bubbles use the asymmetric warm editorial corner radii.

- [ ] **Step 3: Move channel labels into metadata styling**

Style `.msg-meta` as a compact role/channel/time row and make `.channel-chip` a low-emphasis inline label. Preserve `.chip` elsewhere on the site by scoping the new rules to `.msg`.

- [ ] **Step 4: Improve composer, controls, and accessibility states**

Give quick prompts and the composer consistent spacing, keep all existing controls usable, add `:focus-visible` outlines scoped to `.chat-layout`, and add a `prefers-reduced-motion: reduce` rule that disables chat transitions.

- [ ] **Step 5: Add narrow responsive rules**

At `max-width: 800px`, allow the reading column to use the full width, set message content to `max-width: 86%`, reduce outer padding, and prevent horizontal overflow. At `max-width: 480px`, stack the title metadata and let quick prompts scroll horizontally without wrapping the page.

- [ ] **Step 6: Run the focused test**

Run: `pytest tests/chat/test_chat_ui.py -q`

Expected: PASS.

### Task 5: Verify behavior and visual output

**Files:**
- Modify: none unless verification finds a regression.

- [ ] **Step 1: Run chat regression tests**

Run: `pytest tests/chat -q`

Expected: all chat tests pass.

- [ ] **Step 2: Check the diff and page source**

Run: `git diff --check` and inspect that only `app/templates/chat/index.html`, `app/static/chat.js`, `app/static/app.css`, and the new UI test are part of this UI change. Existing unrelated worktree changes must remain untouched.

- [ ] **Step 3: Perform browser checks at 375px, 768px, 1024px, and 1440px**

Open the existing local chat page, verify no horizontal scrollbar, confirm user bubbles are on the right, assistant bubbles on the left, labels sit in metadata rows, and the input/quick prompts remain usable. Verify a live message and a failed/retry message retain their current actions.

- [ ] **Step 4: Run the full test suite**

Run: `pytest -q`

Expected: PASS with no new failures.

- [ ] **Step 5: Commit only the UI implementation files**

Run:

```bash
git add app/templates/chat/index.html app/static/chat.js app/static/app.css tests/chat/test_chat_ui.py docs/superpowers/plans/2026-09-16-warm-editorial-chat-ui.md
git commit -m "feat: refresh teaching assistant chat UI"
```

Do not stage or commit the pre-existing enterprise WeChat and chat synchronization changes.
