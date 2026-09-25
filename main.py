#!/usr/bin/env python3
"""GTK4 GUI for capturing a simple webcam timelapse."""

import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk


class TimelapseWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="タイムラプス撮影")
        self.set_default_size(720, 600)
        self.set_resizable(True)

        self.timer_id = None
        self.is_running = False
        self.is_capturing = False
        self.capture_count = 0

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(20)
        root.set_margin_end(20)
        self.set_child(root)

        settings = Gtk.Grid(column_spacing=12, row_spacing=12)
        root.append(settings)

        folder_label = Gtk.Label(label="保存先", xalign=0)
        self.folder_entry = Gtk.Entry()
        default_output_dir = Path.home() / "Pictures" / "timelapse" / datetime.now().strftime("%Y%m%d-%H%M")
        self.folder_entry.set_text(str(default_output_dir))
        self.folder_entry.set_hexpand(True)
        self.folder_button = Gtk.Button(label="選択…")
        self.folder_button.connect("clicked", self.on_choose_folder)
        settings.attach(folder_label, 0, 0, 1, 1)
        settings.attach(self.folder_entry, 1, 0, 1, 1)
        settings.attach(self.folder_button, 2, 0, 1, 1)

        interval_label = Gtk.Label(label="撮影間隔（秒）", xalign=0)
        self.interval_spin = Gtk.SpinButton.new_with_range(0.5, 86400, 0.5)
        self.interval_spin.set_digits(1)
        self.interval_config_path = Path(GLib.get_user_config_dir()) / "timelapse-gui" / "settings.json"
        self.interval_spin.set_value(self.load_interval())
        self.interval_spin.connect("value-changed", self.on_interval_changed)
        settings.attach(interval_label, 0, 1, 1, 1)
        settings.attach(self.interval_spin, 1, 1, 1, 1)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.append(actions)
        self.start_button = Gtk.Button(label="撮影開始")
        self.start_button.add_css_class("suggested-action")
        self.start_button.connect("clicked", self.on_start)
        self.stop_button = Gtk.Button(label="撮影停止")
        self.stop_button.set_sensitive(False)
        self.stop_button.connect("clicked", self.on_stop)
        actions.append(self.start_button)
        actions.append(self.stop_button)

        self.state_label = Gtk.Label(label="停止中", xalign=0)
        self.count_label = Gtk.Label(label="撮影枚数: 0", xalign=0)
        root.append(self.state_label)
        root.append(self.count_label)

        preview_frame = Gtk.Frame(label="最新画像")
        preview_frame.set_vexpand(True)
        root.append(preview_frame)
        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_keep_aspect_ratio(True)
        preview_frame.set_child(self.picture)

        self.connect("close-request", self.on_close_request)

    def load_interval(self):
        try:
            settings = json.loads(self.interval_config_path.read_text(encoding="utf-8"))
            interval = float(settings.get("interval_seconds", 10))
            if 0.5 <= interval <= 86400 and round(interval * 2) == interval * 2:
                return interval
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        return 10

    def on_interval_changed(self, spin_button):
        try:
            self.interval_config_path.parent.mkdir(parents=True, exist_ok=True)
            self.interval_config_path.write_text(
                json.dumps({"interval_seconds": spin_button.get_value()}),
                encoding="utf-8",
            )
        except OSError as error:
            GLib.warning(f"撮影間隔を設定ファイルに保存できません: {error}")

    def on_choose_folder(self, _button):
        chooser = Gtk.FileChooserNative.new(
            "保存先を選択", self, Gtk.FileChooserAction.SELECT_FOLDER, "選択", "キャンセル"
        )
        chooser.connect("response", self.on_folder_chosen)
        chooser.show()

    def on_folder_chosen(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            folder = chooser.get_file()
            if folder is not None:
                self.folder_entry.set_text(folder.get_path())
        chooser.destroy()

    def on_start(self, _button):
        output_dir = Path(self.folder_entry.get_text()).expanduser()
        if not self.folder_entry.get_text().strip():
            self.show_error("保存先ディレクトリを指定してください。")
            return
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            if not output_dir.is_dir():
                raise OSError("指定先がディレクトリではありません")
        except OSError as error:
            self.show_error(f"保存先を準備できません: {error}")
            return

        self.output_dir = output_dir
        self.interval_milliseconds = round(self.interval_spin.get_value() * 1000)
        self.is_running = True
        self.start_button.set_sensitive(False)
        self.stop_button.set_sensitive(True)
        self.folder_entry.set_sensitive(False)
        self.folder_button.set_sensitive(False)
        self.interval_spin.set_sensitive(False)
        self.state_label.set_text("撮影中")
        self.capture_once()
        self.timer_id = GLib.timeout_add(self.interval_milliseconds, self.on_timer)

    def on_timer(self):
        if self.is_running:
            self.capture_once()
            return GLib.SOURCE_CONTINUE
        self.timer_id = None
        return GLib.SOURCE_REMOVE

    def capture_once(self):
        if self.is_capturing:
            return
        self.is_capturing = True
        now = datetime.now()
        output_path = self.output_dir / f"{now:%Y%m%d_%H%M%S}.jpg"
        # Keep the documented second-resolution name when possible, and add
        # milliseconds when a sub-second capture would otherwise overwrite it.
        if output_path.exists():
            output_path = self.output_dir / (
                f"{now:%Y%m%d_%H%M%S}_{now.microsecond // 1000:03d}.jpg"
            )
        threading.Thread(
            target=self.run_fswebcam, args=(output_path,), daemon=True
        ).start()

    def run_fswebcam(self, output_path):
        try:
            result = subprocess.run(
                ["fswebcam", "--no-banner", "--resolution", "1280x720", str(output_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                GLib.idle_add(self.on_capture_succeeded, output_path)
            else:
                details = (result.stderr or result.stdout).strip()
                GLib.idle_add(
                    self.on_capture_failed,
                    f"fswebcam が終了コード {result.returncode} で失敗しました。"
                    + (f"\n{details}" if details else ""),
                )
        except OSError as error:
            GLib.idle_add(self.on_capture_failed, f"fswebcam を実行できません: {error}")

    def on_capture_succeeded(self, output_path):
        self.is_capturing = False
        self.capture_count += 1
        self.count_label.set_text(f"撮影枚数: {self.capture_count}")
        self.picture.set_filename(str(output_path))
        self.state_label.set_text("撮影中" if self.is_running else "停止中")
        return GLib.SOURCE_REMOVE

    def on_capture_failed(self, message):
        self.is_capturing = False
        self.state_label.set_text(message)
        return GLib.SOURCE_REMOVE

    def on_stop(self, _button):
        self.stop_capture_timer()
        self.is_running = False
        self.start_button.set_sensitive(True)
        self.stop_button.set_sensitive(False)
        self.folder_entry.set_sensitive(True)
        self.folder_button.set_sensitive(True)
        self.interval_spin.set_sensitive(True)
        if not self.is_capturing:
            self.state_label.set_text("停止中")

    def stop_capture_timer(self):
        if self.timer_id is not None:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

    def show_error(self, message):
        self.state_label.set_text(message)

    def on_close_request(self, _window):
        self.stop_capture_timer()
        self.is_running = False
        return False


class TimelapseApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="jp.example.TimelapseGui", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        window = self.props.active_window
        if window is None:
            window = TimelapseWindow(self)
        window.present()


if __name__ == "__main__":
    raise SystemExit(TimelapseApplication().run())
