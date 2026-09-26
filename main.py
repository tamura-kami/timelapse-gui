#!/usr/bin/env python3
"""GTK4 GUI for capturing a simple webcam timelapse."""

import json
import math
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, Gio, GLib, Gst, Gtk

Gst.init(None)

PREVIEW_WIDTH = 640
PREVIEW_HEIGHT = 360


class TimelapseWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="タイムラプス撮影")
        self.set_default_size(720, 600)
        self.set_resizable(True)

        self.timer_id = None
        self.is_running = False
        self.is_capturing = False
        self.is_video_encoding = False
        self.is_closing = False
        self.capture_count = 0
        self.camera_controls = self.get_camera_controls()
        self.camera_scales = {}
        self.camera_write_timer_ids = {}
        self.saved_camera_controls = {}
        self.preview_pipeline = None
        self.preview_sink = None
        self.preview_poll_id = None
        self.preview_bus = None
        self.preview_bus_handler = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(20)
        root.set_margin_end(20)
        self.set_child(root)

        settings = Gtk.Grid(column_spacing=12, row_spacing=6)
        root.append(settings)

        folder_label = Gtk.Label(label="保存先", xalign=0)
        self.folder_entry = Gtk.Entry()
        default_output_dir = Path.home() / "Pictures" / "timelapse" / datetime.now().strftime("%Y%m%d-%H%M")
        self.folder_entry.set_text(str(default_output_dir))
        self.folder_entry.set_hexpand(True)
        self.folder_button = Gtk.Button(label="選択…")
        self.folder_button.connect("clicked", self.on_choose_folder)
        self.new_folder_button = Gtk.Button(label="新規")
        self.new_folder_button.connect("clicked", self.on_create_new_folder)
        settings.attach(folder_label, 0, 0, 1, 1)
        settings.attach(self.folder_entry, 1, 0, 1, 1)
        settings.attach(self.folder_button, 2, 0, 1, 1)
        settings.attach(self.new_folder_button, 3, 0, 1, 1)

        interval_label = Gtk.Label(label="撮影間隔（秒）", xalign=0)
        self.interval_spin = Gtk.SpinButton.new_with_range(0.1, 86400, 0.1)
        self.interval_spin.set_digits(1)
        self.interval_config_path = Path(GLib.get_user_config_dir()) / "timelapse-gui" / "settings.json"
        self.interval_spin.set_value(self.load_interval())
        self.interval_spin.connect("value-changed", self.on_interval_changed)
        settings.attach(interval_label, 0, 1, 1, 1)
        settings.attach(self.interval_spin, 1, 1, 1, 1)

        control_labels = {
            "brightness": "明るさ",
            "contrast": "コントラスト",
            "saturation": "彩度",
        }
        camera_controls_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12
        )
        settings.attach(camera_controls_box, 0, 2, 4, 1)
        for control, label_text in control_labels.items():
            control_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=2
            )
            control_box.set_hexpand(True)
            camera_controls_box.append(control_box)
            control_box.append(Gtk.Label(label=label_text, xalign=0))
            info = self.camera_controls.get(control)
            if info is None:
                control_box.append(
                    Gtk.Label(label="未対応または未接続", xalign=0)
                )
                continue

            minimum, maximum, step, current = info
            scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, minimum, maximum, step
            )
            scale.set_digits(0)
            scale.set_draw_value(True)
            scale.set_hexpand(True)
            scale.set_size_request(-1, 22)
            scale.set_margin_top(0)
            scale.set_margin_bottom(0)
            saved_value = self.load_saved_camera_control(
                control, minimum, maximum, step
            )
            scale.set_value(current if saved_value is None else saved_value)
            scale.connect("value-changed", self.on_camera_control_changed, control)
            control_box.append(scale)
            self.camera_scales[control] = scale
            if saved_value is not None:
                self.saved_camera_controls[control] = saved_value

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.append(actions)
        self.start_button = Gtk.Button(label="撮影開始")
        self.start_button.add_css_class("suggested-action")
        self.start_button.connect("clicked", self.on_start)
        self.stop_button = Gtk.Button(label="撮影停止")
        self.stop_button.set_sensitive(False)
        self.stop_button.connect("clicked", self.on_stop)
        self.video_button = Gtk.Button(label="動画化")
        self.video_button.connect("clicked", self.on_make_video)
        actions.append(self.start_button)
        actions.append(self.stop_button)
        actions.append(self.video_button)

        self.state_label = Gtk.Label(label="停止中", xalign=0)
        self.count_label = Gtk.Label(label="撮影枚数: 0", xalign=0)
        root.append(self.state_label)
        root.append(self.count_label)

        preview_frame = Gtk.Frame(label="カメラプレビュー / 最新画像")
        preview_frame.set_vexpand(True)
        root.append(preview_frame)
        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        preview_aspect = Gtk.AspectFrame.new(
            0.5, 0.5, PREVIEW_WIDTH / PREVIEW_HEIGHT, False
        )
        preview_aspect.set_hexpand(True)
        preview_aspect.set_vexpand(True)
        preview_aspect.set_child(self.picture)
        preview_frame.set_child(preview_aspect)

        self.connect("close-request", self.on_close_request)
        for control, value in self.saved_camera_controls.items():
            self.set_camera_control(control, value)

    def start_preview(self):
        if self.is_closing or self.preview_pipeline is not None or self.is_capturing:
            return
        pipeline_description = (
            "v4l2src device=/dev/video0 ! videoconvert ! videoscale ! videorate ! "
            f"video/x-raw,width={PREVIEW_WIDTH},height={PREVIEW_HEIGHT},framerate=5/1 ! "
            "jpegenc quality=75 ! appsink name=preview_sink max-buffers=1 drop=true sync=false"
        )
        try:
            pipeline = Gst.parse_launch(pipeline_description)
        except GLib.Error as error:
            self.state_label.set_text(f"カメラプレビューを準備できません: {error.message}")
            return

        self.preview_pipeline = pipeline
        self.preview_sink = pipeline.get_by_name("preview_sink")
        self.preview_bus = pipeline.get_bus()
        self.preview_bus.add_signal_watch()
        self.preview_bus_handler = self.preview_bus.connect(
            "message::error", self.on_preview_error
        )
        result = pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self.stop_preview()
            self.state_label.set_text("カメラプレビューを開始できません。カメラデバイスを確認してください。")
            return
        self.preview_poll_id = GLib.timeout_add(100, self.update_preview)
        if not self.is_running:
            self.state_label.set_text("停止中（ライブプレビュー）")

    def stop_preview(self):
        if self.preview_poll_id is not None:
            GLib.source_remove(self.preview_poll_id)
            self.preview_poll_id = None
        if self.preview_bus is not None:
            if self.preview_bus_handler is not None:
                self.preview_bus.disconnect(self.preview_bus_handler)
                self.preview_bus_handler = None
            self.preview_bus.remove_signal_watch()
            self.preview_bus = None
        if self.preview_pipeline is not None:
            self.preview_pipeline.set_state(Gst.State.NULL)
            self.preview_pipeline = None
            self.preview_sink = None

    def update_preview(self):
        if self.preview_sink is None or self.is_closing:
            self.preview_poll_id = None
            return GLib.SOURCE_REMOVE
        sample = self.preview_sink.emit("try-pull-sample", 0)
        if sample is not None:
            buffer = sample.get_buffer()
            success, mapped = buffer.map(Gst.MapFlags.READ)
            if success:
                try:
                    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(mapped.data))
                    self.picture.set_paintable(texture)
                    if not self.is_running and not self.is_capturing:
                        self.state_label.set_text("停止中（ライブプレビュー）")
                except GLib.Error as error:
                    self.state_label.set_text(f"プレビュー画像を表示できません: {error.message}")
                finally:
                    buffer.unmap(mapped)
        return GLib.SOURCE_CONTINUE

    def on_preview_error(self, _bus, message):
        error, _debug = message.parse_error()
        self.stop_preview()
        if not self.is_closing:
            self.state_label.set_text(f"カメラプレビューのエラー: {error.message}")
        return GLib.SOURCE_REMOVE

    def load_interval(self):
        try:
            settings = json.loads(self.interval_config_path.read_text(encoding="utf-8"))
            interval = float(settings.get("interval_seconds", 10))
            if (
                math.isfinite(interval)
                and 0.1 <= interval <= 86400
                and math.isclose(interval * 10, round(interval * 10), abs_tol=1e-9)
            ):
                return interval
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        return 10

    def get_camera_controls(self):
        controls = {}
        if shutil.which("v4l2-ctl") is None:
            return controls
        try:
            result = subprocess.run(
                ["v4l2-ctl", "-d", "/dev/video0", "--list-ctrls"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return controls
        if result.returncode != 0:
            return controls
        for line in result.stdout.splitlines():
            match = re.match(
                r"\s*(brightness|contrast|saturation)\b",
                line,
            )
            if match:
                control = match.group(1)
                values = re.search(
                    r"\bmin=(-?\d+)\s+max=(-?\d+)\s+step=(\d+).*?\bvalue=(-?\d+)",
                    line,
                )
                if values:
                    minimum, maximum, step, current = map(int, values.groups())
                    if minimum < maximum and step > 0:
                        controls[control] = (minimum, maximum, step, current)
        return controls

    def load_saved_camera_control(self, control, minimum, maximum, step):
        try:
            settings = json.loads(self.interval_config_path.read_text(encoding="utf-8"))
            value = int(settings[control])
            if minimum <= value <= maximum and (value - minimum) % step == 0:
                return value
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass
        return None

    def on_camera_control_changed(self, scale, control):
        timer_id = self.camera_write_timer_ids.get(control)
        if timer_id is not None:
            GLib.source_remove(timer_id)
        self.camera_write_timer_ids[control] = GLib.timeout_add(
            150, self.apply_camera_control_change, control, round(scale.get_value())
        )

    def apply_camera_control_change(self, control, value):
        self.camera_write_timer_ids[control] = None
        self.set_camera_control(control, value)
        self.save_camera_control(control, value)
        return GLib.SOURCE_REMOVE

    def save_camera_control(self, control, value):
        try:
            self.interval_config_path.parent.mkdir(parents=True, exist_ok=True)
            settings = {}
            try:
                settings = json.loads(self.interval_config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            settings[control] = value
            self.interval_config_path.write_text(
                json.dumps(settings), encoding="utf-8"
            )
        except OSError as error:
            GLib.warning(f"カメラ設定を保存できません: {error}")

    def set_camera_control(self, control, value):
        try:
            result = subprocess.run(
                ["v4l2-ctl", "-d", "/dev/video0", f"--set-ctrl={control}={value}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self.show_error(f"カメラ設定を変更できません: {error}")
            return False
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            self.show_error(
                "カメラ設定を変更できません。"
                + (f"\n{details}" if details else "")
            )
            return False
        return True

    def on_interval_changed(self, spin_button):
        try:
            self.interval_config_path.parent.mkdir(parents=True, exist_ok=True)
            settings = {}
            try:
                settings = json.loads(self.interval_config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            settings["interval_seconds"] = spin_button.get_value()
            self.interval_config_path.write_text(
                json.dumps(settings), encoding="utf-8"
            )
        except OSError as error:
            GLib.warning(f"撮影間隔を設定ファイルに保存できません: {error}")

    def on_choose_folder(self, _button):
        dialog = Gtk.FileDialog()
        dialog.set_title("保存先を選択")
        dialog.select_folder(self, None, self.on_folder_chosen)

    def on_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error as error:
            if not (
                error.matches(Gtk.DialogError.quark(), Gtk.DialogError.CANCELLED)
                or error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED)
            ):
                self.show_error(f"保存先を選択できません: {error.message}")
            return
        if folder is not None and folder.get_path() is not None:
            self.folder_entry.set_text(folder.get_path())

    def on_create_new_folder(self, _button):
        parent_dir = Path.home() / "Pictures" / "timelapse"
        folder_name = datetime.now().strftime("%Y%m%d-%H%M%S")
        new_dir = parent_dir / folder_name
        suffix = 1
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
            while True:
                try:
                    new_dir.mkdir()
                    break
                except FileExistsError:
                    new_dir = parent_dir / f"{folder_name}-{suffix:03d}"
                    suffix += 1
            self.folder_entry.set_text(str(new_dir))
            self.capture_count = 0
            self.count_label.set_text("撮影枚数: 0")
            self.picture.set_paintable(None)
            self.state_label.set_text(f"新しい保存先を作成しました: {new_dir}")
        except OSError as error:
            self.show_error(f"新しい保存先を作成できません: {error}")

    def on_start(self, _button):
        if self.is_video_encoding:
            return
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
        self.new_folder_button.set_sensitive(False)
        self.interval_spin.set_sensitive(False)
        self.video_button.set_sensitive(False)
        self.set_camera_scales_sensitive(False)
        self.state_label.set_text("撮影中")
        self.capture_once()
        self.timer_id = GLib.timeout_add(self.interval_milliseconds, self.on_timer)

    def on_make_video(self, _button):
        if self.is_running or self.is_capturing or self.is_video_encoding:
            self.show_error("撮影停止後、撮影処理が終わってから動画化してください。")
            return

        image_dir = Path(self.folder_entry.get_text()).expanduser()
        if not image_dir.is_dir():
            self.show_error(f"保存先ディレクトリがありません: {image_dir}")
            return

        script_path = Path(__file__).resolve().parent / "images_to_stabilized_video.sh"
        if not script_path.is_file():
            self.show_error(f"動画化スクリプトが見つかりません: {script_path}")
            return

        self.is_video_encoding = True
        self.set_video_controls_sensitive(False)
        self.state_label.set_text("動画の保存先を選択してください")
        dialog = Gtk.FileDialog()
        dialog.set_title("動画の保存先")
        dialog.set_initial_folder(Gio.File.new_for_path(str(image_dir.parent)))
        dialog.set_initial_name(f"{image_dir.name}_stabilized.mp4")
        dialog.save(self, None, self.on_video_output_chosen, (script_path, image_dir))

    def on_video_output_chosen(self, dialog, result, task_data):
        script_path, image_dir = task_data
        try:
            output_file = dialog.save_finish(result)
        except GLib.Error as error:
            self.is_video_encoding = False
            self.set_video_controls_sensitive(True)
            if not self.is_closing:
                if error.matches(Gtk.DialogError.quark(), Gtk.DialogError.CANCELLED) or error.matches(
                    Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED
                ):
                    self.state_label.set_text("動画化をキャンセルしました")
                else:
                    self.show_error(f"動画の保存先を選択できません: {error.message}")
            return

        output_path_text = output_file.get_path()
        if output_path_text is None:
            self.is_video_encoding = False
            self.set_video_controls_sensitive(True)
            self.show_error("ローカルの保存先を選択してください。")
            return
        output_path = Path(output_path_text)
        if output_path.suffix.lower() != ".mp4":
            output_path = output_path.with_name(f"{output_path.name}.mp4")
        self.state_label.set_text("動画を作成中です…")
        threading.Thread(
            target=self.run_video_encoder,
            args=(script_path, image_dir, output_path),
            daemon=True,
        ).start()

    def run_video_encoder(self, script_path, image_dir, output_path):
        try:
            result = subprocess.run(
                [str(script_path), str(image_dir), "10", str(output_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                message = f"動画を作成しました: {output_path}"
                GLib.idle_add(self.on_video_encoding_finished, message)
            else:
                details = (result.stderr or result.stdout).strip()
                message = "動画化に失敗しました。"
                if details:
                    message += f"\n{details}"
                GLib.idle_add(self.on_video_encoding_finished, message)
        except OSError as error:
            GLib.idle_add(
                self.on_video_encoding_finished,
                f"動画化スクリプトを実行できません: {error}",
            )

    def on_video_encoding_finished(self, message):
        self.is_video_encoding = False
        if self.is_closing:
            return GLib.SOURCE_REMOVE
        self.start_button.set_sensitive(True)
        self.video_button.set_sensitive(True)
        self.folder_entry.set_sensitive(True)
        self.folder_button.set_sensitive(True)
        self.new_folder_button.set_sensitive(True)
        self.interval_spin.set_sensitive(True)
        self.set_camera_scales_sensitive(True)
        self.state_label.set_text(message)
        return GLib.SOURCE_REMOVE

    def set_video_controls_sensitive(self, sensitive):
        self.start_button.set_sensitive(sensitive)
        self.video_button.set_sensitive(sensitive)
        self.folder_entry.set_sensitive(sensitive)
        self.folder_button.set_sensitive(sensitive)
        self.new_folder_button.set_sensitive(sensitive)
        self.interval_spin.set_sensitive(sensitive)
        self.set_camera_scales_sensitive(sensitive)

    def set_camera_scales_sensitive(self, sensitive):
        for scale in self.camera_scales.values():
            scale.set_sensitive(sensitive)

    def on_timer(self):
        if self.is_running:
            self.capture_once()
            return GLib.SOURCE_CONTINUE
        self.timer_id = None
        return GLib.SOURCE_REMOVE

    def capture_once(self):
        if self.is_capturing or self.is_closing:
            return
        self.is_capturing = True
        self.stop_preview()
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
        if self.is_closing:
            return GLib.SOURCE_REMOVE
        self.capture_count += 1
        self.count_label.set_text(f"撮影枚数: {self.capture_count}")
        self.picture.set_filename(str(output_path))
        self.state_label.set_text("撮影中" if self.is_running else "停止中（ライブプレビュー）")
        self.start_preview()
        return GLib.SOURCE_REMOVE

    def on_capture_failed(self, message):
        self.is_capturing = False
        if self.is_closing:
            return GLib.SOURCE_REMOVE
        self.state_label.set_text(message)
        self.start_preview()
        return GLib.SOURCE_REMOVE

    def on_stop(self, _button):
        self.stop_capture_timer()
        self.is_running = False
        self.start_button.set_sensitive(True)
        self.stop_button.set_sensitive(False)
        self.folder_entry.set_sensitive(True)
        self.folder_button.set_sensitive(True)
        self.new_folder_button.set_sensitive(True)
        self.interval_spin.set_sensitive(True)
        self.set_camera_scales_sensitive(True)
        self.video_button.set_sensitive(True)
        if not self.is_capturing:
            self.state_label.set_text("停止中（ライブプレビュー）")

    def stop_capture_timer(self):
        if self.timer_id is not None:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

    def show_error(self, message):
        self.state_label.set_text(message)

    def on_close_request(self, _window):
        self.is_closing = True
        self.stop_capture_timer()
        self.is_running = False
        self.stop_preview()
        return False


class TimelapseApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="jp.example.TimelapseGui", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        window = self.props.active_window
        if window is None:
            window = TimelapseWindow(self)
        window.present()
        window.start_preview()


if __name__ == "__main__":
    raise SystemExit(TimelapseApplication().run())
