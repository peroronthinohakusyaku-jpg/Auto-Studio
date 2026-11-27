const BASE = "http://127.0.0.1:8769";
const CHAT_HOSTS = new Set(["chatgpt.com", "chat.openai.com"]);
const MAX_AUTO_TASKS_PER_CONVERSATION = 5;

function validChatSender(sender) {
  try { return CHAT_HOSTS.has(new URL(sender.url || "").hostname); }
  catch (_error) { return false; }
}

async function authHeaders() {
  const { pairingCode } = await chrome.storage.local.get(["pairingCode"]);
  if (!pairingCode) throw new Error("拡張機能のオプションでペアリングしてください。");
  return { "Authorization": `Bearer ${pairingCode}`, "Content-Type": "application/json" };
}

async function request(path, options = {}) {
  const headers = await authHeaders();
  const response = await fetch(BASE + path, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  let data = {};
  try { data = await response.json(); } catch (_error) {}
  if (!response.ok) throw new Error(data.error || `local bridge error (${response.status})`);
  return data;
}

function conversationKey(url) {
  try {
    const parsed = new URL(url);
    const match = parsed.pathname.match(/\/c\/([^/]+)/);
    return `${parsed.hostname}:${match ? match[1] : "new-chat"}`;
  } catch (_error) { return "unknown"; }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!validChatSender(sender)) {
    sendResponse({ ok: false, message: "ChatGPTのタブからのみ利用できます。" });
    return false;
  }
  (async () => {
    try {
      if (message?.type === "AUTO_STUDIO_HANDOFF") {
        await request("/task", { method: "POST", body: JSON.stringify({ text: message.text }) });
        sendResponse({ ok: true, message: "Auto Studioへ送信しました" });
        return;
      }
      if (message?.type === "AUTO_STUDIO_AUTO_TASK") {
        const state = await request("/status");
        if (!state.auto_handoff_enabled) {
          sendResponse({ ok: true, skipped: true });
          return;
        }
        const key = conversationKey(sender.url);
        const stored = await chrome.storage.session.get(["autoTaskCounts"]);
        const counts = stored.autoTaskCounts || {};
        if ((counts[key] || 0) >= MAX_AUTO_TASKS_PER_CONVERSATION) {
          sendResponse({ ok: false, capped: true, message: "この会話での自動実行上限（5件）に達しました。新しい会話で続けてください。" });
          return;
        }
        await request("/auto-task", { method: "POST", body: JSON.stringify({ text: message.text }) });
        counts[key] = (counts[key] || 0) + 1;
        await chrome.storage.session.set({ autoTaskCounts: counts });
        sendResponse({ ok: true, accepted: true });
        return;
      }
      if (message?.type === "AUTO_STUDIO_POLL_REPLY") {
        const result = await request("/reply");
        sendResponse({ ok: true, reply: result.reply || null });
        return;
      }
      sendResponse({ ok: false, message: "未対応の要求です。" });
    } catch (error) {
      sendResponse({ ok: false, message: error?.message || "Auto Studioに接続できません。" });
    }
  })();
  return true;
});
