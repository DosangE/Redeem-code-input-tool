import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

import automation

MAX_RECOMMENDED = 50
STATUS_TAGS = {"완료": "ok", "확인불가": "warn", "중단": "bad", "차단 의심": "bad"}

CONFIG_PATH = Path.home() / ".redeem_code_input_tool.json"

MANUAL_KEY = "manual"
# 프리셋 사이트: (내부 키, 버튼 이름, 주소). 회원번호는 키별로 저장된다.
SITE_PRESETS = [
    ("tskgb", "세나 리버스", "https://coupon.netmarble.com/tskgb"),
    ("sololv", "나혼렙 어라이즈", "https://coupon.netmarble.com/sololv"),
]

DEFAULT_DELAY_MIN = 1.0
DEFAULT_DELAY_MAX = 2.0


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class RedeemApp:
    def __init__(self, root):
        self.root = root
        self.root.title("리딤코드 입력기")
        self.root.geometry("460x700")
        self.root.minsize(420, 580)

        self.driver = None
        self.stop_event = threading.Event()
        self.worker = None
        self.event_queue = queue.Queue()
        self.total = 0
        self.done_count = 0

        self.config = load_config()
        saved_members = self.config.get("member_numbers", {})
        # 화면에 표시할 회원번호를 사이트별로 들고 있는다. 프리셋 두 개만 파일에 저장된다.
        self.member_cache = {key: saved_members.get(key, "") for key, _, _ in SITE_PRESETS}
        self.member_cache[MANUAL_KEY] = ""

        self.delay_min = self._as_float(self.config.get("delay_min"), DEFAULT_DELAY_MIN)
        self.delay_max = self._as_float(self.config.get("delay_max"), DEFAULT_DELAY_MAX)

        last_site = self.config.get("last_site")
        valid_keys = [key for key, _, _ in SITE_PRESETS] + [MANUAL_KEY]
        self.current_site = last_site if last_site in valid_keys else SITE_PRESETS[0][0]

        self._build_ui()
        self._apply_site_selection(initial=True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._poll_queue)

    @staticmethod
    def _as_float(value, fallback):
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _build_ui(self):
        pad = {"padx": 10, "pady": (6, 0)}

        tk.Label(self.root, text="쿠폰 사이트 선택").pack(anchor="w", **pad)
        site_row = tk.Frame(self.root)
        site_row.pack(fill="x", padx=10)
        self.site_var = tk.StringVar(value=self.current_site)
        for key, label, _ in SITE_PRESETS:
            tk.Radiobutton(
                site_row, text=label, value=key, variable=self.site_var,
                indicatoron=False, command=self._on_site_changed,
                padx=6, pady=4, selectcolor="#c7d9f7",
            ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Radiobutton(
            site_row, text="주소 직접 입력", value=MANUAL_KEY, variable=self.site_var,
            indicatoron=False, command=self._on_site_changed,
            padx=6, pady=4, selectcolor="#c7d9f7",
        ).pack(side="left", fill="x", expand=True)

        url_row = tk.Frame(self.root)
        url_row.pack(fill="x", padx=10, pady=(6, 0))
        self.site_url = tk.Entry(url_row)
        self.site_url.pack(side="left", fill="x", expand=True)
        tk.Button(url_row, text="브라우저 열기", command=self._on_open_browser).pack(side="left", padx=(6, 0))

        tk.Label(self.root, text="회원번호 / 계정정보").pack(anchor="w", **pad)
        self.member_number = tk.Entry(self.root)
        self.member_number.pack(fill="x", padx=10)
        self.member_hint = tk.Label(self.root, text="", fg="#777", anchor="w", justify="left", wraplength=430)
        self.member_hint.pack(fill="x", padx=10, pady=(2, 0))

        tk.Label(self.root, text="쿠폰번호 (한 줄에 하나씩)").pack(anchor="w", **pad)
        self.coupons = scrolledtext.ScrolledText(self.root, height=6)
        self.coupons.pack(fill="both", padx=10)

        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill="x", padx=10, pady=(10, 0))
        self.start_btn = tk.Button(btn_frame, text="시작", bg="#2563eb", fg="white", command=self._on_start)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.stop_btn = tk.Button(btn_frame, text="중지", bg="#e5484d", fg="white", command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        delay_row = tk.Frame(self.root)
        delay_row.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(delay_row, text="딜레이 세부설정", command=self._on_delay_settings).pack(side="left")
        self.delay_label = tk.Label(delay_row, text="", fg="#777", anchor="w")
        self.delay_label.pack(side="left", padx=(8, 0))
        self._refresh_delay_label()

        self.status_label = tk.Label(self.root, text="", fg="#333", anchor="w", justify="left", wraplength=430)
        self.status_label.pack(fill="x", padx=10, pady=(6, 0))

        self.log = scrolledtext.ScrolledText(self.root, height=12, state="disabled")
        self.log.pack(fill="both", expand=True, padx=10, pady=(6, 8))
        self.log.tag_config("ok", foreground="#166534")
        self.log.tag_config("warn", foreground="#854d0e")
        self.log.tag_config("bad", foreground="#991b1b")

        notice = (
            "본 도구는 화면에 보이는 입력칸/버튼을 사람이 누르는 것과 동일하게 자동으로 채워 넣을 뿐이며, "
            "캡차 우회나 탐지 회피 기능은 없습니다. 본인 소유 계정에만 사용하고, 각 게임사의 이용약관을 "
            "확인 후 책임 하에 사용하세요."
        )
        tk.Label(self.root, text=notice, fg="#999", wraplength=430, justify="left", font=("", 8)).pack(
            fill="x", padx=10, pady=(0, 8)
        )

    # --- 사이트 선택 / 회원번호 캐시 ---

    def _preset_url(self, key):
        for site_key, _, url in SITE_PRESETS:
            if site_key == key:
                return url
        return None

    def _set_entry(self, entry, value):
        state = entry.cget("state")
        entry.config(state="normal")
        entry.delete(0, "end")
        entry.insert(0, value)
        if state != "normal":
            entry.config(state=state)

    def _on_site_changed(self):
        # 전환 전 화면의 회원번호를 이전 사이트 몫으로 보관한다.
        self.member_cache[self.current_site] = self.member_number.get().strip()
        self.current_site = self.site_var.get()
        self._apply_site_selection()

    def _apply_site_selection(self, initial=False):
        key = self.current_site
        url = self._preset_url(key)

        if url:
            self._set_entry(self.site_url, url)
            self.site_url.config(state="readonly")
            self.member_hint.config(text="이 사이트의 회원번호는 자동으로 저장되어 다음 실행 때 채워집니다.")
        else:
            self.site_url.config(state="normal")
            if initial or not self.site_url.get().strip():
                self._set_entry(self.site_url, "")
            self.member_hint.config(text="직접 입력한 주소의 회원번호는 저장되지 않습니다.")

        self._set_entry(self.member_number, self.member_cache.get(key, ""))

    def _persist_member_numbers(self):
        self.member_cache[self.current_site] = self.member_number.get().strip()
        self.config["member_numbers"] = {
            key: self.member_cache.get(key, "") for key, _, _ in SITE_PRESETS
        }
        self.config["last_site"] = self.current_site
        save_config(self.config)

    # --- 딜레이 설정 ---

    def _refresh_delay_label(self):
        self.delay_label.config(text=f"현재 {self.delay_min:g}~{self.delay_max:g}초")

    def _on_delay_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("딜레이 세부설정")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog,
            text=("쿠폰 하나를 처리한 뒤 다음 쿠폰까지 기다리는 시간입니다.\n"
                  f"너무 짧게 두면 사이트에서 비정상 접근으로 판단할 수 있습니다.\n"
                  f"(최소 {automation.MIN_DELAY:g}초, 10개마다 자동으로 긴 휴식이 추가됩니다)"),
            justify="left", wraplength=320,
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 8), sticky="w")

        tk.Label(dialog, text="최소 딜레이(초)").grid(row=1, column=0, padx=12, sticky="w")
        min_entry = tk.Entry(dialog, width=10)
        min_entry.insert(0, f"{self.delay_min:g}")
        min_entry.grid(row=1, column=1, padx=12, sticky="w")

        tk.Label(dialog, text="최대 딜레이(초)").grid(row=2, column=0, padx=12, pady=(6, 0), sticky="w")
        max_entry = tk.Entry(dialog, width=10)
        max_entry.insert(0, f"{self.delay_max:g}")
        max_entry.grid(row=2, column=1, padx=12, pady=(6, 0), sticky="w")

        def on_reset():
            self._set_entry(min_entry, f"{DEFAULT_DELAY_MIN:g}")
            self._set_entry(max_entry, f"{DEFAULT_DELAY_MAX:g}")

        def on_save():
            new_min = max(automation.MIN_DELAY, self._as_float(min_entry.get(), DEFAULT_DELAY_MIN))
            new_max = max(new_min, self._as_float(max_entry.get(), DEFAULT_DELAY_MAX))
            self.delay_min, self.delay_max = new_min, new_max
            self.config["delay_min"] = new_min
            self.config["delay_max"] = new_max
            save_config(self.config)
            self._refresh_delay_label()
            dialog.destroy()

        button_row = tk.Frame(dialog)
        button_row.grid(row=3, column=0, columnspan=2, padx=12, pady=12, sticky="e")
        tk.Button(button_row, text="기본값", command=on_reset).pack(side="left", padx=(0, 6))
        tk.Button(button_row, text="취소", command=dialog.destroy).pack(side="left", padx=(0, 6))
        tk.Button(button_row, text="저장", command=on_save).pack(side="left")

    # --- 실행 ---

    def _append_log(self, code, status, message):
        self.log.configure(state="normal")
        tag = STATUS_TAGS.get(status, "")
        self.log.insert("end", f"[{status}] ", (tag,))
        self.log.insert("end", f"{code}\n{message}\n\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_running(self, running):
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")

    def _make_options(self):
        options = Options()
        options.add_experimental_option(
            "prefs",
            {
                "autofill.profile_enabled": False,
                "autofill.credit_card_enabled": False,
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
            },
        )
        options.add_argument("--log-level=3")
        return options

    def _clear_clipboard(self):
        try:
            self.root.clipboard_clear()
            self.root.update()
        except Exception:
            pass

    def _ensure_driver(self, site_url):
        if self.driver is None:
            self.driver = webdriver.Chrome(options=self._make_options())
        if site_url:
            target = site_url if site_url.startswith(("http://", "https://")) else "https://" + site_url
            self.driver.get(target)
        return self.driver

    def _on_open_browser(self):
        try:
            self._ensure_driver(self.site_url.get().strip())
            self.status_label.config(text="브라우저가 열렸습니다. 원하는 쿠폰 사이트로 이동한 뒤 '시작'을 누르세요.")
        except Exception as e:
            messagebox.showerror("오류", f"브라우저를 열 수 없습니다: {e}")

    def _on_start(self):
        member_number = self.member_number.get().strip()
        coupons = [c.strip() for c in self.coupons.get("1.0", "end").splitlines() if c.strip()]
        site_url = self.site_url.get().strip()

        if not member_number:
            self.status_label.config(text="회원번호를 입력해주세요.")
            return
        if not coupons:
            self.status_label.config(text="쿠폰번호를 한 줄에 하나씩 입력해주세요.")
            return

        if len(coupons) > MAX_RECOMMENDED:
            proceed = messagebox.askyesno(
                "확인",
                f"한 번에 {len(coupons)}개는 차단 위험이 높습니다. {MAX_RECOMMENDED}개 이하로 나눠서 "
                f"진행하는 것을 권장합니다.\n\n그래도 계속하시겠습니까?",
            )
            if not proceed:
                return

        if not site_url and self.driver is None:
            self.status_label.config(text="사이트를 선택하거나, 먼저 '브라우저 열기'로 쿠폰 사이트를 열어주세요.")
            return

        self._persist_member_numbers()
        self._clear_clipboard()

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.status_label.config(text="준비 중...")

        try:
            self._ensure_driver(site_url)
        except Exception as e:
            self.status_label.config(text=f"브라우저를 열 수 없습니다: {e}")
            return

        self.stop_event = threading.Event()
        self._set_running(True)
        self.total = len(coupons)
        self.done_count = 0

        def on_event(kind, payload):
            self.event_queue.put((kind, payload))

        self.worker = threading.Thread(
            target=automation.run_batch,
            args=(self.driver, member_number, coupons, self.delay_min, self.delay_max, self.stop_event, on_event),
            daemon=True,
        )
        self.worker.start()

    def _on_stop(self):
        self.stop_event.set()
        self.status_label.config(text="중지 요청됨. 현재 항목 처리 후 멈춥니다.")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                if kind == "result":
                    self.done_count += 1
                    self._append_log(payload["code"], payload["status"], payload["message"])
                    self.status_label.config(text=f"진행 중: {self.done_count} / {self.total}")
                elif kind == "cooldown":
                    self.status_label.config(text=f"진행 중: {self.done_count} / {self.total} (안전을 위한 휴식 중...)")
                elif kind == "aborted":
                    self.status_label.config(text="중단됨: " + payload)
                elif kind == "done":
                    self._set_running(False)
                    if not self.status_label.cget("text").startswith("중단됨"):
                        self.status_label.config(text=f"완료: 총 {self.total}개 처리")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _on_close(self):
        self._persist_member_numbers()
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    RedeemApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
