async function load() {
  const { token } = await chrome.storage.local.get("token");
  document.getElementById("token").value = token || "";
}

async function save() {
  await chrome.storage.local.set({ token: document.getElementById("token").value.trim() });
  document.getElementById("status").textContent = "Saved locally in this browser profile.";
}

document.getElementById("save").addEventListener("click", save);
load();
