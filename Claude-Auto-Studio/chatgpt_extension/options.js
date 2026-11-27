const code = document.getElementById("pairingCode");
const status = document.getElementById("status");
chrome.storage.local.get(["pairingCode"], value => { code.value = value.pairingCode || ""; });

document.getElementById("save").addEventListener("click", async () => {
  const pairingCode = code.value.trim();
  if (pairingCode.length < 32) {
    status.textContent = "ペアリングコードを確認してください。";
    return;
  }
  try {
    const response = await fetch("http://127.0.0.1:8769/pair", {
      method: "POST",
      headers: { "Authorization": `Bearer ${pairingCode}` }
    });
    if (!response.ok) {
      status.textContent = response.status === 401 ? "コードが違います。Auto Studioで新しいコードを発行してください。" : "Auto Studioの連携サーバーに接続できません。アプリが起動中か確認してください。";
      return;
    }
    await chrome.storage.local.set({ pairingCode });
    status.textContent = "接続しました。手動送信とAuto Studio自動往復を利用できます。";
  } catch (_error) {
    status.textContent = "接続できません。Auto Studioを起動してから再度お試しください。";
  }
});
