import random
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException

MEMBER_KEYWORDS = [
    "회원번호", "회원 번호", "계정번호", "계정 번호", "닉네임",
    "계정", "아이디", "account", "uid", "member", "userid", "user_id", "pid",
]
COUPON_KEYWORDS = [
    "쿠폰번호", "쿠폰 번호", "쿠폰코드", "교환코드", "교환 코드",
    "시리얼", "쿠폰", "coupon", "code", "redeem",
]
SUBMIT_KEYWORDS = [
    "사용하기", "등록하기", "등록", "적용하기", "적용", "제출", "쿠폰 사용", "submit", "사용",
]

CONFIRM_RE = re.compile(r"확인|예|동의|proceed|^ok$", re.I)
CANCEL_RE = re.compile(r"취소|cancel", re.I)
CLOSE_RE = re.compile(r"확인|닫기|돌아가기|나가기|목록으로|close|ok", re.I)
BLOCK_RE = re.compile(
    r"비정상적인\s*접근|비정상적으로\s*(감지|이용)|이용이?\s*제한|일시적으로\s*(제한|차단)|"
    r"접근이?\s*차단|계정이?\s*차단|차단되었습니다|차단됨|보안\s*문자|매크로|too many requests|captcha",
    re.I,
)

MAX_CONSECUTIVE_ISSUES = 3

# 쿠폰 사이 최소 대기 시간(초). 이보다 짧게는 설정할 수 없다.
MIN_DELAY = 1.0


def _visible_text_inputs(driver):
    result = []
    for el in driver.find_elements(By.CSS_SELECTOR, "input"):
        try:
            t = (el.get_attribute("type") or "text").lower()
            if t in ("text", "tel", "number", "search") and el.is_displayed() and el.is_enabled():
                result.append(el)
        except StaleElementReferenceException:
            continue
    return result


def _field_haystack(el):
    parts = []
    for attr in ("placeholder", "name", "id", "aria-label", "class"):
        try:
            v = el.get_attribute(attr)
        except StaleElementReferenceException:
            v = None
        if v:
            parts.append(v)
    return " ".join(parts).lower()


def _matches(el, keywords):
    hay = _field_haystack(el)
    return any(k.lower() in hay for k in keywords)


def find_member_input(driver):
    inputs = _visible_text_inputs(driver)
    found = next((el for el in inputs if _matches(el, MEMBER_KEYWORDS)), None)
    if found:
        return found
    if len(inputs) == 2:
        return inputs[0]
    return None


def find_coupon_input(driver, exclude_el):
    inputs = [el for el in _visible_text_inputs(driver) if el != exclude_el]
    found = next((el for el in inputs if _matches(el, COUPON_KEYWORDS)), None)
    if found:
        return found
    if len(inputs) == 1:
        return inputs[0]
    return None


def _visible_buttons(driver):
    result = []
    for b in driver.find_elements(By.CSS_SELECTOR, "button, input[type=submit]"):
        try:
            if b.is_displayed() and b.is_enabled():
                result.append(b)
        except StaleElementReferenceException:
            continue
    return result


def _btn_text(b):
    try:
        return (b.text or b.get_attribute("value") or "").strip()
    except StaleElementReferenceException:
        return ""


def find_submit_button(driver):
    for b in _visible_buttons(driver):
        text = _btn_text(b).lower()
        if any(k.lower() in text for k in SUBMIT_KEYWORDS):
            return b
    return None


def set_value(el, value, attempts=3):
    for _ in range(attempts):
        el.click()
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.DELETE)
        el.send_keys(value)
        try:
            if el.get_attribute("value") == value:
                return
        except StaleElementReferenceException:
            return
        time.sleep(0.3)


def extract_nearby_text(el):
    own_text = _btn_text(el)
    node = el
    for _ in range(6):
        try:
            node = node.find_element(By.XPATH, "..")
            text = (node.text or "").strip()
        except StaleElementReferenceException:
            return own_text
        if text and text != own_text and len(text) > len(own_text) and len(text) < 500:
            return text
    return own_text


def _is_gone(el):
    try:
        return not el.is_displayed()
    except StaleElementReferenceException:
        return True


def wait_form_ready(driver, stop_event, timeout=8.0, required_stable=2, interval=0.3):
    """닫기/돌아가기 클릭 이후, 입력 폼(회원번호+쿠폰+제출버튼)이 다시 나타나
    연속으로 안정적으로 잡힐 때까지 대기한다. '완료' 화면이 다음 화면(입력 폼)으로
    완전히 전환되기 전에 곧바로 다음 코드를 제출해 회원번호가 반려되는 문제를 막기 위함."""
    deadline = time.time() + timeout
    stable_count = 0
    while time.time() < deadline:
        if stop_event.is_set():
            return False
        m = find_member_input(driver)
        c = find_coupon_input(driver, m) if m else None
        s = find_submit_button(driver)
        if m and c and s:
            stable_count += 1
            if stable_count >= required_stable:
                return True
        else:
            stable_count = 0
        time.sleep(interval)
    return False


