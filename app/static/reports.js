/* 周报复制到剪贴板：零网络依赖，纯前端。 */
(function () {
  "use strict";

  function selectElementText(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return selection.toString();
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    var copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (e) {
      copied = false;
    }
    document.body.removeChild(ta);
    return copied;
  }

  function onCopyClick(button, textEl) {
    var text = textEl ? textEl.textContent.trim() : "";
    if (!text) {
      return;
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () {
          button.textContent = "已复制";
        },
        function () {
          if (fallbackCopy(text)) {
            button.textContent = "已复制";
          } else {
            window.prompt("请手动复制以下内容：", text);
          }
        }
      );
      return;
    }

    if (fallbackCopy(text)) {
      button.textContent = "已复制";
    } else {
      window.prompt("请手动复制以下内容：", text);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.getElementById("copy-report");
    var textEl = document.getElementById("report-text");
    if (!button) {
      return;
    }
    button.addEventListener("click", function () {
      onCopyClick(button, textEl);
    });
  });
})();
