"""Tkinter desktop UI for selecting files and saving datalog reports."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from datalog_checker.config import (
    default_check_settings,
    parse_gain_threshold,
    parse_temperature_tolerance,
)
from datalog_checker.coverage import CoverageResult, scan_coverage_file
from datalog_checker.coverage_config import (
    TestNameMapping,
    load_temperature_measurement_names,
    load_test_name_mapping,
)
from datalog_checker.core import (
    NegativeGain,
    PortFailure,
    TemperatureFailure,
    flagged_dut_names,
    scan_file,
    scan_temperature_file,
    summarize_port_failures,
    write_csv_report,
    write_markdown_report,
)


QUALCOMM_RED = "#D71920"
QUALCOMM_RED_DARK = "#A91218"
QUALCOMM_RED_LIGHT = "#FDEBEC"
CHARCOAL = "#263238"
MUTED_TEXT = "#667085"
APP_BACKGROUND = "#F4F6F8"
PANEL_BACKGROUND = "#FFFFFF"
BORDER = "#D9DEE5"
DISABLED = "#B7BEC2"
WHITE = "#FFFFFF"


def resource_path(relative_path: str) -> Path:
    """Resolve a bundled resource in source and PyInstaller builds."""
    if getattr(sys, "frozen", False):
        base_path = Path(getattr(sys, "_MEIPASS"))
    else:
        base_path = Path(__file__).resolve().parent.parent
    return base_path / relative_path


class RoundedButton(tk.Canvas):
    """A compact, theme-independent rounded button for the primary action."""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: object,
        **kwargs: object,
    ) -> None:
        self._button_text = text
        self._command = command
        self._button_state = "normal"
        self._hovered = False
        self._button_width = 232
        self._button_height = 44
        super().__init__(
            master,
            width=self._button_width,
            height=self._button_height,
            background=APP_BACKGROUND,
            borderwidth=0,
            highlightthickness=0,
            **kwargs,
        )
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Return>", self._on_click)
        self.bind("<space>", self._on_click)
        self._draw()

    def configure(self, cnf: dict[str, object] | None = None, **kwargs: object):
        state = kwargs.pop("state", None)
        if state is not None:
            self._button_state = str(state)
            self._hovered = False
            self._draw()
        return super().configure(cnf, **kwargs)

    config = configure

    def _on_enter(self, _event: tk.Event) -> None:
        if self._button_state == "normal":
            self._hovered = True
            self._draw()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._hovered:
            self._hovered = False
            self._draw()

    def _on_click(self, _event: tk.Event) -> str:
        if self._button_state == "normal" and callable(self._command):
            self.focus_set()
            self._command()
        return "break"

    def _draw(self) -> None:
        self.delete("all")
        if self._button_state == "disabled":
            fill = DISABLED
            text_color = WHITE
        elif self._hovered:
            fill = QUALCOMM_RED
            text_color = WHITE
        else:
            fill = QUALCOMM_RED_DARK
            text_color = WHITE

        left, top = 2, 2
        right, bottom = self._button_width - 2, self._button_height - 2
        radius = 12
        self.create_rectangle(
            left + radius,
            top,
            right - radius,
            bottom,
            fill=fill,
            outline=fill,
        )
        self.create_rectangle(
            left,
            top + radius,
            right,
            bottom - radius,
            fill=fill,
            outline=fill,
        )
        for x1, y1 in (
            (left, top),
            (right - 2 * radius, top),
            (left, bottom - 2 * radius),
            (right - 2 * radius, bottom - 2 * radius),
        ):
            self.create_oval(
                x1,
                y1,
                x1 + 2 * radius,
                y1 + 2 * radius,
                fill=fill,
                outline=fill,
            )
        self.create_text(
            self._button_width / 2,
            self._button_height / 2,
            text=self._button_text,
            fill=text_color,
            font=("Segoe UI", 10, "bold"),
        )


class DatalogCheckerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Datalog Checker")
        self.geometry("920x600")
        self.minsize(700, 460)
        self.configure(background=APP_BACKGROUND)
        self._configure_styles()
        self._set_application_icon()

        self.check_settings = default_check_settings()
        self.selected_files: list[Path] = []
        self.check_vars: dict[str, tk.BooleanVar] = {}
        self.gain_threshold_var = tk.StringVar(
            value=f"{self.check_settings['Gain'].threshold:g}"
        )
        self.gain_threshold_entry: ttk.Entry | None = None
        self.temperature_strict_var = tk.BooleanVar(
            value=self.check_settings["Temperature"].strict
        )
        self.temperature_tolerance_var = tk.StringVar(
            value=f"{self.check_settings['Temperature'].tolerance:g}"
        )
        self.temperature_strict_button: ttk.Checkbutton | None = None
        self.temperature_tolerance_entry: ttk.Entry | None = None
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.run_button: RoundedButton
        self.status_var = tk.StringVar(value="Add one or more CSV files to begin.")
        self.count_var = tk.StringVar(value="0 files selected")

        self._build_ui()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TFrame", background=APP_BACKGROUND)
        style.configure("TLabel", background=APP_BACKGROUND, foreground=CHARCOAL)
        style.configure(
            "Muted.TLabel",
            background=APP_BACKGROUND,
            foreground=MUTED_TEXT,
        )
        style.configure(
            "TLabelframe",
            background=APP_BACKGROUND,
            bordercolor=BORDER,
        )
        style.configure(
            "TLabelframe.Label",
            background=APP_BACKGROUND,
            foreground=CHARCOAL,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure("TCheckbutton", background=APP_BACKGROUND, foreground=CHARCOAL)
        style.configure("TEntry", padding=(7, 5), fieldbackground=PANEL_BACKGROUND)
        style.configure("TButton", padding=(10, 6))
        style.configure(
            "Help.TButton",
            foreground=QUALCOMM_RED,
            font=("Segoe UI", 9, "bold"),
            padding=(2, 1),
        )

    def _set_application_icon(self) -> None:
        try:
            self.iconbitmap(str(resource_path("assets/datalog_checker.ico")))
        except tk.TclError:
            pass

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        header = tk.Frame(outer, background=QUALCOMM_RED, padx=18, pady=14)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="Datalog Checker",
            background=QUALCOMM_RED,
            foreground=WHITE,
            font=("Segoe UI", 17, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text="Verify gain, temperature, and coverage results from datalogs.",
            background=QUALCOMM_RED,
            foreground=QUALCOMM_RED_LIGHT,
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        checks_area = ttk.LabelFrame(outer, text="Checks", padding=8)
        checks_area.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        checks_area.columnconfigure(0, weight=1)
        ttk.Label(
            checks_area,
            text="Select checks to enable and configure their parameters.",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        check_list_container = ttk.Frame(checks_area)
        check_list_container.grid(row=1, column=0, sticky="ew")
        check_list_container.columnconfigure(0, weight=1)
        check_canvas = tk.Canvas(
            check_list_container,
            height=100,
            background=APP_BACKGROUND,
            borderwidth=0,
            highlightthickness=0,
        )
        check_scrollbar = ttk.Scrollbar(
            check_list_container,
            orient="vertical",
            command=check_canvas.yview,
        )
        check_canvas.configure(yscrollcommand=check_scrollbar.set)
        check_canvas.grid(row=0, column=0, sticky="ew")
        check_scrollbar.grid(row=0, column=1, sticky="ns")
        check_rows = ttk.Frame(check_canvas)
        check_rows.columnconfigure(0, weight=1)
        check_rows.columnconfigure(1, minsize=72)
        check_rows.columnconfigure(2, minsize=180)
        check_rows.columnconfigure(3, minsize=110)
        check_window = check_canvas.create_window(
            (0, 0), window=check_rows, anchor="nw"
        )
        check_rows.bind(
            "<Configure>",
            lambda _event: check_canvas.configure(
                scrollregion=check_canvas.bbox("all")
            ),
        )
        check_canvas.bind(
            "<Configure>",
            lambda event: check_canvas.itemconfigure(
                check_window, width=event.width
            ),
        )

        if self.check_settings:
            for row_number, (name, check_settings) in enumerate(
                self.check_settings.items()
            ):
                check_var = tk.BooleanVar(value=check_settings.enabled)
                self.check_vars[name] = check_var
                ttk.Checkbutton(
                    check_rows,
                    text=name,
                    variable=check_var,
                    command=(
                        self.update_gain_threshold_state
                        if name == "Gain"
                        else self.update_temperature_options_state
                        if name == "Temperature"
                        else None
                    ),
                ).grid(row=row_number, column=0, sticky="w", pady=1)

                if name == "Gain":
                    ttk.Label(
                        check_rows,
                        text="Low gain threshold (dB):",
                    ).grid(row=row_number, column=2, sticky="e", padx=(8, 8), pady=1)
                    self.gain_threshold_entry = ttk.Entry(
                        check_rows,
                        textvariable=self.gain_threshold_var,
                        width=10,
                    )
                    self.gain_threshold_entry.grid(
                        row=row_number, column=3, sticky="w", pady=1
                    )
                elif name == "Temperature":
                    strict_frame = ttk.Frame(check_rows)
                    strict_frame.grid(
                        row=row_number,
                        column=1,
                        sticky="w",
                        padx=(8, 8),
                        pady=1,
                    )
                    self.temperature_strict_button = ttk.Checkbutton(
                        strict_frame,
                        text="Strict",
                        variable=self.temperature_strict_var,
                    )
                    self.temperature_strict_button.pack(side="left")
                    ttk.Button(
                        strict_frame,
                        text="?",
                        width=2,
                        style="Help.TButton",
                        command=self.show_temperature_strict_help,
                    ).pack(side="left", padx=(5, 0))
                    tolerance_frame = ttk.Frame(check_rows)
                    tolerance_frame.grid(
                        row=row_number, column=2, sticky="e", padx=(8, 8), pady=1
                    )
                    tolerance_frame.grid_remove()
                    ttk.Label(
                        check_rows,
                        text="Tolerance (deg C):",
                    ).grid(row=row_number, column=2, sticky="e", padx=(8, 8), pady=1)
                    ttk.Label(tolerance_frame, text="Tolerance (°C):").pack(
                        side="left"
                    )
                    self.temperature_tolerance_entry = ttk.Entry(
                        check_rows,
                        textvariable=self.temperature_tolerance_var,
                        width=10,
                    )
                    self.temperature_tolerance_entry.grid(
                        row=row_number, column=3, sticky="w", pady=1
                    )
        else:
            ttk.Label(check_rows, text="No checks configured.").grid(
                row=0, column=0, sticky="w"
            )

        if self.gain_threshold_entry is not None:
            self.update_gain_threshold_state()
        if self.temperature_tolerance_entry is not None:
            self.update_temperature_options_state()

        file_area = ttk.LabelFrame(outer, text="Datalog files", padding=8)
        file_area.grid(row=2, column=0, sticky="nsew")
        file_area.columnconfigure(0, weight=1)
        file_area.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(file_area)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(toolbar, text="Add CSV files...", command=self.add_files).pack(side="left")
        ttk.Button(toolbar, text="Clear all", command=self.clear_files).pack(side="left", padx=(8, 0))
        ttk.Label(toolbar, textvariable=self.count_var).pack(side="right")

        list_container = ttk.Frame(file_area)
        list_container.grid(row=1, column=0, sticky="nsew")
        list_container.columnconfigure(0, weight=1)
        list_container.columnconfigure(1, weight=0, minsize=40)
        list_container.columnconfigure(2, weight=0)
        list_container.rowconfigure(0, weight=1)

        self.file_canvas = tk.Canvas(list_container, highlightthickness=0)
        self.remove_canvas = tk.Canvas(
            list_container, width=40, highlightthickness=0
        )
        self.file_canvas.configure(background=PANEL_BACKGROUND, borderwidth=0)
        self.remove_canvas.configure(background=PANEL_BACKGROUND, borderwidth=0)
        vertical_scrollbar = ttk.Scrollbar(
            list_container, orient="vertical", command=self.scroll_vertical
        )
        horizontal_scrollbar = ttk.Scrollbar(
            list_container, orient="horizontal", command=self.file_canvas.xview
        )
        self.file_canvas.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        self.remove_canvas.configure(yscrollcommand=lambda *_args: None)
        self.file_canvas.grid(row=0, column=0, sticky="nsew")
        self.remove_canvas.grid(row=0, column=1, sticky="ns")
        vertical_scrollbar.grid(row=0, column=2, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        self.file_rows = ttk.Frame(self.file_canvas)
        self.file_rows.columnconfigure(0, weight=1)
        self.file_window = self.file_canvas.create_window(
            (0, 0), window=self.file_rows, anchor="nw"
        )
        self.remove_rows = ttk.Frame(self.remove_canvas)
        self.remove_rows.columnconfigure(0, weight=1)
        self.remove_window = self.remove_canvas.create_window(
            (0, 0), window=self.remove_rows, anchor="nw"
        )
        self.file_rows.bind(
            "<Configure>",
            lambda _event: self.file_canvas.configure(
                scrollregion=self.file_canvas.bbox("all")
            ),
        )
        self.file_canvas.bind("<Configure>", self.resize_file_rows)
        self.remove_rows.bind(
            "<Configure>",
            lambda _event: self.update_file_scroll_region(),
        )
        self.remove_canvas.bind("<Configure>", self.resize_remove_rows)
        self.file_canvas.bind_all("<MouseWheel>", self.mousewheel)

        bottom = ttk.Frame(outer)
        bottom.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(
            bottom,
            textvariable=self.status_var,
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.run_button = RoundedButton(
            bottom,
            text="Run scan and save reports",
            command=self.run_scan,
            cursor="hand2",
        )
        self.run_button.grid(row=0, column=1, sticky="e")
        self.refresh_file_list()

    def update_gain_threshold_state(self) -> None:
        if self.gain_threshold_entry is None:
            return
        gain_check = self.check_vars.get("Gain")
        state = "normal" if gain_check is not None and gain_check.get() else "disabled"
        self.gain_threshold_entry.configure(state=state)

    def update_temperature_options_state(self) -> None:
        temperature_check = self.check_vars.get("Temperature")
        state = (
            "normal"
            if temperature_check is not None and temperature_check.get()
            else "disabled"
        )
        if self.temperature_strict_button is not None:
            self.temperature_strict_button.configure(state=state)
        if self.temperature_tolerance_entry is not None:
            self.temperature_tolerance_entry.configure(state=state)

    def show_temperature_strict_help(self) -> None:
        messagebox.showinfo(
            "Strict temperature mode",
            "Strict requires every temperature reading to pass. "
            "When Strict is off, at least one reading must pass.",
            parent=self,
        )

    def add_files(self) -> None:
        filenames = filedialog.askopenfilenames(
            parent=self,
            title="Select CSV files",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        existing = {path.resolve() for path in self.selected_files}
        for filename in filenames:
            path = Path(filename)
            resolved = path.resolve()
            if resolved not in existing:
                self.selected_files.append(path)
                existing.add(resolved)
        self.refresh_file_list()

    def remove_file(self, path: Path) -> None:
        self.selected_files = [selected for selected in self.selected_files if selected != path]
        self.refresh_file_list()

    def clear_files(self) -> None:
        self.selected_files.clear()
        self.refresh_file_list()

    def refresh_file_list(self) -> None:
        for child in self.file_rows.winfo_children():
            child.destroy()
        for child in self.remove_rows.winfo_children():
            child.destroy()

        for row_number, path in enumerate(self.selected_files):
            file_row = ttk.Frame(self.file_rows, padding=(2, 2))
            file_row.grid(row=row_number, column=0, sticky="ew")
            ttk.Label(file_row, text=str(path), anchor="w").grid(
                row=0, column=0, sticky="w", padx=(2, 8)
            )
            remove_row = ttk.Frame(self.remove_rows, padding=(2, 2))
            remove_row.grid(row=row_number, column=0, sticky="ew")
            ttk.Button(
                remove_row,
                text="-",
                width=3,
                command=lambda selected=path: self.remove_file(selected),
            ).grid(row=0, column=0, sticky="ew")

        self.count_var.set(f"{len(self.selected_files)} file(s) selected")
        self.after_idle(self.update_file_scroll_region)

    def resize_file_rows(self, event: tk.Event) -> None:
        self.file_rows.update_idletasks()
        requested_width = self.file_rows.winfo_reqwidth()
        self.file_canvas.itemconfigure(
            self.file_window,
            width=max(event.width, requested_width),
        )
        self.file_canvas.configure(scrollregion=self.file_canvas.bbox("all"))

    def resize_remove_rows(self, event: tk.Event) -> None:
        self.remove_canvas.itemconfigure(self.remove_window, width=event.width)
        self.remove_canvas.configure(scrollregion=self.remove_canvas.bbox("all"))

    def scroll_vertical(self, *args: object) -> None:
        self.file_canvas.yview(*args)
        self.remove_canvas.yview(*args)

    def mousewheel(self, event: tk.Event) -> str:
        units = -int(event.delta / 120)
        if event.state & 0x0001:
            self.file_canvas.xview_scroll(units, "units")
        else:
            self.file_canvas.yview_scroll(units, "units")
            self.remove_canvas.yview_moveto(self.file_canvas.yview()[0])
        return "break"

    def update_file_scroll_region(self) -> None:
        self.file_rows.update_idletasks()
        self.file_canvas.update_idletasks()
        canvas_width = self.file_canvas.winfo_width()
        requested_width = self.file_rows.winfo_reqwidth()
        self.file_canvas.itemconfigure(
            self.file_window,
            width=max(canvas_width, requested_width),
        )
        self.remove_canvas.itemconfigure(
            self.remove_window,
            width=max(self.remove_canvas.winfo_width(), self.remove_rows.winfo_reqwidth()),
        )
        self.file_canvas.configure(scrollregion=self.file_canvas.bbox("all"))
        self.remove_canvas.configure(scrollregion=self.remove_canvas.bbox("all"))
        self.remove_canvas.yview_moveto(self.file_canvas.yview()[0])

    def run_scan(self) -> None:
        if not self.selected_files:
            messagebox.showwarning(
                "No files selected",
                "Add at least one CSV file before running the scan.",
                parent=self,
            )
            return

        try:
            gain_threshold = parse_gain_threshold(self.gain_threshold_var.get())
        except ValueError as error:
            messagebox.showerror("Invalid gain threshold", str(error), parent=self)
            return

        temperature_enabled = self.check_vars["Temperature"].get()
        try:
            temperature_tolerance = parse_temperature_tolerance(
                self.temperature_tolerance_var.get()
            )
        except ValueError as error:
            messagebox.showerror(
                "Invalid temperature tolerance",
                str(error),
                parent=self,
            )
            return

        coverage_enabled = self.check_vars["Coverage"].get()
        coverage_mapping: TestNameMapping | None = None
        temperature_measurement_names: tuple[str, ...] | None = None
        if coverage_enabled or temperature_enabled:
            try:
                if coverage_enabled:
                    coverage_mapping = load_test_name_mapping()
                if temperature_enabled:
                    temperature_measurement_names = load_temperature_measurement_names()
            except (OSError, ValueError) as error:
                messagebox.showerror(
                    "Invalid runtime configuration",
                    str(error),
                    parent=self,
                )
                return

        self.run_button.configure(state="disabled")
        self.status_var.set("Scanning files...")
        paths = tuple(self.selected_files)
        threading.Thread(
            target=self.scan_in_background,
            args=(
                paths,
                self.check_vars["Gain"].get(),
                gain_threshold,
                temperature_enabled,
                temperature_tolerance,
                self.temperature_strict_var.get(),
                temperature_measurement_names,
                coverage_enabled,
                coverage_mapping,
            ),
            daemon=True,
        ).start()
        self.after(100, self.check_scan_result)

    def scan_in_background(
        self,
        paths: tuple[Path, ...],
        gain_enabled: bool,
        gain_threshold: float,
        temperature_enabled: bool,
        temperature_tolerance: float,
        temperature_strict: bool,
        temperature_measurement_names: tuple[str, ...] | None,
        coverage_enabled: bool,
        coverage_mapping: TestNameMapping | None,
    ) -> None:
        results: list[NegativeGain] = []
        temperature_failures: list[TemperatureFailure] = []
        coverage_results: list[CoverageResult] = []
        scanned_files: list[Path] = []
        errors: list[str] = []

        for path in paths:
            try:
                if gain_enabled:
                    results.extend(scan_file(path, str(path), gain_threshold))
                if temperature_enabled:
                    temperature_failures.extend(
                        scan_temperature_file(
                            path,
                            str(path),
                            temperature_tolerance,
                            temperature_strict,
                            temperature_measurement_names,
                        )
                    )
                if coverage_enabled and coverage_mapping is not None:
                    coverage_results.append(
                        scan_coverage_file(path, str(path), coverage_mapping)
                    )
                scanned_files.append(path)
            except (OSError, ValueError) as error:
                errors.append(f"`{path}`: {error}")

        results.sort(key=lambda result: (result.dut_sn, result.csv_file, result.line_number))
        failures = summarize_port_failures(results)
        self.result_queue.put(
            (
                "done",
                (
                    scanned_files,
                    results,
                    failures,
                    temperature_failures,
                    coverage_results,
                    errors,
                ),
            )
        )

    def check_scan_result(self) -> None:
        try:
            message_type, payload = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self.check_scan_result)
            return

        if message_type == "done":
            (
                scanned_files,
                results,
                failures,
                temperature_failures,
                coverage_results,
                errors,
            ) = payload  # type: ignore[misc]
            self.save_reports(
                scanned_files,
                results,
                failures,
                temperature_failures,
                coverage_results,
                errors,
            )

    def save_reports(
        self,
        scanned_files: list[Path],
        results: list[NegativeGain],
        failures: list[PortFailure],
        temperature_failures: list[TemperatureFailure],
        coverage_results: list[CoverageResult],
        errors: list[str],
    ) -> None:
        self.run_button.configure(state="normal")
        output_directory = filedialog.askdirectory(
            parent=self,
            title="Choose a folder for the reports",
            mustexist=True,
        )
        if not output_directory:
            self.status_var.set("Scan finished. Report save cancelled.")
            return

        output_path = Path(output_directory)
        markdown_path = output_path / "datalog_report.md"
        csv_path = output_path / "datalog_report.csv"
        try:
            write_markdown_report(
                markdown_path,
                Path("GUI file selection"),
                scanned_files,
                results,
                failures,
                errors,
                temperature_failures,
                coverage_results,
            )
            write_csv_report(
                csv_path,
                failures,
                temperature_failures,
                coverage_results,
            )
        except OSError as error:
            self.status_var.set("Could not save the reports.")
            messagebox.showerror("Save failed", str(error), parent=self)
            return

        self.status_var.set(f"Reports saved to {output_path}")
        summary = (
            f"Scanned files: {len(scanned_files)}\n"
            f"Low gain rows: {len(results)}\n"
            f"DUTs flagged: "
            f"{len(flagged_dut_names(results, temperature_failures, coverage_results))}\n"
            f"Input port failure rows: {len(failures)}\n\n"
            f"Temperature failure cases: {len(temperature_failures)}\n\n"
            f"Coverage failure cases: "
            f"{sum(bool(result.gaps) for result in coverage_results)}\n\n"
            f"Saved:\n{markdown_path}\n{csv_path}"
        )
        if errors:
            summary += f"\n\nFiles with errors: {len(errors)}"
        messagebox.showinfo("Scan complete", summary, parent=self)


def main() -> None:
    app = DatalogCheckerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
