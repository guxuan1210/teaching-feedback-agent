(function () {
  "use strict";

  var dirty = false;

  var form = document.querySelector(".feedback-form");
  if (form) {
    form.addEventListener("input", function () {
      dirty = true;
    });
    form.addEventListener("submit", function () {
      dirty = false;
    });
  }

  document.querySelectorAll(".roster a").forEach(function (link) {
    link.addEventListener("click", function (event) {
      if (dirty && !window.confirm("当前内容尚未保存，确定切换学生吗？")) {
        event.preventDefault();
      }
    });
  });

  window.addEventListener("beforeunload", function (event) {
    if (dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
})();
