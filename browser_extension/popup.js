async function capturePage() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab");
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      function textFrom(selectors) {
        for (const selector of selectors) {
          const text = document.querySelector(selector)?.innerText?.trim();
          if (text) return text.slice(0, 500);
        }
        return "";
      }
      const vacancyTitle = textFrom(["h1", "[data-test-job-title]", ".job-details-jobs-unified-top-card__job-title", ".top-card-layout__title"])
        || document.title.split("|")[0].split("-")[0].trim().slice(0, 500);
      const company = textFrom(["[data-test-job-company-name]", ".job-details-jobs-unified-top-card__company-name", ".topcard__org-name-link", ".top-card-layout__card .topcard__flavor"]);
      return {
        page_url: location.href,
        document_title: document.title,
        vacancy_title: vacancyTitle,
        company,
        visible_text: document.body ? document.body.innerText.slice(0, 200000) : "",
        selected_text: String(getSelection() || "").slice(0, 200000),
        hostname: location.hostname,
        captured_at: new Date().toISOString()
      };
    }
  });
  return result;
}

async function prefillFields() {
  const payload = await capturePage();
  document.getElementById("vacancy-title").value = payload.vacancy_title || "";
  document.getElementById("company").value = payload.company || "";
}

async function save() {
  const status = document.getElementById("status");
  status.textContent = "Saving...";
  const { token } = await chrome.storage.local.get("token");
  if (!token) {
    status.textContent = "Configure the capture token first.";
    return;
  }
  const payload = await capturePage();
  payload.source_label = document.getElementById("source").value;
  payload.vacancy_title = document.getElementById("vacancy-title").value.trim();
  payload.company = document.getElementById("company").value.trim();
  const response = await fetch("http://127.0.0.1:8765/api/v1/manual-capture", {
    method: "POST",
    headers: { "content-type": "application/json", "x-job-assistant-token": token },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    status.textContent = `Failed: ${response.status}`;
    return;
  }
  const result = await response.json();
  if (result.next_opened) {
    status.textContent = "Saved. Opened next queued job.";
  } else if (result.warning) {
    status.textContent = "Saved, but the next job could not be opened.";
  } else {
    status.textContent = "Saved. No pending jobs remain.";
  }
}

document.getElementById("save").addEventListener("click", () => save().catch(error => {
  document.getElementById("status").textContent = error.message;
}));
prefillFields().catch(() => {});
