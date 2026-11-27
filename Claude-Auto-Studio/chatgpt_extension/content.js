(() => {
  const BUTTON_ATTR = "data-auto-studio-button";
  const styleId = "auto-studio-chatgpt-style";
  const TASK_OPEN = "[AUTO_STUDIO_TASK]";
  const TASK_CLOSE = "[/AUTO_STUDIO_TASK]";
  const seen = new Set();
  let scanTimer = 0;
  let pendingReply = "";
  let replyBusy = false;

  function addStyle() {
    if (document.getElementById(styleId)) return;
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `.auto-studio-send{margin:8px 0;padding:6px 10px;border:1px solid #d7865a;border-radius:8px;background:#2c211c;color:#ffe8d9;cursor:pointer;font:12px system-ui,sans-serif}.auto-studio-send:hover{background:#483024}.auto-studio-send[data-state="done"]{border-color:#4a9}.auto-studio-send[data-state="error"]{border-color:#d66}#auto-studio-latest{position:fixed;right:24px;bottom:22px;z-index:2147483647;box-shadow:0 3px 14px #0008}`;
    document.documentElement.appendChild(style);
  }

  function isVisible(element) {
    if (!element || !element.isConnected) return false;
    const style = getComputedStyle(element), rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }

  function textFrom(element) { return isVisible(element) ? (element.innerText || "").trim() : ""; }

  function latestAssistantText() {
    const selected = (window.getSelection()?.toString() || "").trim();
    if (selected) return selected;
    const messages = [...document.querySelectorAll('[data-message-author-role="assistant"], [data-turn="assistant"]')].filter(isVisible);
    if (messages.length) return textFrom(messages[messages.length - 1]);
    const markdown = [...document.querySelectorAll("main .markdown, main [class~='markdown']")].filter(isVisible).map(textFrom).filter(Boolean);
    if (markdown.length) return markdown[markdown.length - 1];
    const articles = [...document.querySelectorAll("main article")].filter(isVisible);
    for (let i = articles.length - 1; i >= 0; i--) {
      const article = articles[i];
      if (!article.querySelector('[data-message-author-role="user"], [data-turn="user"]')) return textFrom(article.querySelector(".markdown") || article);
    }
    return "";
  }

  function sendAnswer(button, text) {
    if (!text) { showButton(button, "ChatGPTの回答が見つかりません", "error"); return; }
    button.disabled = true;
    button.textContent = "送信中…";
    chrome.runtime.sendMessage({ type: "AUTO_STUDIO_HANDOFF", text }, result => {
      const error = chrome.runtime.lastError;
      button.disabled = false;
      showButton(button, error ? "拡張機能エラー" : result?.message || "送信に失敗しました", error || !result?.ok ? "error" : "done");
    });
  }

  function showButton(button, label, state) {
    button.dataset.state = state;
    button.textContent = label;
    window.setTimeout(() => { button.textContent = "Auto Studioへ送る"; button.dataset.state = ""; }, 4000);
  }

  function installButtons() {
    addStyle();
    for (const message of document.querySelectorAll('[data-message-author-role="assistant"]')) {
      const host = message.parentElement;
      if (!host || host.querySelector(`[${BUTTON_ATTR}]`)) continue;
      const button = document.createElement("button");
      button.className = "auto-studio-send"; button.setAttribute(BUTTON_ATTR, "true");
      button.textContent = "Auto Studioへ送る"; button.type = "button";
      button.addEventListener("click", () => sendAnswer(button, (window.getSelection()?.toString() || "").trim() || textFrom(message) || latestAssistantText()));
      host.appendChild(button);
    }
    if (document.body && !document.getElementById("auto-studio-latest")) {
      const button = document.createElement("button");
      button.id = "auto-studio-latest"; button.className = "auto-studio-send";
      button.textContent = "Auto Studioへ送る"; button.type = "button";
      button.title = "直近のChatGPT回答をAuto Studioへ渡します";
      button.addEventListener("click", () => sendAnswer(button, latestAssistantText()));
      document.body.appendChild(button);
    }
  }

  function isGenerating() {
    return !![...document.querySelectorAll('button[data-testid="stop-button"],button[aria-label*="Stop generating"],button[aria-label*="停止"]')].find(isVisible);
  }

  function reportTask(text, fingerprint) {
    chrome.runtime.sendMessage({ type: "AUTO_STUDIO_AUTO_TASK", text }, result => {
      if (chrome.runtime.lastError) return;
      if (result?.skipped) return;
      if (result?.capped) {
        const badge = document.getElementById("auto-studio-latest");
        if (badge) showButton(badge, result.message, "error");
      } else if (result?.ok) seen.add(fingerprint);
    });
  }

  function scanForTask() {
    if (isGenerating()) { scanTimer = window.setTimeout(scanForTask, 1200); return; }
    const answer = latestAssistantText();
    const start = answer.lastIndexOf(TASK_OPEN);
    if (start < 0) return;
    const contentStart = start + TASK_OPEN.length;
    const end = answer.indexOf(TASK_CLOSE, contentStart);
    if (end < 0) return;
    const text = answer.slice(contentStart, end).trim();
    const fingerprint = `${location.pathname}:${text}`;
    if (!text || seen.has(fingerprint)) return;
    reportTask(text, fingerprint);
  }

  function findComposer() {
    const candidates = [...document.querySelectorAll("#prompt-textarea,textarea,[contenteditable='true']")];
    return candidates.find(el => isVisible(el) && !el.closest("[data-auto-studio-button]")) || null;
  }

  function fillComposer(element, text) {
    element.focus();
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value")?.set;
      if (setter) setter.call(element, text); else element.value = text;
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    } else {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = window.getSelection();
      selection.removeAllRanges(); selection.addRange(range);
      const inserted = document.execCommand("insertText", false, text);
      if (!inserted) {
        element.replaceChildren();
        const paragraph = document.createElement("p"); paragraph.textContent = text; element.appendChild(paragraph);
      }
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    }
  }

  function deliverReply() {
    if (!pendingReply || replyBusy) return;
    const composer = findComposer();
    if (!composer) return;
    const current = (composer.innerText || composer.value || "").trim();
    if (current) return; // Never overwrite a user's draft.
    replyBusy = true;
    const text = `[AUTO_STUDIO_RESULT]\n${pendingReply}\n[/AUTO_STUDIO_RESULT]`;
    fillComposer(composer, text);
    window.setTimeout(() => {
      const send = [...document.querySelectorAll('[data-testid="send-button"],button[aria-label="Send prompt"],button[aria-label="Send"]')].find(el => isVisible(el) && !el.disabled);
      if (send) send.click();
      else {
        const form = composer.closest("form");
        if (form) form.requestSubmit(); else { replyBusy = false; return; }
      }
      pendingReply = "";
      replyBusy = false;
    }, 350);
  }

  function pollReply() {
    chrome.runtime.sendMessage({ type: "AUTO_STUDIO_POLL_REPLY" }, result => {
      if (chrome.runtime.lastError) return;
      if (result?.ok && result.reply && !pendingReply) pendingReply = result.reply;
      deliverReply();
    });
  }

  installButtons();
  const observer = new MutationObserver(() => {
    installButtons();
    window.clearTimeout(scanTimer);
    scanTimer = window.setTimeout(scanForTask, 1000);
    deliverReply();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  window.setInterval(pollReply, 1800);
})();
