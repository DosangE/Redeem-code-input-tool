(function () {
  if (window.__redeemHelperLoaded) {
    return;
  }
  window.__redeemHelperLoaded = true;

  const MEMBER_KEYWORDS = [
    "회원번호", "회원 번호", "계정번호", "계정 번호", "닉네임",
    "계정", "아이디", "account", "uid", "member", "userid", "user_id", "pid"
  ];
  const COUPON_KEYWORDS = [
    "쿠폰번호", "쿠폰 번호", "쿠폰코드", "교환코드", "교환 코드",
    "시리얼", "쿠폰", "coupon", "code", "redeem"
  ];
  const SUBMIT_KEYWORDS = [
    "사용하기", "등록하기", "등록", "적용하기", "적용", "제출", "쿠폰 사용", "submit", "사용"
  ];
  const CONFIRM_KEYWORDS = /확인|예|동의|proceed|^ok$/i;
  const CANCEL_KEYWORDS = /취소|cancel/i;
  const CLOSE_KEYWORDS = /확인|닫기|돌아가기|나가기|목록으로|close|ok/i;
  const BLOCK_KEYWORDS = /비정상적인\s*접근|비정상적으로\s*(감지|이용)|이용이?\s*제한|일시적으로\s*(제한|차단)|접근이?\s*차단|계정이?\s*차단|차단되었습니다|차단됨|보안\s*문자|매크로|too many requests|captcha/i;
  const MAX_CONSECUTIVE_ISSUES = 3;

  let state = { running: false, stopRequested: false, results: [], total: 0, index: 0, aborted: null };

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function isVisible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function visibleTextInputs() {
    return Array.from(document.querySelectorAll("input")).filter((el) => {
      const type = (el.type || "text").toLowerCase();
      return ["text", "tel", "number", "search"].includes(type) && isVisible(el) && !el.disabled;
    });
  }

  function fieldHaystack(el) {
    return [el.placeholder, el.name, el.id, el.getAttribute("aria-label"), el.className]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
  }

  function matchesKeywords(el, keywords) {
    const hay = fieldHaystack(el);
    return keywords.some((k) => hay.includes(k.toLowerCase()));
  }

  function findMemberInput() {
    const inputs = visibleTextInputs();
    let found = inputs.find((el) => matchesKeywords(el, MEMBER_KEYWORDS));
    if (found) return found;
    if (inputs.length === 2) return inputs[0];
    return null;
  }

  function findCouponInput(excludeEl) {
    const inputs = visibleTextInputs().filter((el) => el !== excludeEl);
    let found = inputs.find((el) => matchesKeywords(el, COUPON_KEYWORDS));
    if (found) return found;
    if (inputs.length === 1) return inputs[0];
    return null;
  }

  function findSubmitButton() {
    const candidates = Array.from(document.querySelectorAll("button, input[type=submit]"));
    const visible = candidates.filter((b) => isVisible(b) && !b.disabled);
    return (
      visible.find((b) => {
        const text = (b.textContent || b.value || "").trim().toLowerCase();
        return SUBMIT_KEYWORDS.some((k) => text.includes(k.toLowerCase()));
      }) || null
    );
  }

  function setNativeValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function extractNearbyText(closeBtn) {
    const ownText = (closeBtn.textContent || "").trim();
    let node = closeBtn;
    for (let i = 0; i < 6 && node.parentElement; i++) {
      node = node.parentElement;
      const text = node.innerText && node.innerText.trim();
      if (text && text !== ownText && text.length > ownText.length && text.length < 500) {
        return text;
      }
    }
    return ownText;
  }

  function isGone(el) {
    return !document.contains(el) || !isVisible(el);
  }

  async function waitFormReady(timeoutMs, requiredStable) {
    // 닫기/돌아가기 클릭 이후, 입력 폼(회원번호+쿠폰+제출버튼)이 다시 나타나
    // 연속으로 안정적으로 잡힐 때까지 대기한다. '완료' 화면이 다음 화면(입력 폼)으로
    // 완전히 전환되기 전에 곧바로 다음 코드를 제출해 회원번호가 반려되는 문제를 막기 위함.
    const deadline = Date.now() + timeoutMs;
    let stableCount = 0;
    while (Date.now() < deadline) {
      if (state.stopRequested) return false;
      const m = findMemberInput();
      const c = m ? findCouponInput(m) : null;
      const s = findSubmitButton();
      if (m && c && s) {
        stableCount++;
        if (stableCount >= requiredStable) return true;
      } else {
        stableCount = 0;
      }
      await sleep(300);
    }
    return false;
  }

  async function waitAndHandleModals(preClickButtons, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    const baseline = preClickButtons;
    let stage = "initial"; // initial -> awaitingTransition -> result
    let confirmEl = null;

    while (Date.now() < deadline) {
      if (state.stopRequested) return { text: null, blocked: false, stopped: true };
      await sleep(250);

      if (stage === "awaitingTransition") {
        if (isGone(confirmEl)) stage = "result";
        continue;
      }

      const buttons = Array.from(document.querySelectorAll("button")).filter(isVisible);

      if (stage === "initial") {
        const newButtons = buttons.filter((b) => !baseline.has(b));
        const cancelBtn = newButtons.find((b) => CANCEL_KEYWORDS.test(b.textContent || ""));
        const confirmBtn = newButtons.find((b) => CONFIRM_KEYWORDS.test((b.textContent || "").trim()) && b !== cancelBtn);
        if (cancelBtn && confirmBtn) {
          confirmBtn.click();
          confirmEl = confirmBtn;
          stage = "awaitingTransition";
          continue;
        }

        const closeBtn = newButtons.find((b) => CLOSE_KEYWORDS.test((b.textContent || "").trim()));
        if (closeBtn) {
          const text = extractNearbyText(closeBtn);
          const blocked = BLOCK_KEYWORDS.test(text);
          closeBtn.click();
          await waitFormReady(8000, 2);
          return { text, blocked, stopped: false };
        }
      } else if (stage === "result") {
        const closeBtn = buttons.find((b) => CLOSE_KEYWORDS.test((b.textContent || "").trim()));
        if (closeBtn) {
          const text = extractNearbyText(closeBtn);
          const blocked = BLOCK_KEYWORDS.test(text);
          closeBtn.click();
          await waitFormReady(8000, 2);
          return { text, blocked, stopped: false };
        }
      }
    }
    return { text: null, blocked: false, stopped: false };
  }

  function computeDelayMs(minSec, maxSec) {
    const min = Math.max(3, Number(minSec) || 3);
    const max = Math.max(min, Number(maxSec) || min);
    return Math.round((min + Math.random() * (max - min)) * 1000);
  }

  function broadcast(type, extra) {
    try {
      chrome.runtime.sendMessage(Object.assign({ type, state: snapshotState() }, extra || {}));
    } catch (e) {
      /* popup 이 닫혀있으면 무시 */
    }
  }

  function snapshotState() {
    return {
      running: state.running,
      results: state.results,
      total: state.total,
      index: state.index,
      aborted: state.aborted
    };
  }

  async function pollFor(fn, timeoutMs, intervalMs) {
    const deadline = Date.now() + timeoutMs;
    let result = fn();
    while (!result && Date.now() < deadline) {
      await sleep(intervalMs);
      result = fn();
    }
    return result;
  }

  async function runBatch(payload) {
    const { memberNumber, coupons, delayMin, delayMax } = payload;
    let consecutiveIssues = 0;

    const memberInput = await pollFor(findMemberInput, 3000, 200);
    if (!memberInput) {
      state.aborted = "회원번호 입력칸을 찾지 못했습니다. 이 사이트는 자동 인식이 지원되지 않아 중단합니다.";
      state.running = false;
      broadcast("DONE");
      return;
    }
    setNativeValue(memberInput, memberNumber);

    for (let i = 0; i < coupons.length; i++) {
      if (state.stopRequested) {
        state.aborted = "사용자 요청으로 중지되었습니다.";
        break;
      }

      state.index = i;
      const code = coupons[i];

      const currentMemberInput = findMemberInput();
      const couponInput = findCouponInput(currentMemberInput);
      const submitBtn = findSubmitButton();

      if (!currentMemberInput || !couponInput || !submitBtn) {
        state.aborted = "입력칸 또는 제출 버튼을 화면에서 찾지 못해 중단합니다. (페이지 구조가 바뀌었을 수 있습니다)";
        state.results.push({ code, status: "중단", message: state.aborted });
        break;
      }

      setNativeValue(currentMemberInput, memberNumber);
      setNativeValue(couponInput, code);

      const preClickButtons = new Set(Array.from(document.querySelectorAll("button")).filter(isVisible));
      submitBtn.click();

      const result = await waitAndHandleModals(preClickButtons, 8000);

      if (result.stopped) {
        state.aborted = "사용자 요청으로 중지되었습니다.";
        break;
      }

      if (result.blocked) {
        state.results.push({ code, status: "차단 의심", message: result.text });
        state.aborted = "사이트가 비정상 접근/차단 관련 메시지를 표시했습니다. 안전을 위해 즉시 중단합니다. 잠시 후 브라우저로 직접 상태를 확인해주세요.";
        broadcast("PROGRESS");
        break;
      }

      if (result.text === null) {
        consecutiveIssues++;
        state.results.push({ code, status: "확인불가", message: "결과 메시지를 자동으로 인식하지 못했습니다. 화면을 직접 확인해주세요." });
      } else {
        consecutiveIssues = 0;
        state.results.push({ code, status: "완료", message: result.text });
      }

      broadcast("PROGRESS");

      if (consecutiveIssues >= MAX_CONSECUTIVE_ISSUES) {
        state.aborted = `결과를 연속 ${MAX_CONSECUTIVE_ISSUES}회 인식하지 못해 안전을 위해 중단합니다. 화면을 직접 확인해주세요.`;
        break;
      }

      const isLast = i === coupons.length - 1;
      if (!isLast && !state.stopRequested) {
        let delay = computeDelayMs(delayMin, delayMax);
        if ((i + 1) % 10 === 0) {
          delay += 15000 + Math.random() * 15000;
          broadcast("PROGRESS", { cooldown: true });
        }
        await sleep(delay);
      }
    }

    state.running = false;
    broadcast("DONE");
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || !msg.type) return;

    if (msg.type === "PING") {
      sendResponse({ ok: true });
      return;
    }

    if (msg.type === "GET_STATUS") {
      sendResponse(snapshotState());
      return;
    }

    if (msg.type === "START") {
      if (state.running) {
        sendResponse({ ok: false, error: "이미 실행 중입니다." });
        return;
      }
      state = { running: true, stopRequested: false, results: [], total: msg.payload.coupons.length, index: 0, aborted: null };
      sendResponse({ ok: true });
      runBatch(msg.payload);
      return;
    }

    if (msg.type === "STOP") {
      state.stopRequested = true;
      sendResponse({ ok: true });
      return;
    }
  });
})();