def wait_and_handle_modals(driver, baseline_texts, timeout, stop_event):
    deadline = time.time() + timeout
    baseline = set(baseline_texts)
    stage = "initial"  # initial -> awaiting_transition -> result
    confirm_el = None

    while time.time() < deadline:
        if stop_event.is_set():
            return None, False, True
        time.sleep(0.25)

        if stage == "awaiting_transition":
            if _is_gone(confirm_el):
                stage = "result"
            continue

        current_buttons = _visible_buttons(driver)
        current_texts = {_btn_text(b) for b in current_buttons if _btn_text(b)}

        if stage == "initial":
            new_texts = current_texts - baseline
            cancel_t = next((t for t in new_texts if CANCEL_RE.search(t)), None)
            confirm_t = next((t for t in new_texts if CONFIRM_RE.search(t) and t != cancel_t), None)
            if cancel_t and confirm_t:
                btn = next((b for b in current_buttons if _btn_text(b) == confirm_t), None)
                if btn:
                    btn.click()
                    confirm_el = btn
                    stage = "awaiting_transition"
                continue

            close_t = next((t for t in new_texts if CLOSE_RE.search(t)), None)
            if close_t:
                btn = next((b for b in current_buttons if _btn_text(b) == close_t), None)
                text = extract_nearby_text(btn) if btn else ""
                blocked = bool(BLOCK_RE.search(text))
                if btn:
                    btn.click()
                wait_form_ready(driver, stop_event)
                return text, blocked, False

        elif stage == "result":
            btn = next((b for b in current_buttons if CLOSE_RE.search(_btn_text(b))), None)
            if btn:
                text = extract_nearby_text(btn)
                blocked = bool(BLOCK_RE.search(text))
                btn.click()
                wait_form_ready(driver, stop_event)
                return text, blocked, False

    return None, False, False


def _poll_for(fn, timeout=3.0, interval=0.2):
    deadline = time.time() + timeout
    result = fn()
    while not result and time.time() < deadline:
        time.sleep(interval)
        result = fn()
    return result


def compute_delay(min_sec, max_sec):
    lo = max(MIN_DELAY, float(min_sec or MIN_DELAY))
    hi = max(lo, float(max_sec or lo))
    return lo + random.random() * (hi - lo)


def run_batch(driver, member_number, coupons, delay_min, delay_max, stop_event, on_event):
    """순차적으로 쿠폰을 입력한다. on_event(kind, payload)로 진행 상황을 통지한다.
    kind: 'result' | 'cooldown' | 'aborted' | 'done'
    """
    consecutive_issues = 0
    total = len(coupons)

    member_input = _poll_for(lambda: find_member_input(driver))
    if not member_input:
        on_event("aborted", "회원번호 입력칸을 찾지 못했습니다. 이 사이트는 자동 인식이 지원되지 않아 중단합니다.")
        on_event("done", None)
        return

    set_value(member_input, member_number)

    for i, code in enumerate(coupons):
        if stop_event.is_set():
            on_event("aborted", "사용자 요청으로 중지되었습니다.")
            break

        member_input = find_member_input(driver)
        coupon_input = find_coupon_input(driver, member_input)
        submit_btn = find_submit_button(driver)

        if not member_input or not coupon_input or not submit_btn:
            msg = "입력칸 또는 제출 버튼을 화면에서 찾지 못해 중단합니다. (페이지 구조가 바뀌었을 수 있습니다)"
            on_event("result", {"code": code, "status": "중단", "message": msg})
            on_event("aborted", msg)
            break

        set_value(member_input, member_number)
        set_value(coupon_input, code)

        baseline_texts = {_btn_text(b) for b in _visible_buttons(driver) if _btn_text(b)}
        submit_btn.click()

        text, blocked, stopped = wait_and_handle_modals(driver, baseline_texts, 8, stop_event)

        if stopped:
            on_event("aborted", "사용자 요청으로 중지되었습니다.")
            break

        if blocked:
            on_event("result", {"code": code, "status": "차단 의심", "message": text})
            on_event("aborted", "사이트가 비정상 접근/차단 관련 메시지를 표시했습니다. 안전을 위해 즉시 중단합니다. 잠시 후 브라우저로 직접 상태를 확인해주세요.")
            break

        if text is None:
            consecutive_issues += 1
            on_event("result", {"code": code, "status": "확인불가", "message": "결과 메시지를 자동으로 인식하지 못했습니다. 화면을 직접 확인해주세요."})
        else:
            consecutive_issues = 0
            on_event("result", {"code": code, "status": "완료", "message": text})

        if consecutive_issues >= MAX_CONSECUTIVE_ISSUES:
            on_event("aborted", f"결과를 연속 {MAX_CONSECUTIVE_ISSUES}회 인식하지 못해 안전을 위해 중단합니다. 화면을 직접 확인해주세요.")
            break

        is_last = i == total - 1
        if not is_last and not stop_event.is_set():
            delay = compute_delay(delay_min, delay_max)
            if (i + 1) % 10 == 0:
                delay += 15 + random.random() * 15
                on_event("cooldown", None)
            waited = 0.0
            while waited < delay:
                if stop_event.is_set():
                    break
                step = min(0.2, delay - waited)
                time.sleep(step)
                waited += step

    on_event("done", None)
