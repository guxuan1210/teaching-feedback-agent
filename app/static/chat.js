/* 教学助手前端：流式渲染、停止、重试、新建表单与移动端抽屉。 */
(function () {
  "use strict";

  var layout = document.getElementById("chat-layout");
  if (!layout) {
    return;
  }

  var conversationId = layout.getAttribute("data-conversation-id");
  var canWrite = layout.getAttribute("data-can-write") === "1";
  var modelConfigured = layout.getAttribute("data-model-configured") === "1";
  var archived = layout.getAttribute("data-archived") === "1";

  var messagesEl = document.getElementById("messages");
  var form = document.getElementById("chat-form");
  var input = document.getElementById("chat-input");
  var sendBtn = document.getElementById("chat-send");
  var charCount = document.getElementById("char-count");

  var controller = null;
  var generating = false;

  function scrollToBottom() {
    if (messagesEl) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  }

  function updateCharCount() {
    if (charCount) {
      charCount.textContent = (input ? input.value.length : 0) + " / 4000";
    }
  }

  // ---------- 新建对话表单 ----------
  var scopeType = document.getElementById("scope-type");
  var classSelect = document.getElementById("class-select");
  var studentSelect = document.getElementById("student-select");
  var studentField = document.getElementById("student-field");

  function updateScopeType() {
    if (studentField) {
      studentField.style.display = scopeType.value === "class" ? "none" : "";
    }
  }

  function filterStudents() {
    if (!classSelect || !studentSelect) {
      return;
    }
    var classId = classSelect.value;
    var options = studentSelect.querySelectorAll("option");
    for (var i = 0; i < options.length; i++) {
      var opt = options[i];
      if (!opt.value) {
        continue;
      }
      var classes = (opt.getAttribute("data-classes") || "").split(",").filter(Boolean);
      opt.style.display =
        classId === "" || classes.indexOf(classId) !== -1 ? "" : "none";
    }
  }

  if (scopeType) {
    scopeType.addEventListener("change", updateScopeType);
    updateScopeType();
  }
  if (classSelect) {
    classSelect.addEventListener("change", filterStudents);
    filterStudents();
  }

  // ---------- 移动端抽屉 ----------
  var drawerOpen = document.getElementById("drawer-open");
  var drawerClose = document.getElementById("drawer-close");
  var sidebar = document.getElementById("chat-sidebar");
  if (drawerOpen && sidebar) {
    drawerOpen.addEventListener("click", function () {
      sidebar.classList.add("open");
    });
  }
  if (drawerClose && sidebar) {
    drawerClose.addEventListener("click", function () {
      sidebar.classList.remove("open");
    });
  }

  // ---------- DOM 构建 ----------
  function createUserBubble(text) {
    var div = document.createElement("div");
    div.className = "msg user";
    var avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.textContent = "我";
    var bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    div.appendChild(avatar);
    div.appendChild(bubble);
    return div;
  }

  function createAssistantBubble() {
    var element = document.createElement("div");
    element.className = "msg assistant";

    var avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.textContent = "助";

    var body = document.createElement("div");
    body.className = "msg-body";

    var status = document.createElement("div");
    status.className = "msg-status";

    var bubble = document.createElement("div");
    bubble.className = "bubble";

    var sources = document.createElement("details");
    sources.className = "msg-sources source-details";
    sources.hidden = true;

    body.appendChild(status);
    body.appendChild(bubble);
    body.appendChild(sources);
    element.appendChild(avatar);
    element.appendChild(body);

    return { element: element, body: body, status: status, bubble: bubble, sources: sources };
  }

  function renderSources(container, items) {
    container.hidden = false;
    container.textContent = "";

    var heading = document.createElement("summary");
    heading.textContent = "回答依据";
    container.appendChild(heading);

    if (!items || items.length === 0) {
      var empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "数据不足，无可用来源。";
      container.appendChild(empty);
      return;
    }

    var ul = document.createElement("ul");
    ul.className = "source-list";
    for (var i = 0; i < items.length; i++) {
      var source = items[i];
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = source.url;
      a.textContent = source.label;
      li.appendChild(a);
      if (source.cited) {
        var mark = document.createElement("span");
        mark.className = "cited-mark";
        mark.textContent = "引用";
        li.appendChild(mark);
      }
      ul.appendChild(li);
    }
    container.appendChild(ul);
  }

  function finalizeDone(element, assistantMessageId) {
    generating = false;
    controller = null;
    sendBtn.textContent = "发送";
    input.disabled = !modelConfigured;
    element.setAttribute("data-message-id", assistantMessageId);
    var status = element.querySelector(".msg-status");
    if (status) {
      status.textContent = "";
    }
    installCopyActions(element);
  }

  function finalizeFailed(element, message, assistantMessageId) {
    generating = false;
    controller = null;
    sendBtn.textContent = "发送";
    input.disabled = !modelConfigured;
    element.classList.add("failed");
    if (assistantMessageId) {
      element.setAttribute("data-message-id", assistantMessageId);
    }
    var status = element.querySelector(".msg-status");
    if (status) {
      status.textContent = "";
    }
    var body = element.querySelector(".msg-body");
    var error = document.createElement("p");
    error.className = "msg-error";
    error.textContent = message;
    body.appendChild(error);
    if (canWrite && !archived && assistantMessageId) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "retry-btn ghost";
      btn.textContent = "重试";
      btn.setAttribute("data-message-id", assistantMessageId);
      body.appendChild(btn);
    }
  }

  // ---------- SSE 解析 ----------
  function parseEvent(block) {
    var event = "message";
    var dataLines = [];
    var lines = block.split("\n");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (line.indexOf("event:") === 0) {
        event = line.slice(6).trim();
      } else if (line.indexOf("data:") === 0) {
        dataLines.push(line.slice(5).trim());
      }
    }
    var data = {};
    if (dataLines.length) {
      try {
        data = JSON.parse(dataLines.join("\n"));
      } catch (e) {
        data = {};
      }
    }
    return { event: event, data: data };
  }

  function readSSE(body, onEvent) {
    var reader = body.getReader();
    var decoder = new TextDecoder("utf-8");
    var buffer = "";

    function pump() {
      return reader.read().then(function (result) {
        if (result.done) {
          if (buffer.trim()) {
            var parsed = parseEvent(buffer);
            onEvent(parsed.event, parsed.data);
          }
          return;
        }
        buffer += decoder.decode(result.value, { stream: true });
        var parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (var i = 0; i < parts.length; i++) {
          var block = parts[i];
          if (block.trim()) {
            var parsed = parseEvent(block);
            onEvent(parsed.event, parsed.data);
          }
        }
        return pump();
      });
    }

    return pump();
  }

  function streamReply(assistant, url, body) {
    controller = new AbortController();
    generating = true;
    sendBtn.textContent = "停止";
    input.disabled = true;

    var fullText = "";
    var assistantMessageId = "";

    function onEvent(event, data) {
      if (event === "meta") {
        assistantMessageId = data.assistant_message_id || assistantMessageId;
        assistant.status.textContent = (data.model || "模型") + " · 正在生成";
      } else if (event === "delta") {
        fullText += data.text || "";
        assistant.bubble.textContent = fullText;
      } else if (event === "sources") {
        renderSources(assistant.sources, data.items || []);
      } else if (event === "done") {
        assistant.bubble.textContent = fullText;
        finalizeDone(assistant.element, assistantMessageId);
      } else if (event === "error") {
        finalizeFailed(assistant.element, data.message || "生成失败", assistantMessageId);
      }
    }

    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
      .then(function (response) {
        if (!response.ok) {
          return response.json().then(function (err) {
            throw new Error(err.detail || "请求失败");
          });
        }
        return readSSE(response.body, onEvent);
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") {
          finalizeFailed(assistant.element, "已停止", assistantMessageId);
        } else {
          finalizeFailed(assistant.element, err.message || "请求失败", assistantMessageId);
        }
      });
  }

  // ---------- 可复制文案：识别、复制与本地编辑 ----------
  function normalizeHeading(line) {
    var s = (line || "").trim();
    s = s.replace(/^#{1,6}\s*/, "");
    s = s.replace(/^(\*\*|__)([\s\S]*?)(\*\*|__)$/, "$2");
    s = s.replace(/^(\*|_)([\s\S]*?)(\*|_)$/, "$2");
    s = s.trim();
    s = s.replace(/[:：]\s*$/, "");
    return s.trim();
  }

  function parseCopyableSection(text) {
    var lines = (text || "").split("\n");
    var start = -1;
    var end = -1;
    for (var i = 0; i < lines.length; i++) {
      var heading = normalizeHeading(lines[i]);
      if (start === -1) {
        if (heading === "可复制文案") {
          start = i;
        }
      } else if (heading === "给老师的依据") {
        end = i;
        break;
      }
    }
    if (start === -1 || end === -1) {
      return null;
    }
    var section = lines.slice(start + 1, end).join("\n").trim();
    if (!section) {
      return null;
    }
    return { lines: lines, start: start, end: end, section: section };
  }

  function getBubbleText(bubble) {
    var paragraphs = bubble.querySelectorAll("p");
    if (paragraphs.length) {
      var parts = [];
      for (var i = 0; i < paragraphs.length; i++) {
        parts.push(paragraphs[i].textContent);
      }
      return parts.join("\n");
    }
    return bubble.textContent;
  }

  function appendLines(container, lines) {
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (line.trim() === "") {
        continue;
      }
      var p = document.createElement("p");
      p.textContent = line;
      container.appendChild(p);
    }
  }

  function renderBubbleLines(bubble, lines) {
    bubble.textContent = "";
    appendLines(bubble, lines);
  }

  function copyText(text, button) {
    var stripped = (text || "").replace(/\[S\d+\]/g, "").trim();

    function showCopied() {
      if (!button) {
        return;
      }
      var original = button.textContent;
      button.textContent = "已复制";
      setTimeout(function () {
        button.textContent = original;
      }, 1500);
    }

    function legacyCopy() {
      var ta = document.createElement("textarea");
      ta.value = stripped;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (e) {
        ok = false;
      }
      document.body.removeChild(ta);
      return ok;
    }

    function manualCopy() {
      window.prompt("请手动复制以下文案：", stripped);
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(stripped).then(showCopied, function () {
        if (!legacyCopy()) {
          manualCopy();
        }
      });
    } else if (legacyCopy()) {
      showCopied();
    } else {
      manualCopy();
    }
  }

  function enterCopyEdit(bubble, state, bar) {
    bar.style.display = "none";
    bubble.textContent = "";

    appendLines(bubble, state.lines.slice(0, state.start + 1));

    var textarea = document.createElement("textarea");
    textarea.className = "copy-edit-textarea";
    textarea.value = state.section;
    bubble.appendChild(textarea);

    appendLines(bubble, state.lines.slice(state.end));

    var actions = document.createElement("div");
    actions.className = "copy-edit-actions";

    var doneBtn = document.createElement("button");
    doneBtn.type = "button";
    doneBtn.className = "copy-done-btn";
    doneBtn.textContent = "完成编辑";

    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "copy-cancel-btn ghost";
    cancelBtn.textContent = "取消";

    actions.appendChild(doneBtn);
    actions.appendChild(cancelBtn);
    bubble.appendChild(actions);

    function updateDone() {
      doneBtn.disabled = textarea.value.trim() === "";
    }
    textarea.addEventListener("input", updateDone);
    updateDone();

    cancelBtn.addEventListener("click", function () {
      renderBubbleLines(bubble, state.lines);
      bar.style.display = "";
    });

    doneBtn.addEventListener("click", function () {
      var newSection = textarea.value.trim();
      if (!newSection) {
        return;
      }
      var sectionLines = newSection.split("\n");
      state.lines = state.lines
        .slice(0, state.start + 1)
        .concat(sectionLines, state.lines.slice(state.end));
      state.section = newSection;
      state.end = state.start + 1 + sectionLines.length;
      renderBubbleLines(bubble, state.lines);
      bar.style.display = "";
    });
  }

  function installCopyActions(messageEl) {
    var bubble = messageEl.querySelector(".bubble");
    if (!bubble) {
      return;
    }
    var parsed = parseCopyableSection(getBubbleText(bubble));
    if (!parsed) {
      return;
    }

    var existing = messageEl.querySelector(".msg-actions");
    if (existing) {
      existing.parentNode.removeChild(existing);
    }

    var state = {
      lines: parsed.lines,
      start: parsed.start,
      end: parsed.end,
      section: parsed.section,
    };

    var bar = document.createElement("div");
    bar.className = "msg-actions";

    var copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "copy-btn";
    copyBtn.textContent = "复制文案";

    var editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "edit-btn ghost";
    editBtn.textContent = "编辑文案";

    bar.appendChild(copyBtn);
    bar.appendChild(editBtn);

    copyBtn.addEventListener("click", function () {
      copyText(state.section, copyBtn);
    });
    editBtn.addEventListener("click", function () {
      enterCopyEdit(bubble, state, bar);
    });

    bubble.parentNode.insertBefore(bar, bubble.nextSibling);
  }

  // ---------- 发送与重试 ----------
  function sendMessage() {
    var content = input.value.trim();
    if (!content) {
      return;
    }
    if (content.length > 4000) {
      alert("消息超过 4000 字限制");
      return;
    }

    messagesEl.appendChild(createUserBubble(content));
    var assistant = createAssistantBubble();
    messagesEl.appendChild(assistant.element);

    input.value = "";
    updateCharCount();
    scrollToBottom();

    streamReply(assistant, "/chat/" + conversationId + "/messages", { content: content });
  }

  function retryMessage(messageId) {
    if (generating) {
      return;
    }
    var failedEl = messagesEl.querySelector(
      '.msg.failed[data-message-id="' + messageId + '"]'
    );
    if (failedEl) {
      failedEl.parentNode.removeChild(failedEl);
    }
    var assistant = createAssistantBubble();
    messagesEl.appendChild(assistant.element);
    scrollToBottom();
    streamReply(assistant, "/chat/" + conversationId + "/retry", { message_id: messageId });
  }

  function stopGeneration() {
    if (controller) {
      controller.abort();
    }
  }

  if (form && input && sendBtn) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!generating) {
        sendMessage();
      }
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!generating) {
          sendMessage();
        }
      }
    });

    input.addEventListener("input", updateCharCount);

    sendBtn.addEventListener("click", function () {
      if (generating) {
        stopGeneration();
      } else {
        sendMessage();
      }
    });
  }

  if (messagesEl) {
    messagesEl.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest(".retry-btn") : null;
      if (btn) {
        retryMessage(btn.getAttribute("data-message-id"));
      }
    });
  }

  var quickPrompts = document.querySelectorAll(".quick-prompt");
  for (var i = 0; i < quickPrompts.length; i++) {
    quickPrompts[i].addEventListener("click", function () {
      if (!input || input.disabled) {
        return;
      }
      input.value = this.getAttribute("data-prompt") || this.textContent;
      updateCharCount();
      input.focus();
    });
  }

  // ---------- 会话右键菜单：重命名 / 删除 ----------
  function postForm(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(body).toString(),
      credentials: "same-origin",
    });
  }

  var contextMenu = null;

  function hideContextMenu() {
    if (contextMenu) {
      contextMenu.parentNode.removeChild(contextMenu);
      contextMenu = null;
    }
  }

  function renameConversation(id, currentTitle) {
    var title = window.prompt("重命名对话", currentTitle);
    if (title === null) {
      return;
    }
    title = title.trim();
    if (!title) {
      return;
    }
    postForm("/chat/" + id + "/rename", { title: title }).then(function (response) {
      if (response.ok) {
        window.location.reload();
      } else {
        window.alert("重命名失败");
      }
    });
  }

  function deleteConversation(id, title) {
    if (!window.confirm("确定删除对话「" + title + "」？此操作不可恢复。")) {
      return;
    }
    postForm("/chat/" + id + "/delete", {}).then(function (response) {
      if (!response.ok) {
        window.alert("删除失败");
        return;
      }
      if (id === conversationId) {
        window.location.href = archived ? "/chat?archived=1" : "/chat";
      } else {
        window.location.reload();
      }
    });
  }

  function showContextMenu(x, y, id, title) {
    hideContextMenu();
    var menu = document.createElement("div");
    menu.className = "chat-context-menu";

    var renameBtn = document.createElement("button");
    renameBtn.type = "button";
    renameBtn.textContent = "重命名";
    renameBtn.addEventListener("click", function () {
      hideContextMenu();
      renameConversation(id, title);
    });

    var deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger";
    deleteBtn.textContent = "删除";
    deleteBtn.addEventListener("click", function () {
      hideContextMenu();
      deleteConversation(id, title);
    });

    menu.appendChild(renameBtn);
    menu.appendChild(deleteBtn);
    document.body.appendChild(menu);
    contextMenu = menu;

    var menuWidth = menu.offsetWidth || 120;
    var menuHeight = menu.offsetHeight || 80;
    if (x + menuWidth > window.innerWidth) {
      x = window.innerWidth - menuWidth - 4;
    }
    if (y + menuHeight > window.innerHeight) {
      y = window.innerHeight - menuHeight - 4;
    }
    menu.style.left = x + "px";
    menu.style.top = y + "px";
  }

  var chatItems = document.querySelectorAll(".chat-item");
  for (var ci = 0; ci < chatItems.length; ci++) {
    (function (item) {
      item.addEventListener("contextmenu", function (e) {
        if (item.getAttribute("data-owner") !== "1") {
          return;
        }
        e.preventDefault();
        e.stopPropagation();
        showContextMenu(
          e.clientX,
          e.clientY,
          item.getAttribute("data-conversation-id"),
          item.getAttribute("data-title")
        );
      });
    })(chatItems[ci]);
  }

  document.addEventListener("click", hideContextMenu);
  document.addEventListener("contextmenu", hideContextMenu);

  if (messagesEl) {
    var historyMessages = messagesEl.querySelectorAll(".msg.assistant:not(.failed)");
    for (var i = 0; i < historyMessages.length; i++) {
      installCopyActions(historyMessages[i]);
    }
  }

  updateCharCount();
  scrollToBottom();
})();
