const $ = (id) => document.getElementById(id);

const siteUrlEl = $("siteUrl");
const memberEl = $("memberNumber");
const couponsEl = $("coupons");
const delayMinEl = $("delayMin");
const delayMaxEl = $("delayMax");
const startBtn = $("startBtn");
const stopBtn = $("stopBtn");
const statusEl = $("status");
const logEl = $("log");

const MAX_RECOMMENDED = 50;

const STATUS_CLASS = {
  "완료": "ok",
  "확인불가": "warn",
  "중단": "bad",
  "차단 의심": "bad"
};

function parseCoupons(raw) {
  return raw
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function normalizeUrl(u) {
  if (!/^https?:\/\//i.test(u)) return "https://" + u;
  return u;
}

function waitForTabComplete(tabId) {
  return new Promise((resolve) => {
    function listener(id, info) {
      if (id === tabId && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

function renderLog(results) {
  logEl.innerHTML = "";
  results
    .slice()
    .reverse()
    .forEach((r) => {
      const div = document.createElement("div");
      div.className = "log-item";
      const badgeClass = STATUS_CLASS[r.status] || "unknown";
      div.innerHTML =
        `<span class="code">${escapeHtml(r.code)}</span>` +
        `<span class="badge ${badgeClass}">${escapeHtml(r.status)}</span>` +
        `<div class="msg">${escapeHtml(r.message || "")}</div>`;
      logEl.appendChild(div);
    });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function setRunningUI(running) {
  startBtn.disabled = running;
  stopBtn.disabled = !running;
}

function renderState(state, extra) {
  if (!state) return;
  renderLog(state.results || []);
  if (state.running) {
    setRunningUI(true);
    let msg = `진행 중: ${state.index + 1} / ${state.total}`;
    if (extra && extra.cooldown) msg += " (안전을 위한 휴식 중...)";
    statusEl.textContent = msg;
  } else {
    setRunningUI(false);
    if (state.aborted) {
      statusEl.textContent = "중단됨: " + state.aborted;
    } else if (state.total > 0) {
      statusEl.textContent = `완료: 총 ${state.total}개 처리`;
    } else {
      statusEl.textContent = "";
    }
  }
}

async function restoreForm() {
  const saved = await chrome.storage.local.get([
    "siteUrl", "memberNumber", "coupons", "delayMin", "delayMax"
  ]);
  if (saved.siteUrl) siteUrlEl.value = saved.siteUrl;
  if (saved.memberNumber) memberEl.value = saved.memberNumber;
  if (saved.coupons) couponsEl.value = saved.coupons;
  if (saved.delayMin) delayMinEl.value = saved.delayMin;
  if (saved.delayMax) delayMaxEl.value = saved.delayMax;
}

async function restoreRunningStatus() {
  try {
    const tab = await getActiveTab();
    if (!tab) return;
    chrome.tabs.sendMessage(tab.id, { type: "GET_STATUS" }, (resp) => {
      if (chrome.runtime.lastError) return;
      if (resp) renderState(resp);
    });
  } catch (e) {
    /* 콘텐츠 스크립트 미주입 상태 - 실행 이력 없음 */
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || !msg.type) return;
  if (msg.type === "PROGRESS" || msg.type === "DONE") {
    renderState(msg.state, msg);
  }
});

startBtn.addEventListener("click", async () => {
  const memberNumber = memberEl.value.trim();
  const coupons = parseCoupons(couponsEl.value);
  let delayMin = Number(delayMinEl.value) || 4;
  let delayMax = Number(delayMaxEl.value) || 8;
  const siteUrlRaw = siteUrlEl.value.trim();

  if (!memberNumber) {
    statusEl.textContent = "회원번호를 입력해주세요.";
    return;
  }
  if (coupons.length === 0) {
    statusEl.textContent = "쿠폰번호를 한 줄에 하나씩 입력해주세요.";
    return;
  }
  if (delayMin < 3) delayMin = 3;
  if (delayMax < delayMin) delayMax = delayMin;

  if (coupons.length > MAX_RECOMMENDED) {
    const proceed = confirm(
      `한 번에 ${coupons.length}개는 차단 위험이 높습니다. ${MAX_RECOMMENDED}개 이하로 나눠서 진행하는 것을 권장합니다.\n\n그래도 계속하시겠습니까?`
    );
    if (!proceed) return;
  }

  await chrome.storage.local.set({
    siteUrl: siteUrlRaw,
    memberNumber,
    coupons: couponsEl.value,
    delayMin,
    delayMax
  });

  statusEl.textContent = "준비 중...";
  logEl.innerHTML = "";

  let tab = await getActiveTab();
  if (!tab) {
    statusEl.textContent = "활성 탭을 찾을 수 없습니다.";
    return;
  }

  if (siteUrlRaw) {
    const target = normalizeUrl(siteUrlRaw);
    if (tab.url !== target) {
      await chrome.tabs.update(tab.id, { url: target });
      await waitForTabComplete(tab.id);
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  } catch (e) {
    statusEl.textContent = "이 페이지에는 확장 프로그램을 실행할 수 없습니다. (chrome:// 등 특수 페이지 여부 확인)";
    return;
  }

  chrome.tabs.sendMessage(tab.id, { type: "START", payload: { memberNumber, coupons, delayMin, delayMax } }, (resp) => {
    if (chrome.runtime.lastError) {
      statusEl.textContent = "콘텐츠 스크립트와 통신할 수 없습니다: " + chrome.runtime.lastError.message;
      return;
    }
    if (!resp || !resp.ok) {
      statusEl.textContent = (resp && resp.error) || "시작하지 못했습니다.";
      return;
    }
    setRunningUI(true);
    statusEl.textContent = `진행 중: 1 / ${coupons.length}`;
  });
});

stopBtn.addEventListener("click", async () => {
  const tab = await getActiveTab();
  if (!tab) return;
  chrome.tabs.sendMessage(tab.id, { type: "STOP" }, () => {
    if (chrome.runtime.lastError) return;
    statusEl.textContent = "중지 요청됨. 현재 항목 처리 후 멈춥니다.";
  });
});

restoreForm();
restoreRunningStatus();
