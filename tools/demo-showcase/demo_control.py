#!/usr/bin/env python3
"""Desktop editor for the existing synthetic ReShow demo showcase."""
import argparse
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from demo_ui_service import DemoEditor, EXHIBITS


class DemoWindow:
    def __init__(self, root, args):
        self.root, self.editor, self.signature = root, None, None
        self.events, self.closed, self.busy = queue.Queue(), False, False
        self.profile_version = self.label_version = None
        self.actions = []
        root.title("ReShow · Live demo editor")
        root.geometry("940x900")
        root.minsize(760, 820)
        root.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ReShow live demo editor", font=("TkDefaultFont", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Synthetic AWS data · demo-test-case · demo-test-device").pack(anchor="w", pady=(2, 10))
        self.producer_path = tk.StringVar(value=args.producer_access or "")
        self.admin_path = tk.StringVar(value=args.admin_tokens or "")
        self.plans_path = tk.StringVar(value=str(args.plans_dir))
        connection = ttk.LabelFrame(frame, text="Private local files", padding=8)
        connection.pack(fill="x")
        self.path_row(connection, 0, "Producer access", self.producer_path, False)
        self.path_row(connection, 1, "Administrator tokens", self.admin_path, False)
        self.path_row(connection, 2, "Saved upload plans", self.plans_path, True)
        selection = ttk.Frame(frame)
        selection.pack(fill="x", pady=10)
        ttk.Label(selection, text="Exhibit").pack(side="left")
        self.selected = tk.StringVar(value=EXHIBITS[0])
        self.selector = ttk.Combobox(selection, textvariable=self.selected, values=EXHIBITS, state="readonly", width=28)
        self.selector.pack(side="left", padx=8)
        self.selector.bind("<<ComboboxSelected>>", self.selection_changed)
        self.load_button = self.button(selection, "Load current data", self.load_current)
        self.load_button.pack(side="left")
        self.current = tk.StringVar(value="Choose the private files. Loading current data needs Administrator tokens.")
        ttk.Label(frame, textvariable=self.current, wraplength=840).pack(anchor="w", pady=(0, 8))
        # Reserve feedback before allocating the notebook's remaining height.
        feedback = ttk.Frame(frame)
        feedback.pack(side="bottom", fill="x", pady=(10, 0))
        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill="both", expand=True)
        self.sensor_tab, self.profile_tab, self.label_tab = [ttk.Frame(self.tabs, padding=12) for _ in range(3)]
        for tab, name in ((self.sensor_tab, "Sensor reading"), (self.profile_tab, "Exhibit profile"), (self.label_tab, "Label draft")):
            self.tabs.add(tab, text=name)
        self.temperature, self.temperature_entry = self.field(self.sensor_tab, 0, "Temperature °C", "23.5")
        self.brightness = self.field(self.sensor_tab, 1, "Brightness ADC (0–4095)", "1800")[0]
        self.distance = self.field(self.sensor_tab, 2, "Distance cm (optional)", "")[0]
        self.occupied = tk.BooleanVar(value=False)
        self.occupied_control = ttk.Checkbutton(self.sensor_tab, text="Occupied", variable=self.occupied)
        self.occupied_control.grid(row=3, column=1, sticky="w", pady=6)
        ttk.Label(self.sensor_tab, text="Each send appends a reading and status. Retry uses the saved values, even if these fields change.", wraplength=720).grid(row=4, column=0, columnspan=2, sticky="w", pady=8)
        sensor_actions = ttk.Frame(self.sensor_tab)
        sensor_actions.grid(row=5, column=0, columnspan=2, sticky="w", pady=8)
        self.button(sensor_actions, "Send live reading", self.send_reading).pack(side="left")
        self.retry_button = self.button(sensor_actions, "Retry last upload", self.retry_upload)
        self.retry_button.pack(side="left", padx=8)
        self.button(sensor_actions, "Open saved upload…", self.open_upload).pack(side="left")
        self.plan_text = tk.StringVar(value="No upload plan yet.")
        ttk.Label(self.sensor_tab, textvariable=self.plan_text, wraplength=740).grid(row=6, column=0, columnspan=2, sticky="w", pady=8)
        self.profile = {}
        profile_fields = (("name", "Name"), ("collection", "Collection"), ("description", "Description"),
            ("temperature_min_c", "Minimum temperature °C"), ("temperature_max_c", "Maximum temperature °C"),
            ("brightness_min_percent", "Minimum brightness /100"), ("brightness_max_percent", "Maximum brightness /100"), ("warning_seconds", "Warning seconds"))
        for row, (key, label) in enumerate(profile_fields):
            self.profile[key] = self.field(self.profile_tab, row, label, "")[0]
        self.profile_note = tk.StringVar(value="Load the current profile before editing.")
        ttk.Label(self.profile_tab, textvariable=self.profile_note).grid(row=8, column=0, columnspan=2, sticky="w", pady=8)
        self.profile_save = self.button(self.profile_tab, "Save profile", lambda: self.save_edit("profile"))
        self.profile_save.grid(row=9, column=0, sticky="w")
        self.label = {}
        for row, (key, label) in enumerate((("title", "Title"), ("collection", "Collection"), ("description", "Description"), ("image_media_id", "Image media ID (optional)"))):
            self.label[key] = self.field(self.label_tab, row, label, "")[0]
        self.label_note = tk.StringVar(value="Load the current draft before editing.")
        ttk.Label(self.label_tab, textvariable=self.label_note).grid(row=4, column=0, columnspan=2, sticky="w", pady=8)
        self.label_save = self.button(self.label_tab, "Save label draft", lambda: self.save_edit("label"))
        self.label_save.grid(row=5, column=0, sticky="w")
        ttk.Label(feedback, text="Profile and draft edits update saved data. Applying them to a device requires a separate command.", wraplength=720).pack(anchor="w", pady=(0, 4))
        self.message = tk.StringVar(value="Ready. Keep the website’s Live showcase tab open to see automatic updates.")
        ttk.Label(feedback, textvariable=self.message, wraplength=720).pack(anchor="w", pady=(4, 0))
        self.progress = ttk.Progressbar(feedback, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 0))
        self.create_actions_menu()
        self.update_buttons()
        root.after(100, self.drain)

    def create_actions_menu(self):
        self.menubar = tk.Menu(self.root)
        self.actions_menu = tk.Menu(self.menubar, tearoff=False)
        mac = self.root.tk.call("tk", "windowingsystem") == "aqua"
        self.actions_menu.add_command(label="Load current data", command=self.load_current,
                                      accelerator="Command+L" if mac else "Ctrl+L")
        self.actions_menu.add_command(label="Focus sensor temperature", command=self.focus_temperature)
        self.actions_menu.add_command(label="Send live reading", command=self.send_reading,
                                      accelerator="Command+Return" if mac else "Ctrl+Return")
        self.actions_menu.add_command(label="Retry last upload", command=self.retry_upload)
        self.menubar.add_cascade(label="Actions", menu=self.actions_menu)
        self.root.configure(menu=self.menubar)
        bindings = [("<Control-l>", self.load_current), ("<Control-Return>", self.send_reading)]
        if mac:
            bindings.extend([("<Command-l>", self.load_current), ("<Command-Return>", self.send_reading)])
        for sequence, action in bindings:
            self.root.bind(sequence, lambda _event, command=action: self.shortcut(command))

    def shortcut(self, action):
        if not self.busy:
            action()
        return "break"

    def focus_temperature(self):
        if self.busy:
            return
        self.tabs.select(self.sensor_tab)
        self.temperature_entry.focus_set()
        self.temperature_entry.selection_range(0, tk.END)

    def button(self, parent, label, command):
        button = ttk.Button(parent, text=label, command=command)
        self.actions.append(button)
        return button

    def path_row(self, frame, row, label, variable, directory):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        browse = lambda: self.browse(variable, directory)
        self.button(frame, "Browse…", browse).grid(row=row, column=2, padx=(8, 0))
        frame.columnconfigure(1, weight=1)

    def field(self, frame, row, label, initial):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        variable = tk.StringVar(value=initial)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        frame.columnconfigure(1, weight=1)
        return variable, entry

    def browse(self, variable, directory):
        selected = filedialog.askdirectory(parent=self.root) if directory else filedialog.askopenfilename(parent=self.root, filetypes=[("JSON files", "*.json")])
        if selected:
            variable.set(selected)

    def get_editor(self):
        signature = (self.plans_path.get(), self.producer_path.get(), self.admin_path.get())
        if not signature[0].strip():
            raise ValueError("Choose a directory for the saved upload plans.")
        if signature != self.signature:
            previous = self.editor
            self.editor = DemoEditor(signature[0], signature[1] or None, signature[2] or None)
            if previous:
                self.editor.last_plan, self.editor.last_digest = previous.last_plan, previous.last_digest
            self.signature = signature
        return self.editor

    def update_buttons(self):
        for button in self.actions:
            button.configure(state="disabled" if self.busy else "normal")
        self.selector.configure(state="disabled" if self.busy else "readonly")
        for label in ("Load current data", "Focus sensor temperature", "Send live reading", "Retry last upload"):
            self.actions_menu.entryconfigure(label, state="disabled" if self.busy else "normal")
        if not self.busy:
            self.profile_save.configure(state="normal" if self.profile_version is not None else "disabled")
            self.label_save.configure(state="normal" if self.label_version is not None else "disabled")
            self.retry_button.configure(state="normal" if self.editor and self.editor.last_plan else "disabled")
            self.actions_menu.entryconfigure("Retry last upload", state="normal" if self.editor and self.editor.last_plan else "disabled")

    def run(self, label, operation, success):
        if self.busy:
            return
        try:
            editor = self.get_editor()
        except (ValueError, OSError) as error:
            self.message.set(str(error))
            return
        self.busy = True
        self.message.set(label + "…")
        self.progress.start()
        self.update_buttons()
        def worker():
            try:
                self.events.put((success, operation(editor), None))
            except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
                self.events.put((success, None, str(error)))
            except Exception:
                self.events.put((success, None, "An unexpected response stopped this action. Reload current data before editing again."))
        threading.Thread(target=worker, daemon=True).start()

    def drain(self):
        if self.closed:
            return
        try:
            success, result, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.progress.stop()
            if self.editor and self.editor.last_plan:
                self.plan_text.set("Saved upload plan: " + str(self.editor.last_plan))
            if error:
                self.message.set("Stopped: " + error + " An interrupted upload may be retried with the same saved plan.")
            else:
                success(result)
            self.update_buttons()
        self.root.after(100, self.drain)

    def selection_changed(self, _event=None):
        self.profile_version = self.label_version = None
        self.profile_note.set("Load this exhibit’s current profile before editing.")
        self.label_note.set("Load this exhibit’s current draft before editing.")
        self.update_buttons()

    def load_current(self):
        selected = self.selected.get()
        def loaded(data):
            profile, draft = data["profile"], data["draft"]
            self.profile_version = profile["version"]
            self.label_version = draft["version"] if draft else 0
            for key, variable in self.profile.items():
                variable.set(str(profile.get(key, "")))
            content = draft["content"] if draft else {"title": profile["name"], "collection": profile.get("collection", ""), "description": profile.get("description", ""), "image_media_id": None}
            for key, variable in self.label.items():
                variable.set(content.get(key) or "")
            status = data["showcase"].get("device_status") or {}
            if status.get("temperature_c") is not None:
                self.temperature.set(str(status["temperature_c"]))
            if status.get("brightness_percent") is not None:
                self.brightness.set(str(round(status["brightness_percent"] * 4095 / 100)))
            self.occupied.set(status.get("occupied") is True)
            self.distance.set("" if status.get("distance_cm") is None else str(status["distance_cm"]))
            self.current.set("Loaded " + selected + " · observation " + str(status.get("recorded_at", "unknown")))
            self.profile_note.set("Loaded profile version " + str(self.profile_version))
            self.label_note.set("Loaded draft version " + str(self.label_version))
            self.message.set("Current values loaded. Saves use these versions; reload after a version conflict.")
        self.run("Loading current data", lambda editor: editor.load(selected), loaded)

    def send_reading(self):
        args = (self.selected.get(), self.temperature.get(), self.brightness.get(), bool(self.occupied.get()), self.distance.get())
        self.run("Sending live reading", lambda editor: editor.send_telemetry(*args),
                 lambda _result: self.message.set("Reading and status saved and read back. Watch the Live showcase tab update automatically."))

    def retry_upload(self):
        self.run("Retrying the same saved upload", lambda editor: editor.retry_telemetry(),
                 lambda _result: self.message.set("Saved upload verified. Existing matching records were kept."))

    def open_upload(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("JSON upload plans", "*.json")])
        if path:
            def selected(data):
                reading = data["reading"]
                self.selected.set(reading["exhibit_id"])
                self.selection_changed()
                self.temperature.set(str(reading["temperature_c"]))
                self.brightness.set(str(reading["brightness_raw"]))
                self.occupied.set(reading["occupied"])
                self.distance.set("" if reading["distance_cm"] is None else str(reading["distance_cm"]))
                self.message.set("Saved values loaded into the sensor fields. Retry last upload sends or verifies this exact plan.")
            self.run("Opening saved upload", lambda editor: editor.select_retry(path), selected)

    def save_edit(self, kind):
        selected, version = self.selected.get(), self.profile_version if kind == "profile" else self.label_version
        variables = self.profile if kind == "profile" else self.label
        values = {key: variable.get() for key, variable in variables.items()}
        def saved(data):
            current = data["result"]["version"]
            if kind == "profile":
                self.profile_version = current
                self.profile_note.set("Saved profile version " + str(current))
            else:
                self.label_version = current
                self.label_note.set("Saved draft version " + str(current))
            self.message.set(("Profile" if kind == "profile" else "Label draft") + " saved at version " + str(current) + ". Device-confirmed content requires a separate command.")
        self.run("Saving " + kind, lambda editor: editor.save_edit(kind, selected, version, values), saved)

    def close(self):
        self.closed = True
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer-access", help="Private JSON file; never displayed or copied into plans")
    parser.add_argument("--admin-tokens", help="Current private Administrator OAuth JSON file")
    parser.add_argument("--plans-dir", type=Path, default=Path("demo-plans"))
    args = parser.parse_args()
    root = tk.Tk()
    DemoWindow(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
