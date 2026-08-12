import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

import automation

MAX_RECOMMENDED = 50
STATUS_TAGS = {"완료": "ok", "확인불가": "warn", "중단": "bad", "차단 의심": "bad"}


class RedeemApp:
    def __init__(self, root):
        self.root = root
        self.root.title("리딤코드 입력기")
        self.root.geometry("460x680")
        self.root.minsize(420, 560)

        self.driver = None
        self.stop_event = threading.Event()
        self.worker = None
        self.event_queue = queue.Queue()
        self.total = 0
        self.done_count = 0

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._poll_queue)

    def _build_ui(self):
        pad = {"padx": 10, "pady": (6, 0)}

        tk.Label(self.root, text="쿠폰 입력 사이트 주소 (선택)").pack(anchor="w", **pad)
        url_row = tk.Frame(self.root)
        url_row.pack(fill="x", padx=10)
        self.site_url = tk.Entry(url_row)
        self.site_url.pack(side="left", fill="x", expand=True)
        tk.Button(url_row, text="브라우저 열기", command=self._on_open_browser).pack(side="left", padx=(6, 0))

        tk.Label(self.root, text="회원번호 / 계정정보").pack(anchor="w", **pad)
        self.member_number = tk.Entry(self.root)
        self.member_number.pack(fill="x", padx=10)

        tk.Label(self.root, text="쿠폰번호 (한 줄에 하나씩)").pack(anchor="w", **pad)
        self.coupons = scrolledtext.ScrolledText(self.root, height=6)
        self.coupons.pack(fill="both", padx=10)

        delay_frame = tk.Frame(self.root)
        delay_frame.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(delay_frame, text="최소 딜레이(초)").grid(row=0, column=0, sticky="w")
        tk.Label(delay_frame, text="최대 딜레이(초)").grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.delay_min = tk.Entry(delay_frame, width=8)
        self.delay_min.insert(0, "4")
        self.delay_min.grid(row=1, column=0, sticky="w")
        self.delay_max = tk.Entry(delay_frame, width=8)
        self.delay_max.insert(0, "8")
        self.delay_max.grid(row=1, column=1, sticky="w", padx=(16, 0))

        tk.Label(
            self.root,
            text="차단 방지를 위해 최소 3초 이상 강제되며, 10개마다 자동으로 긴 휴식이 추가됩니다.",
            fg="#777", wraplength=430, justify="left",
        ).pack(anchor="w", padx=10, pady=(4, 0))

        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill="x", padx=10, pady=8)
        self.start_btn = tk.Button(btn_frame, text="시작", bg="#2563eb", fg="white", command=self._on_start)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.stop_btn = tk.Button(btn_frame, text="중지", bg="#e5484d", fg="white", command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.status_label = tk.Label(self.root, text="", fg="#333", anchor="w", justify="left", wraplength=430)
        self.status_label.pack(fill="x", padx=10, pady=(2, 0))

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
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
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

        try:
            delay_min = max(3.0, float(self.delay_min.get() or 4))
        except ValueError:
            delay_min = 4.0
        try:
            delay_max = float(self.delay_max.get() or 8)
        except ValueError:
            delay_max = 8.0
        if delay_max < delay_min:
            delay_max = delay_min

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
            self.status_label.config(text="사이트 주소를 입력하거나, 먼저 '브라우저 열기'로 쿠폰 사이트를 열어주세요.")
            return

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
            args=(self.driver, member_number, coupons, delay_min, delay_max, self.stop_event, on_event),
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
