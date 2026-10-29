"""Lightweight native desktop components inspired by the public TDS palette."""

import tkinter as tk
from tkinter import ttk

BLUE = "#3182F6"
BLUE_HOVER = "#2272EB"
BLUE_PALE = "#E8F3FF"
BG = "#F2F4F6"
WHITE = "#FFFFFF"
INK = "#191F28"
TEXT = "#333D4B"
MUTED = "#6B7684"
LINE = "#E5E8EB"
FONT = "맑은 고딕"


def rounded_shape(canvas, x1, y1, x2, y2, radius, **kwargs):
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class Card(tk.Frame):
    def __init__(self, parent, *, padding=22, radius=22, fill=WHITE, **kwargs):
        super().__init__(parent, bg=parent.cget("bg"), **kwargs)
        self.surface = tk.Canvas(self, highlightthickness=0, bd=0, bg=self.cget("bg"))
        self.surface.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=padding, pady=padding)
        self.bind("<Configure>", lambda e: self._draw(e.width, e.height, radius, fill), add="+")

    def _draw(self, width, height, radius, fill):
        self.surface.delete("all")
        if width > 2 and height > 2:
            rounded_shape(self.surface, 0, 0, width, height, radius, fill=fill, outline="")


class Button(tk.Canvas):
    """Rounded action button with native Tab, Enter/Space and disabled states."""

    def __init__(self, parent, *, text, command, variant="primary", width=150, height=48, state="normal"):
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"),
                         bd=0, highlightthickness=0, takefocus=state != "disabled")
        self._text, self._command, self._state = text, command, state
        self._variant = variant
        self._hover = self._focused = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Return>", self._keyboard_invoke)
        self.bind("<space>", self._keyboard_invoke)
        self.configure(cursor="hand2" if state != "disabled" else "")

    def _set_hover(self, value):
        self._hover = value
        self._draw()

    def _set_focus(self, value):
        self._focused = value
        self._draw()

    def _click(self, event):
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.focus_set()
            self.invoke()

    def _keyboard_invoke(self, _event):
        self.invoke()
        return "break"

    def invoke(self):
        if self._state != "disabled" and self._command:
            return self._command()

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
        for key, attr in (("text", "_text"), ("command", "_command"), ("state", "_state")):
            if key in kwargs:
                setattr(self, attr, kwargs.pop(key))
        if hasattr(self, "_state"):
            kwargs["takefocus"] = self._state != "disabled"
        result = super().configure(**kwargs)
        if hasattr(self, "_variant"):
            self._draw()
        return result

    config = configure

    def cget(self, key):
        if key in ("state", "text", "command"):
            return getattr(self, "_" + key)
        return super().cget(key)

    def _draw(self):
        self.delete("all")
        primary = self._variant == "primary"
        fill = (BLUE_HOVER if self._hover else BLUE) if primary else (LINE if self._hover else BG)
        color = WHITE if primary else TEXT
        if self._state == "disabled":
            fill, color = LINE, "#8B95A1"
        self.configure_cursor()
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 2 or height <= 2:
            return
        rounded_shape(self, 2, 2, width - 2, height - 2, 14, fill=fill,
                      outline=BLUE if self._focused else fill, width=2)
        if self._focused:
            rounded_shape(self, 0, 0, width, height, 16, fill="", outline=BLUE, width=2)
        self.create_text(width / 2, height / 2, text=self._text, fill=color, font=(FONT, 11, "bold"))

    def configure_cursor(self):
        super().configure(cursor="" if self._state == "disabled" else "hand2")


def label(parent, text=None, *, size=11, color=TEXT, bold=False, variable=None, **kwargs):
    return tk.Label(parent, text=text, textvariable=variable, bg=parent.cget("bg"), fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), anchor="w", **kwargs)


