#!/usr/bin/env python3
"""Simple desktop UI for selecting files and saving gain retest reports."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from gain_retest_checker import (
    NegativeGain,
    PortFailure,
    scan_file,
    summarize_port_failures,
    write_csv_report,
    write_markdown_report,
)


class GainRetestApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Gain Retest Checker")
        self.geometry("900x560")
        self.minsize(680, 420)

        self.selected_files: list[Path] = []
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.run_button: ttk.Button
        self.status_var = tk.StringVar(value="Add one or more CSV files to begin.")
        self.count_var = tk.StringVar(value="0 files selected")

        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        ttk.Label(
            outer,
            text="Gain Retest Checker",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            outer,
            text="Select CSV files, run the scan, and choose where to save the reports.",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        file_area = ttk.LabelFrame(outer, text="CSV files", padding=8)
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
        self.file_canvas.bind(
            "<Configure>",
            self.resize_file_rows,
        )
        self.remove_rows.bind(
            "<Configure>",
            lambda _event: self.update_file_scroll_region(),
        )
        self.remove_canvas.bind(
            "<Configure>",
            self.resize_remove_rows,
        )
        self.file_canvas.bind_all("<MouseWheel>", self.mousewheel)

        bottom = ttk.Frame(outer)
        bottom.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.run_button = ttk.Button(
            bottom,
            text="Run scan and save reports",
            command=self.run_scan,
        )
        self.run_button.grid(row=0, column=1, sticky="e")

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

        self.run_button.configure(state="disabled")
        self.status_var.set("Scanning files...")
        paths = tuple(self.selected_files)
        threading.Thread(target=self.scan_in_background, args=(paths,), daemon=True).start()
        self.after(100, self.check_scan_result)

    def scan_in_background(self, paths: tuple[Path, ...]) -> None:
        results: list[NegativeGain] = []
        scanned_files: list[Path] = []
        errors: list[str] = []

        for path in paths:
            try:
                results.extend(scan_file(path, str(path)))
                scanned_files.append(path)
            except (OSError, ValueError) as error:
                errors.append(f"`{path}`: {error}")

        results.sort(key=lambda result: (result.dut_sn, result.csv_file, result.line_number))
        failures = summarize_port_failures(results)
        self.result_queue.put(("done", (scanned_files, results, failures, errors)))

    def check_scan_result(self) -> None:
        try:
            message_type, payload = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self.check_scan_result)
            return

        if message_type == "done":
            scanned_files, results, failures, errors = payload  # type: ignore[misc]
            self.save_reports(scanned_files, results, failures, errors)

    def save_reports(
        self,
        scanned_files: list[Path],
        results: list[NegativeGain],
        failures: list[PortFailure],
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
        markdown_path = output_path / "gain_retest_report.md"
        csv_path = output_path / "gain_retest_report.csv"
        try:
            write_markdown_report(
                markdown_path,
                Path("GUI file selection"),
                scanned_files,
                results,
                failures,
                errors,
            )
            write_csv_report(csv_path, failures)
        except OSError as error:
            self.status_var.set("Could not save the reports.")
            messagebox.showerror("Save failed", str(error), parent=self)
            return

        self.status_var.set(f"Reports saved to {output_path}")
        summary = (
            f"Scanned files: {len(scanned_files)}\n"
            f"Negative gain rows: {len(results)}\n"
            f"DUTs requiring retest: {len({result.dut_sn for result in results})}\n"
            f"Input port failure rows: {len(failures)}\n\n"
            f"Saved:\n{markdown_path}\n{csv_path}"
        )
        if errors:
            summary += f"\n\nFiles with errors: {len(errors)}"
        messagebox.showinfo("Scan complete", summary, parent=self)


if __name__ == "__main__":
    app = GainRetestApp()
    app.mainloop()