def build_workspace(app):
    app.configure(bg=BG)
    style = ttk.Style(app)
    style.theme_use("clam")
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=WHITE)
    style.configure("API.TFrame", background=WHITE)
    style.configure("Panel.TLabel", background=WHITE, foreground=MUTED, font=(FONT, 10))
    style.configure("API.TLabel", background=WHITE, foreground=MUTED, font=(FONT, 10))
    style.configure("TCheckbutton", background=WHITE, foreground=MUTED, font=(FONT, 9))
    style.map("TCheckbutton", background=[("active", WHITE)])
    style.configure("Input.TEntry", padding=(10, 9), fieldbackground=WHITE, foreground=INK,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    style.configure("TCombobox", padding=(10, 8), font=(FONT, 10), fieldbackground=BG,
                    foreground=TEXT, bordercolor=BG, arrowsize=14)
    style.map("TCombobox", fieldbackground=[("readonly", BG)], selectbackground=[("readonly", BG)],
              selectforeground=[("readonly", TEXT)], bordercolor=[("focus", BLUE)])
    style.configure("Treeview", font=(FONT, 10), rowheight=42, background=WHITE, fieldbackground=WHITE,
                    foreground=TEXT, borderwidth=0, relief="flat")
    style.configure("Treeview.Heading", font=(FONT, 9), background="#F9FAFB", foreground=MUTED,
                    padding=(10, 12), relief="flat", borderwidth=0)
    style.map("Treeview", background=[("selected", BLUE_PALE)], foreground=[("selected", "#1B64DA")])
    style.configure("OT.Horizontal.TProgressbar", troughcolor=BG, background=BLUE, bordercolor=BG,
                    lightcolor=BLUE, darkcolor=BLUE, thickness=6)
    style.configure("TScrollbar", troughcolor=WHITE, background=LINE, bordercolor=WHITE,
                    arrowcolor=MUTED, lightcolor=LINE, darkcolor=LINE, width=12)

    root = tk.Frame(app, bg=BG)
    root.pack(fill="both", expand=True)
    app.header = tk.Frame(root, bg=BG, padx=32, pady=18)
    app.header.pack(fill="x")
    brand = tk.Frame(app.header, bg=BG)
    brand.pack(side="left", anchor="n", pady=10)
    label(brand, "DART-OT", size=16, color=INK, bold=True).pack(anchor="w")
    label(brand, "공시로 확인하는 이자비용", size=10, color=MUTED).pack(anchor="w", pady=(5, 0))
    api_card = Card(app.header, padding=12, radius=16)
    api_card.configure(width=360, height=132)
    api_card.pack_propagate(False)
    api_card.pack(side="right")
    app._entry(api_card.body, "DART API 키", app.api_key_var, show="*", width=30, surface="API")
    ttk.Checkbutton(api_card.body, text="이 PC에 키 저장", variable=app.save_api_key_var).pack(anchor="w")

    app.content = tk.Frame(root, bg=BG, padx=32, pady=8)
    app.content.pack(fill="both", expand=True)
    app.setup_page = tk.Frame(app.content, bg=BG)
    app.setup_page.pack(fill="both", expand=True)
    label(app.setup_page, "어떤 회사를 검토할까요?", size=23, color=INK, bold=True).pack(anchor="w")
    label(app.setup_page, "회사와 조회 조건을 선택하면 공시 수집부터 이자비용 비교까지 한 번에 진행해요.",
          color=MUTED, size=10).pack(anchor="w", pady=(8, 20))
    body = tk.Frame(app.setup_page, bg=BG)
    body.pack(fill="both", expand=True, pady=(0, 14))
    body.columnconfigure(0, minsize=340)
    body.columnconfigure(1, weight=1)
    body.rowconfigure(0, weight=1)

    conditions = Card(body, padding=20)
    app.conditions_card = conditions
    conditions.configure(width=360)
    conditions.pack_propagate(False)
    conditions.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
    left = conditions.body
    label(left, "01  조회 조건", size=13, color=INK, bold=True).pack(anchor="w", pady=(0, 6))
    app._company_entry(left)
    app.search_button = Button(left, text="회사 검색", command=app.search_company, height=46)
    app.search_button.pack(fill="x", pady=(8, 16))
    years = tk.Frame(left, bg=WHITE)
    years.pack(fill="x")
    years.columnconfigure(0, weight=1)
    years.columnconfigure(1, weight=1)
    app._entry(years, "시작연도", app.begin_year_var, grid_col=0, width=9)
    app._entry(years, "종료연도", app.end_year_var, grid_col=1, width=9)
    label(left, "검토 기준", size=10, color=MUTED).pack(anchor="w", pady=(12, 6))
    combo = ttk.Combobox(left, state="readonly", textvariable=app.materiality_preset_display_var,
                         values=app.preset_labels, width=27)
    app.preset_combo = combo
    combo.pack(fill="x")
    combo.bind("<<ComboboxSelected>>", lambda _e: app._select_materiality_preset())

    companies = Card(body, padding=24)
    app.companies_card = companies
    companies.grid(row=0, column=1, sticky="nsew")
    right = companies.body
    label(right, "02  회사 선택", size=13, color=INK, bold=True).pack(anchor="w")
    label(right, "검색 결과에서 검토할 회사를 선택해 주세요.", color=MUTED, size=10).pack(anchor="w", pady=(8, 14))
    app.company_list_host = tk.Frame(right, bg=WHITE)
    app.company_list_host.pack(fill="both", expand=True)
    app.tree = ttk.Treeview(app.company_list_host, columns=("corp_name", "stock_code", "corp_code"),
                           show="headings", selectmode="browse", height=6)
    for key, title, width in (("corp_name", "회사명", 270), ("stock_code", "종목코드", 100), ("corp_code", "DART 고유번호", 135)):
        app.tree.heading(key, text=title)
        app.tree.column(key, width=width, minwidth=90, anchor="w", stretch=key == "corp_name")
    app.tree.bind("<<TreeviewSelect>>", app.select_company)
    app.empty_state = tk.Frame(app.company_list_host, bg=WHITE)
    app.empty_state.pack(fill="both", expand=True)
    empty_center = tk.Frame(app.empty_state, bg=WHITE)
    empty_center.place(relx=0.5, rely=0.46, anchor="center")
    badge = tk.Canvas(empty_center, width=60, height=60, bg=WHITE, bd=0, highlightthickness=0)
    badge.pack(pady=(0, 18))
    rounded_shape(badge, 0, 0, 60, 60, 20, fill=BLUE_PALE, outline="")
    badge.create_oval(18, 15, 38, 35, outline=BLUE, width=3)
    badge.create_line(35, 33, 44, 42, fill=BLUE, width=3)
    app.empty_title_var = tk.StringVar(value="먼저 회사를 검색해 주세요")
    label(empty_center, variable=app.empty_title_var, color=TEXT, size=13, bold=True).pack()
    label(empty_center, "회사명이나 6자리 종목코드로 찾을 수 있어요.", size=10, color=MUTED).pack(pady=(8, 0))
    company_actions = tk.Frame(right, bg=WHITE)
    company_actions.pack(side="bottom", fill="x", before=app.company_list_host, pady=(12, 0))
    label(company_actions, variable=app.status_var, color=MUTED, size=10, wraplength=650, justify="left").pack(fill="x", pady=(0, 12))
    app.export_button = Button(company_actions, text="선택한 회사 검토 시작", command=app.run_export,
                               height=50, state="normal" if app.selected_corp else "disabled")
    app.export_button.pack(fill="x")
    app._build_result_page(app.content)
    if app.selected_corp is not None:
        app._show_search_results([app.selected_corp])


def build_results(app, parent):
    app.result_page = tk.Frame(parent, bg=BG)
    top = tk.Frame(app.result_page, bg=BG)
    top.pack(fill="x", pady=(4, 10))
    app.result_back_button = Button(top, text="← 조회 조건", command=app._show_setup_page, variant="secondary", width=135, height=42)
    app.result_back_button.pack(side="left")
    label(top, "DART-OT", size=12, color=MUTED, bold=True).pack(side="right")
    app.result_title_var = tk.StringVar(value="검토 결과")
    label(app.result_page, variable=app.result_title_var, color=INK, size=23, bold=True).pack(fill="x", pady=(0, 16))

    progress_card = Card(app.result_page, padding=12, radius=18)
    progress_card.pack(fill="x", pady=(0, 16))
    app.result_status_var = tk.StringVar(value="공시를 확인하고 있어요.")
    label(progress_card.body, variable=app.result_status_var, color=MUTED, size=10, wraplength=1000, justify="left").pack(fill="x")
    app.result_progress = ttk.Progressbar(progress_card.body, style="OT.Horizontal.TProgressbar", maximum=100)
    app.result_progress.pack(fill="x", pady=(10, 0))
    metrics = tk.Frame(app.result_page, bg=BG)
    metrics.pack(fill="x", pady=(0, 16))
    app.result_metric_vars = {}
    for column, (key, title) in enumerate((("reports", "정기보고서"), ("tests", "검토한 기간"), ("within", "기준 이내"), ("review", "추가 확인"), ("issues", "추출 이슈"))):
        metrics.columnconfigure(column, weight=1, uniform="metrics")
        card = Card(metrics, padding=12, radius=18)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0 if column == 4 else 6))
        value = tk.StringVar(value="-")
        app.result_metric_vars[key] = value
        label(card.body, title, color=MUTED, size=9).pack(anchor="w")
        label(card.body, variable=value, color=BLUE if key == "within" else INK, size=22, bold=True).pack(anchor="w", pady=(5, 0))

    detail_card = Card(app.result_page, padding=18)
    detail_card.pack(fill="both", expand=True)
    detail = detail_card.body
    detail.columnconfigure(0, weight=1)
    detail.rowconfigure(2, weight=1)
    label(detail, "기간별 검토 결과", color=INK, size=13, bold=True).grid(row=0, column=0, sticky="w")
    app.result_summary_var = tk.StringVar(value="검토가 끝나면 이자비용과 차이를 확인할 수 있어요.")
    label(detail, variable=app.result_summary_var, color=MUTED, size=9, wraplength=1000, justify="left").grid(row=1, column=0, sticky="ew", pady=(8, 14))
    columns = ("report", "judgment", "expected", "actual", "difference", "threshold", "caution", "caution_reason")
    app.result_tree = ttk.Treeview(detail, columns=columns, show="headings", height=5)
    for key, title, width in (("report", "보고서", 190), ("judgment", "판정", 130), ("expected", "계산 이자", 100),
                              ("actual", "실제 이자", 100), ("difference", "차이", 100), ("threshold", "허용차이", 100),
                              ("caution", "주의", 90), ("caution_reason", "주의요망 사유", 300)):
        app.result_tree.heading(key, text=title)
        app.result_tree.column(key, width=width, minwidth=85, anchor="w" if key in ("report", "caution_reason") else "center")
    app.result_tree.tag_configure("within", foreground="#1B64DA")
    app.result_tree.tag_configure("review", foreground="#B45309")
    app.result_tree.grid(row=2, column=0, sticky="nsew")
    horizontal = ttk.Scrollbar(detail, orient="horizontal", command=app.result_tree.xview)
    horizontal.grid(row=3, column=0, sticky="ew")
    vertical = ttk.Scrollbar(detail, orient="vertical", command=app.result_tree.yview)
    vertical.grid(row=2, column=1, sticky="ns")
    app.result_tree.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
    actions = tk.Frame(app.result_page, bg=BG)
    actions.pack(side="bottom", fill="x", pady=(16, 10), before=detail_card)
    label(actions, "계산 근거와 원문은 엑셀에 함께 담았어요.", color=MUTED, size=10).pack(side="left")
    app.result_open_button = Button(actions, text="결과 엑셀 열기", command=app.open_output, width=170, height=48, state="disabled")
    app.result_open_button.pack(side="right")
    Button(actions, text="저장 폴더 열기", command=app.open_output_dir, variant="secondary", width=150, height=48).pack(side="right", padx=10)
