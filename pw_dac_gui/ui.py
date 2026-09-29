from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import conf, hardware, profiles, status as pwstatus
from .profiles import CHANNEL_LAYOUTS, DacProfile, LAYOUT_LABELS

APP_ID = "local.ooto.PipewireDacGui"
PRESETS = [
    ("stable", "安定"),
    ("lowlat", "低遅延"),
    ("hires", "有効DACのレート"),
    ("match_dac", "接続DACに合わせる"),
]


def _esc(text: str) -> str:
    return GLib.markup_escape_text(text or "")


def _dropdown(values: list[str], selected: int = 0) -> Gtk.DropDown:
    drop = Gtk.DropDown.new_from_strings(values)
    if 0 <= selected < len(values):
        drop.set_selected(selected)
    drop.set_hexpand(True)
    return drop


def _select(drop: Gtk.DropDown, value: str) -> None:
    model = drop.get_model()
    if model is None:
        return
    for i in range(model.get_n_items()):
        if model.get_string(i) == value:
            drop.set_selected(i)
            return


def _selected(drop: Gtk.DropDown) -> str:
    item = drop.get_selected_item()
    if item is None:
        return ""
    return item.get_string()


def _spin(value: float, lower: float, upper: float, step: float) -> Gtk.SpinButton:
    adj = Gtk.Adjustment(value=value, lower=lower, upper=upper, step_increment=step)
    spin = Gtk.SpinButton(adjustment=adj, digits=0)
    spin.set_numeric(True)
    spin.set_hexpand(True)
    return spin


def _hint(text: str) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=0, wrap=True)
    lab.add_css_class("hint")
    return lab


def _page_box() -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    box.set_margin_top(16)
    box.set_margin_bottom(16)
    box.set_margin_start(16)
    box.set_margin_end(16)
    return box


def _attach(grid: Gtk.Grid, row: int, label: str, widget: Gtk.Widget) -> int:
    lab = Gtk.Label(label=label, xalign=0)
    lab.set_width_chars(16)
    grid.attach(lab, 0, row, 1, 1)
    grid.attach(widget, 1, row, 1, 1)
    return row + 1


def install_desktop_entry() -> None:
    root = Path(__file__).resolve().parent.parent
    src = root / "share" / "pipewire-dac-gui.desktop"
    if not src.exists():
        return
    dest_dir = Path.home() / ".local" / "share" / "applications"
    dest = dest_dir / "pipewire-dac-gui.desktop"
    text = src.read_text(encoding="utf-8").replace("@EXEC@", str(root / "pipewire-dac-gui"))
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    except OSError:
        return


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="PipeWire DAC 設定")
        self.set_default_size(820, 700)
        self.set_icon_name("audio-card")
        self._updating = False
        self._banner_gen = 0
        self.settings = conf.load()
        self._file_keys: list[Path] = []
        self._selected_dac_id: str | None = self.settings.dacs[0].id if self.settings.dacs else None

        self._build_css()
        self._build_header()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.banner = Gtk.Revealer()
        self.banner.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.banner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.banner_box.add_css_class("banner")
        self.banner_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        self.banner_box.append(self.banner_label)
        self.banner.set_child(self.banner_box)
        root.append(self.banner)

        nb = Gtk.Notebook()
        nb.set_hexpand(True)
        nb.set_vexpand(True)
        nb.append_page(self._status_page(), Gtk.Label(label="状態"))
        nb.append_page(self._clock_page(), Gtk.Label(label="グラフ"))
        nb.append_page(self._dac_page(), Gtk.Label(label="DAC"))
        nb.append_page(self._files_page(), Gtk.Label(label="ファイル"))
        self.notebook = nb
        nb.connect("switch-page", lambda *_: GLib.idle_add(self._refresh_preview))
        root.append(nb)
        self.set_child(root)

        self._to_widgets()
        self.refresh_live()
        GLib.timeout_add_seconds(2, self._tick)

    def _build_css(self) -> None:
        css = Gtk.CssProvider()
        css.load_from_string(
            """
            .hint { opacity: 0.78; }
            .ok { font-weight: 600; }
            label.warn-text { color: #c45c00; }
            label.err-text { color: #c62828; }
            .banner { padding: 8px 12px; }
            .banner-ok { background: #2e7d32; }
            .banner-ok label { color: #fff; }
            .banner-warn { background: #ef6c00; }
            .banner-warn label { color: #fff; }
            .banner-err { background: #c62828; }
            .banner-err label { color: #fff; }
            textview.mono { font-family: monospace; font-size: 0.95em; }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_header(self) -> None:
        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label="PipeWire DAC 設定")
        title.add_css_class("title")
        self.header_badge = Gtk.Label()
        self.header_badge.add_css_class("subtitle")
        titles.append(title)
        titles.append(self.header_badge)
        header.set_title_widget(titles)

        reload_btn = Gtk.Button(label="再読込")
        reload_btn.connect("clicked", lambda *_: self._reload())
        header.pack_start(reload_btn)

        save_btn = Gtk.Button(label="保存")
        save_btn.connect("clicked", lambda *_: self._save(restart=False))
        header.pack_end(save_btn)
        apply_btn = Gtk.Button(label="保存して再起動")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", lambda *_: self._save(restart=True))
        header.pack_end(apply_btn)

    def _status_page(self) -> Gtk.Widget:
        box = _page_box()
        live = Gtk.Frame(label="いまの PipeWire")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_margin_top(10)
        inner.set_margin_bottom(10)
        inner.set_margin_start(12)
        inner.set_margin_end(12)
        self.lbl_cards = Gtk.Label(xalign=0, wrap=True)
        self.lbl_graph = Gtk.Label(xalign=0, wrap=True)
        self.lbl_sink = Gtk.Label(xalign=0, wrap=True)
        self.lbl_holder = Gtk.Label(xalign=0, wrap=True)
        self.lbl_usb = Gtk.Label(xalign=0, wrap=True)
        for lab in (self.lbl_cards, self.lbl_graph, self.lbl_sink, self.lbl_holder, self.lbl_usb):
            inner.append(lab)
        live.set_child(inner)
        box.append(live)

        tmp = Gtk.Frame(label="一時的な切替（再起動不要・保存しない）")
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_margin_top(10)
        grid.set_margin_bottom(10)
        grid.set_margin_start(12)
        grid.set_margin_end(12)
        rate_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.dd_force_rate = _dropdown(["自動"] + [conf.RATE_LABEL[r] for r in conf.ALL_RATES])
        rate_go = Gtk.Button(label="反映")
        rate_go.connect("clicked", lambda *_: self._apply_force_rate())
        rate_row.append(self.dd_force_rate)
        rate_row.append(rate_go)
        q_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.dd_force_quantum = _dropdown(["自動"] + [str(q) for q in conf.QUANTUMS])
        q_go = Gtk.Button(label="反映")
        q_go.connect("clicked", lambda *_: self._apply_force_quantum())
        q_row.append(self.dd_force_quantum)
        q_row.append(q_go)
        _attach(grid, 0, "レート", rate_row)
        _attach(grid, 1, "quantum", q_row)
        tmp.set_child(grid)
        box.append(tmp)
        box.append(
            _hint(
                "ALSA Direct（hw:x,0）が PCM を開いていると PipeWire と排他になります。"
            )
        )
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(box)
        return scroll

    def _clock_page(self) -> Gtk.Widget:
        box = _page_box()
        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for key, label in PRESETS:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda _b, k=key: self._on_preset(k))
            preset_box.append(btn)
        box.append(preset_box)
        box.append(_hint("プリセットはグラフ側の値です。DAC の追加・変更は DAC タブで行います。保存するまで PipeWire は変わりません。"))

        rates_frame = Gtk.Frame(label="グラフが追従するサンプルレート")
        checks = Gtk.FlowBox()
        checks.set_selection_mode(Gtk.SelectionMode.NONE)
        checks.set_max_children_per_line(4)
        checks.set_min_children_per_line(2)
        checks.set_homogeneous(True)
        self.rate_checks: dict[int, Gtk.CheckButton] = {}
        for rate in conf.ALL_RATES:
            cb = Gtk.CheckButton(label=conf.RATE_LABEL[rate])
            cb.connect("toggled", lambda *_: self._on_form_changed())
            self.rate_checks[rate] = cb
            checks.append(cb)
        rates_frame.set_child(checks)
        box.append(rates_frame)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        self.dd_clock_rate = _dropdown([conf.RATE_LABEL[r] for r in conf.ALL_RATES], 1)
        self.dd_quantum = _dropdown([str(q) for q in conf.QUANTUMS])
        self.dd_min_quantum = _dropdown([str(q) for q in conf.QUANTUMS])
        self.dd_max_quantum = _dropdown([str(q) for q in conf.QUANTUMS])
        self.spin_quality = _spin(10, 0, 14, 1)
        qbz_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.sw_qbz = Gtk.Switch()
        self.sw_qbz.set_valign(Gtk.Align.CENTER)
        qbz_row.append(self.sw_qbz)
        qbz_row.append(_hint("qbz だけリサンプルしない（曲のレートとグラフが一致しているとき）"))
        row = 0
        row = _attach(grid, row, "既定レート", self.dd_clock_rate)
        row = _attach(grid, row, "quantum", self.dd_quantum)
        row = _attach(grid, row, "min-quantum", self.dd_min_quantum)
        row = _attach(grid, row, "max-quantum", self.dd_max_quantum)
        row = _attach(grid, row, "リサンプル品質", self.spin_quality)
        row = _attach(grid, row, "QBZ", qbz_row)
        box.append(grid)

        self.lbl_latency = Gtk.Label(xalign=0, wrap=True)
        self.lbl_usb_clock = Gtk.Label(xalign=0, wrap=True)
        box.append(self.lbl_latency)
        box.append(self.lbl_usb_clock)

        exp = Gtk.Expander(label="詳細")
        extra = Gtk.Grid(column_spacing=12, row_spacing=8)
        extra.set_margin_top(8)
        self.dd_quantum_limit = _dropdown([str(q) for q in conf.QUANTUMS])
        _attach(extra, 0, "quantum-limit", self.dd_quantum_limit)
        extra.attach(_hint("Pulse / 映像クライアントの上限バッファ。小さすぎると min-quantum を切れます。"), 0, 1, 2, 1)
        exp.set_child(extra)
        box.append(exp)

        for widget in (
            self.dd_clock_rate,
            self.dd_quantum,
            self.dd_min_quantum,
            self.dd_max_quantum,
            self.dd_quantum_limit,
        ):
            widget.connect("notify::selected", lambda *_: self._on_form_changed())
        self.spin_quality.connect("value-changed", lambda *_: self._on_form_changed())
        self.sw_qbz.connect("notify::active", lambda *_: self._on_form_changed())

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(box)
        return scroll

    def _dac_page(self) -> Gtk.Widget:
        box = _page_box()
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_conn = Gtk.Button(label="接続中の USB DAC を追加")
        add_conn.connect("clicked", lambda *_: self._add_connected_dacs())
        add_blank = Gtk.Button(label="汎用プロファイルを追加")
        add_blank.connect("clicked", lambda *_: self._add_blank_dac())
        bar.append(add_conn)
        bar.append(add_blank)
        box.append(bar)
        box.append(
            _hint(
                "接続中の機器から作るか、空の汎用プロファイルに PipeWire ID を入れて保存します。"
                " K9 / CA7 / 内蔵は初期プロファイルとして載せてあります。"
            )
        )

        self.dac_list = Gtk.ListBox()
        self.dac_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.dac_list.set_show_separators(True)
        self.dac_list.connect("row-selected", self._on_dac_row)
        list_frame = Gtk.Frame(label="プロファイル")
        list_frame.set_child(self.dac_list)
        box.append(list_frame)

        editor = Gtk.Frame(label="選択中の DAC")
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_margin_top(10)
        grid.set_margin_bottom(10)
        grid.set_margin_start(12)
        grid.set_margin_end(12)
        self.ent_dac_name = Gtk.Entry()
        self.ent_usb_id = Gtk.Entry()
        self.ent_usb_id.set_placeholder_text("2972:0047")
        self.ent_pw_id = Gtk.Entry()
        self.ent_pw_id.set_placeholder_text("usb-Vendor_Product-00")
        self.dd_dac_role = _dropdown(["DAC", "内蔵"])
        self.dd_dac_format = _dropdown(["（指定しない）"] + conf.FORMATS)
        self.spin_dac_headroom = _spin(256, -1, 1024, 32)
        self.spin_dac_suspend = _spin(3, -1, 30, 1)
        self.sw_pro_audio = Gtk.Switch()
        self.sw_pro_audio.set_valign(Gtk.Align.CENTER)
        self.dd_channel_layout = _dropdown(LAYOUT_LABELS)
        self.sw_disable_inputs = Gtk.Switch()
        self.sw_disable_inputs.set_valign(Gtk.Align.CENTER)
        self.ent_output_suffixes = Gtk.Entry()
        self.ent_output_suffixes.set_placeholder_text("空 = すべての出力。CA7 アナログは pro-output-0")
        self.ent_disable_suffixes = Gtk.Entry()
        self.ent_disable_suffixes.set_placeholder_text("無効化する出力。CA7 SPDIF だけ使うなら pro-output-0")
        self.ent_dac_notes = Gtk.Entry()
        rates = Gtk.FlowBox()
        rates.set_selection_mode(Gtk.SelectionMode.NONE)
        rates.set_max_children_per_line(4)
        self.dac_rate_checks: dict[int, Gtk.CheckButton] = {}
        for rate in conf.ALL_RATES:
            cb = Gtk.CheckButton(label=conf.RATE_LABEL[rate])
            cb.connect("toggled", lambda *_: self._on_form_changed())
            self.dac_rate_checks[rate] = cb
            rates.append(cb)
        row = 0
        row = _attach(grid, row, "名前", self.ent_dac_name)
        row = _attach(grid, row, "種類", self.dd_dac_role)
        row = _attach(grid, row, "USB ID", self.ent_usb_id)
        row = _attach(grid, row, "PipeWire ID", self.ent_pw_id)
        row = _attach(grid, row, "PCM フォーマット", self.dd_dac_format)
        row = _attach(grid, row, "headroom", self.spin_dac_headroom)
        row = _attach(grid, row, "サスペンド（秒）", self.spin_dac_suspend)
        row = _attach(grid, row, "pro-audio", self.sw_pro_audio)
        row = _attach(grid, row, "FL/FR（配置）", self.dd_channel_layout)
        row = _attach(grid, row, "入力を無効", self.sw_disable_inputs)
        grid.attach(Gtk.Label(label="ネイティブレート", xalign=0), 0, row, 1, 1)
        grid.attach(rates, 1, row, 1, 1)
        row += 1
        row = _attach(grid, row, "出力 suffix", self.ent_output_suffixes)
        row = _attach(grid, row, "無効 suffix", self.ent_disable_suffixes)
        row = _attach(grid, row, "メモ", self.ent_dac_notes)
        grid.attach(
            _hint(
                "headroom / サスペンドは -1 で省略。PipeWire ID は alsa_card. の後ろです。"
                " CA7 アナログはチャンネル 7.1、出力 suffix を pro-output-0 にしてください。"
                " Stereo FL/FR だとリアが消えるかフロントに畳まれます。"
            ),
            0,
            row,
            2,
            1,
        )
        editor.set_child(grid)
        box.append(editor)

        for entry in (
            self.ent_dac_name,
            self.ent_usb_id,
            self.ent_pw_id,
            self.ent_output_suffixes,
            self.ent_disable_suffixes,
            self.ent_dac_notes,
        ):
            entry.connect("changed", lambda *_: self._on_form_changed())
        for drop in (self.dd_dac_role, self.dd_dac_format, self.dd_channel_layout):
            drop.connect("notify::selected", lambda *_: self._on_form_changed())
        for spin in (self.spin_dac_headroom, self.spin_dac_suspend):
            spin.connect("value-changed", lambda *_: self._on_form_changed())
        for sw in (self.sw_pro_audio, self.sw_disable_inputs):
            sw.connect("notify::active", lambda *_: self._on_form_changed())

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(box)
        return scroll

    def _files_page(self) -> Gtk.Widget:
        box = _page_box()
        box.append(_hint("保存時に書き出す内容です。既存ファイルは ~/.config/pipewire/gui-backup/ に退避します。"))
        self.dd_files = Gtk.DropDown()
        self.dd_files.set_hexpand(True)
        self.dd_files.connect("notify::selected", lambda *_: self._show_selected_file())
        box.append(self.dd_files)
        self.tv_preview = Gtk.TextView()
        self.tv_preview.set_editable(False)
        self.tv_preview.set_monospace(True)
        self.tv_preview.set_wrap_mode(Gtk.WrapMode.NONE)
        self.tv_preview.add_css_class("mono")
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_hexpand(True)
        sw.set_child(self.tv_preview)
        box.append(sw)
        return box

    def _connected_cards(self) -> list[hardware.Card]:
        cards = hardware.probe_cards()
        hardware.apply_profiles(cards, self.settings.dacs)
        return cards

    def _rebuild_dac_list(self) -> None:
        self._updating = True
        while True:
            row = self.dac_list.get_row_at_index(0)
            if row is None:
                break
            self.dac_list.remove(row)
        cards = self._connected_cards()
        for dac in self.settings.dacs:
            row = Gtk.ListBoxRow()
            row.dac_id = dac.id
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(8)
            box.set_margin_end(8)
            enabled = Gtk.CheckButton()
            enabled.set_active(dac.enabled)
            enabled.connect("toggled", lambda btn, dac_id=dac.id: self._toggle_dac(dac_id, btn.get_active()))
            name = Gtk.Label(label=dac.name, xalign=0, hexpand=True)
            connected = any(dac.matches_card(card) for card in cards)
            badge = Gtk.Label(label="接続中" if connected else "")
            if connected:
                badge.add_css_class("ok")
            delete = Gtk.Button(label="削除")
            delete.connect("clicked", lambda _b, dac_id=dac.id: self._delete_dac(dac_id))
            box.append(enabled)
            box.append(name)
            box.append(badge)
            box.append(delete)
            row.set_child(box)
            self.dac_list.append(row)
            if dac.id == self._selected_dac_id:
                self.dac_list.select_row(row)
        if self._selected_dac_id is None and self.settings.dacs:
            self._selected_dac_id = self.settings.dacs[0].id
            first = self.dac_list.get_row_at_index(0)
            if first:
                self.dac_list.select_row(first)
        self._updating = False
        self._load_dac_editor()

    def _on_dac_row(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._updating or row is None:
            return
        self._flush_dac_editor()
        self._selected_dac_id = getattr(row, "dac_id", None)
        self._load_dac_editor()
        self._refresh_preview()

    def _toggle_dac(self, dac_id: str, enabled: bool) -> None:
        dac = self.settings.dac(dac_id)
        if dac is None:
            return
        dac.enabled = enabled
        self._on_form_changed()

    def _delete_dac(self, dac_id: str) -> None:
        self._flush_dac_editor()
        self.settings.dacs = [d for d in self.settings.dacs if d.id != dac_id]
        if self._selected_dac_id == dac_id:
            self._selected_dac_id = self.settings.dacs[0].id if self.settings.dacs else None
        self._rebuild_dac_list()
        self._on_form_changed()
        self._flash("ok", "プロファイルをリストから外しました。保存するまで conf は変わりません。")

    def _add_connected_dacs(self) -> None:
        self._flush_dac_editor()
        cards = self._connected_cards()
        ids = {d.id for d in self.settings.dacs}
        added = []
        for card in cards:
            usb = bool(card.usb_id) or (card.pw_id or "").startswith("usb-")
            if not usb:
                continue
            if any(d.matches_card(card) for d in self.settings.dacs):
                continue
            profile = profiles.from_card(card, ids)
            ids.add(profile.id)
            self.settings.dacs.append(profile)
            added.append(profile.name)
        if not added:
            self._flash("warn", "追加できる新しい USB DAC はありません。すでにプロファイルがあるか、USB 機器がありません。")
            return
        self._selected_dac_id = self.settings.dacs[-1].id
        self._rebuild_dac_list()
        self._on_form_changed()
        self._flash("ok", "追加: " + "、".join(added))

    def _add_blank_dac(self) -> None:
        self._flush_dac_editor()
        ids = {d.id for d in self.settings.dacs}
        profile = DacProfile(
            id=profiles.unique_id("usb-dac", ids),
            name="USB DAC",
            pw_id="",
            native_rates=list(conf.ALL_RATES),
        )
        self.settings.dacs.append(profile)
        self._selected_dac_id = profile.id
        self._rebuild_dac_list()
        self._on_form_changed()
        self._flash("ok", "汎用プロファイルを追加しました。PipeWire ID を入れて保存してください。")

    def _split_suffixes(self, text: str) -> list[str]:
        parts = [p.strip() for p in text.replace("，", ",").split(",")]
        return [p for p in parts if p]

    def _flush_dac_editor(self) -> None:
        if self._updating or not self._selected_dac_id:
            return
        dac = self.settings.dac(self._selected_dac_id)
        if dac is None:
            return
        dac.name = self.ent_dac_name.get_text().strip() or dac.name
        dac.usb_id = self.ent_usb_id.get_text().strip()
        dac.pw_id = self.ent_pw_id.get_text().strip()
        dac.role = "onboard" if _selected(self.dd_dac_role) == "内蔵" else "dac"
        fmt = _selected(self.dd_dac_format)
        dac.format = "" if fmt.startswith("（") else fmt
        dac.headroom = int(self.spin_dac_headroom.get_value())
        dac.suspend_seconds = int(self.spin_dac_suspend.get_value())
        dac.pro_audio = self.sw_pro_audio.get_active()
        layout_label = _selected(self.dd_channel_layout)
        dac.channel_layout = next((lid for lid, lab in CHANNEL_LAYOUTS if lab == layout_label), "stereo")
        dac.fl_fr = dac.channel_layout == "stereo"
        dac.disable_inputs = self.sw_disable_inputs.get_active()
        dac.native_rates = [r for r, cb in self.dac_rate_checks.items() if cb.get_active()] or list(conf.ALL_RATES)
        suffixes = self._split_suffixes(self.ent_output_suffixes.get_text())
        dac.output_suffixes = suffixes or [""]
        dac.disable_suffixes = self._split_suffixes(self.ent_disable_suffixes.get_text())
        dac.notes = self.ent_dac_notes.get_text().strip()

    def _load_dac_editor(self) -> None:
        dac = self.settings.dac(self._selected_dac_id or "")
        self._updating = True
        if dac is None:
            self.ent_dac_name.set_text("")
            self.ent_pw_id.set_text("")
            self._updating = False
            return
        self.ent_dac_name.set_text(dac.name)
        self.ent_usb_id.set_text(dac.usb_id)
        self.ent_pw_id.set_text(dac.pw_id)
        _select(self.dd_dac_role, "内蔵" if dac.role == "onboard" else "DAC")
        _select(self.dd_dac_format, dac.format if dac.format in conf.FORMATS else "（指定しない）")
        self.spin_dac_headroom.set_value(dac.headroom)
        self.spin_dac_suspend.set_value(dac.suspend_seconds)
        self.sw_pro_audio.set_active(dac.pro_audio)
        layout_id = dac.resolved_layout()
        layout_label = next((lab for lid, lab in CHANNEL_LAYOUTS if lid == layout_id), LAYOUT_LABELS[1])
        _select(self.dd_channel_layout, layout_label)
        self.sw_disable_inputs.set_active(dac.disable_inputs)
        for rate, cb in self.dac_rate_checks.items():
            cb.set_active(rate in dac.native_rates)
        out = ",".join(s for s in dac.output_suffixes if s)
        self.ent_output_suffixes.set_text(out)
        self.ent_disable_suffixes.set_text(",".join(dac.disable_suffixes))
        self.ent_dac_notes.set_text(dac.notes)
        self._updating = False

    def _tick(self) -> bool:
        if self.get_mapped():
            self.refresh_live(quiet=True)
        return True

    def _reload(self) -> None:
        self.settings = conf.load()
        self._selected_dac_id = self.settings.dacs[0].id if self.settings.dacs else None
        self._to_widgets()
        self.refresh_live()
        self._flash("ok", "ディスク上の設定と機器状態を読み直しました。")

    def _on_preset(self, name: str) -> None:
        s = self._from_widgets()
        if name == "match_dac":
            matched = None
            for card in self._connected_cards():
                matched = next((d for d in s.dacs if d.matches_card(card) and d.role == "dac"), None)
                if matched:
                    s.allowed_rates = [r for r in conf.ALL_RATES if r in matched.native_rates] or list(matched.native_rates)
                    break
            if matched is None:
                self._flash("warn", "接続中の USB DAC プロファイルがありません。")
                return
        else:
            s = conf.apply_preset(name, s)
        self.settings = s
        self._to_widgets()
        label = next(lbl for key, lbl in PRESETS if key == name)
        self._flash("ok", f"プリセット「{label}」を画面に載せました。保存するまで PipeWire は変わりません。")

    def _on_form_changed(self) -> None:
        if self._updating:
            return
        self._flush_dac_editor()
        self._update_derived()
        self._refresh_preview()

    def _selected_rates(self) -> list[int]:
        return [rate for rate, cb in self.rate_checks.items() if cb.get_active()]

    def _from_widgets(self) -> conf.Settings:
        self._flush_dac_editor()
        s = self.settings.copy()
        s.allowed_rates = self._selected_rates() or list(conf.ALL_RATES)
        label = _selected(self.dd_clock_rate)
        for rate, name in conf.RATE_LABEL.items():
            if name == label:
                s.clock_rate = rate
                break
        s.quantum = int(_selected(self.dd_quantum) or s.quantum)
        s.min_quantum = int(_selected(self.dd_min_quantum) or s.min_quantum)
        s.max_quantum = int(_selected(self.dd_max_quantum) or s.max_quantum)
        s.quantum_limit = int(_selected(self.dd_quantum_limit) or s.quantum_limit)
        s.resample_quality = int(self.spin_quality.get_value())
        s.qbz_bitperfect = self.sw_qbz.get_active()
        return s

    def _to_widgets(self) -> None:
        self._updating = True
        s = self.settings
        for rate, cb in self.rate_checks.items():
            cb.set_active(rate in s.allowed_rates)
        _select(self.dd_clock_rate, conf.RATE_LABEL.get(s.clock_rate, "48 kHz"))
        _select(self.dd_quantum, str(s.quantum))
        _select(self.dd_min_quantum, str(s.min_quantum))
        _select(self.dd_max_quantum, str(s.max_quantum))
        _select(self.dd_quantum_limit, str(s.quantum_limit))
        self.spin_quality.set_value(s.resample_quality)
        self.sw_qbz.set_active(s.qbz_bitperfect)
        self._updating = False
        self._rebuild_dac_list()
        self._update_derived()
        self._refresh_preview()

    def _update_derived(self) -> None:
        s = self._from_widgets()
        lat = conf.latency_ms(s.quantum, s.clock_rate)
        self.lbl_latency.set_markup(
            f"バッファ <b>{lat:.2f} ms</b>（quantum {s.quantum} / {hardware.format_hz(s.clock_rate)}）"
        )
        max_rate = max(s.allowed_rates) if s.allowed_rates else s.clock_rate
        us = conf.usb_hs_period_us(s.min_quantum, max_rate)
        usb = (
            f"min-quantum {s.min_quantum} @ {hardware.format_hz(max_rate)} = {us:.0f} µs"
            f"（USB HS 間隔 125 µs）"
        )
        self.lbl_usb_clock.set_text(usb)
        self.lbl_usb_clock.remove_css_class("warn-text")
        self.lbl_usb_clock.remove_css_class("err-text")
        if us < 125:
            self.lbl_usb_clock.add_css_class("err-text")
            self.lbl_usb_clock.set_text(usb + " — 短すぎます。broken-pipe の原因になります。")
        elif us < 180:
            self.lbl_usb_clock.add_css_class("warn-text")
            self.lbl_usb_clock.set_text(usb + " — 余裕が少ないです。")

    def _refresh_preview(self) -> None:
        files = conf.render_all(self._from_widgets())
        keys = list(files.keys())
        labels = []
        for path in keys:
            mark = "（削除）" if files[path] is None else ""
            labels.append(str(path) + mark)
        prev = _selected(self.dd_files)
        self._file_keys = keys
        self.dd_files.set_model(Gtk.StringList.new(labels))
        if prev:
            _select(self.dd_files, prev)
        elif labels:
            self.dd_files.set_selected(0)
        self._show_selected_file()

    def _show_selected_file(self) -> None:
        idx = int(self.dd_files.get_selected())
        if idx < 0 or idx >= len(self._file_keys):
            return
        path = self._file_keys[idx]
        body = conf.render_all(self._from_widgets()).get(path)
        if body is None:
            body = f"# {path.name} は保存時に削除されます\n"
        self.tv_preview.get_buffer().set_text(body)

    def refresh_live(self, quiet: bool = False) -> None:
        live = pwstatus.collect()
        hardware.apply_profiles(live.cards, self.settings.dacs)
        usb_cards = [c for c in live.cards if c.usb_id or (c.pw_id or "").startswith("usb-")]
        named = next((c for c in usb_cards if c.profile_name), None)
        if named and named.profile_name:
            badge = f"{named.profile_name} 接続中"
        elif usb_cards:
            badge = "、".join(c.longname for c in usb_cards) + " 接続中"
        else:
            badge = "USB DAC 未接続"
        self.header_badge.set_text(badge)

        lines = []
        for card in live.cards:
            title = card.profile_name or card.longname
            bits = [title, f"hw:{card.index},{card.alsa_id}"]
            if card.usb_id:
                bits.append(card.usb_id)
            if card.pw_id:
                bits.append(card.pw_id)
            if card.sync:
                bits.append(card.sync)
            if card.formats:
                bits.append(card.formats[0])
            if card.rates:
                bits.append(f"{hardware.format_hz(card.rates[0])}–{hardware.format_hz(card.rates[-1])}")
            if card.playback:
                bits.append(card.playback)
            lines.append(" · ".join(bits))
        self.lbl_cards.set_markup("<b>機器</b>\n" + (_esc("\n".join(lines)) if lines else "ALSA カードがありません"))

        if live.clock_rate and live.clock_quantum:
            lat = conf.latency_ms(live.clock_quantum, live.clock_rate)
            graph = (
                f"{hardware.format_hz(live.clock_rate)} × quantum {live.clock_quantum}"
                f" = {lat:.2f} ms"
            )
            if live.force_rate:
                graph += f"  force-rate {hardware.format_hz(live.force_rate)}"
            if live.force_quantum:
                graph += f"  force-quantum {live.force_quantum}"
        else:
            graph = live.error or "PipeWire に接続できません"
        self.lbl_graph.set_markup("<b>グラフ</b>  " + _esc(graph))

        sink = live.default_sink_desc or live.default_sink or "（なし）"
        extra = []
        if live.sink_spec:
            extra.append(live.sink_spec)
        if live.sink_state:
            extra.append(live.sink_state)
        sink_line = sink if not extra else f"{sink}  ({', '.join(extra)})"
        self.lbl_sink.set_markup("<b>既定出力</b>  " + _esc(sink_line))

        if live.pcm_holder:
            self.lbl_holder.set_markup("<b>PCM 占有</b>  " + _esc(live.pcm_holder))
            self.lbl_holder.add_css_class("warn-text")
        else:
            self.lbl_holder.set_markup("<b>PCM 占有</b>  なし")
            self.lbl_holder.remove_css_class("warn-text")

        usb_bits = []
        for card in live.cards:
            if card.packet_interval_us:
                usb_bits.append(f"{card.profile_name or card.longname} 間隔 {card.packet_interval_us} µs")
        self.lbl_usb.set_markup("<b>USB</b>  " + _esc(" / ".join(usb_bits) if usb_bits else "USB オーディオ情報なし"))

        if not quiet and live.error:
            self._flash("warn", live.error)

        if not quiet:
            self._updating = True
            if live.force_rate:
                _select(self.dd_force_rate, conf.RATE_LABEL.get(live.force_rate, "自動"))
            else:
                self.dd_force_rate.set_selected(0)
            if live.force_quantum:
                _select(self.dd_force_quantum, str(live.force_quantum))
            else:
                self.dd_force_quantum.set_selected(0)
            self._updating = False

    def _apply_force_rate(self) -> None:
        label = _selected(self.dd_force_rate)
        rate = 0
        for value, name in conf.RATE_LABEL.items():
            if name == label:
                rate = value
                break
        ok, err = pwstatus.set_force_rate(rate)
        if ok:
            self._flash("ok", "自動追従に戻しました。" if rate == 0 else f"グラフを {label} に固定しました。")
            self.refresh_live()
        else:
            self._flash("err", err)

    def _apply_force_quantum(self) -> None:
        label = _selected(self.dd_force_quantum)
        quantum = 0 if label == "自動" else int(label)
        ok, err = pwstatus.set_force_quantum(quantum)
        if ok:
            self._flash("ok", "quantum を自動に戻しました。" if quantum == 0 else f"quantum を {quantum} に固定しました。")
            self.refresh_live()
        else:
            self._flash("err", err)

    def _save(self, restart: bool) -> None:
        s = self._from_widgets()
        errors = conf.validate(s)
        if errors:
            self._flash("err", " ".join(errors))
            return
        max_rate = max(s.allowed_rates)
        us = conf.usb_hs_period_us(s.min_quantum, max_rate)
        if us < 125:
            self._confirm(
                "USB 間隔を下回っています",
                f"min-quantum {s.min_quantum} @ {hardware.format_hz(max_rate)} は {us:.0f} µs で、"
                f"USB HS の 125 µs より短いです。保存を続けますか？",
                lambda: self._write(s, restart),
            )
            return
        if restart:
            self._confirm(
                "PipeWire を再起動します",
                "再生中の音はいったん切れます。設定ファイルを書いてから daemon を再起動します。",
                lambda: self._write(s, True),
            )
            return
        self._write(s, False)

    def _write(self, s: conf.Settings, restart: bool) -> None:
        try:
            backup = conf.save(s)
        except OSError as exc:
            self._flash("err", f"保存に失敗しました: {exc}")
            return
        self.settings = s
        if not restart:
            self._flash("ok", f"書きました。退避先 {backup} 。反映には「保存して再起動」が必要です。")
            return
        ok, err = pwstatus.restart_pipewire()
        if ok:
            self._flash("ok", "保存して PipeWire を再起動しました。")
            GLib.timeout_add_seconds(2, lambda: self.refresh_live() or False)
        else:
            self._flash("err", f"ファイルは保存しましたが再起動に失敗: {err}")

    def _confirm(self, title: str, detail: str, then) -> None:
        dialog = Gtk.AlertDialog()
        dialog.set_message(title)
        dialog.set_detail(detail)
        dialog.set_buttons(["キャンセル", "続ける"])
        dialog.set_cancel_button(0)
        dialog.set_default_button(1)
        dialog.set_modal(True)

        def done(dlg: Gtk.AlertDialog, result: Gio.AsyncResult) -> None:
            try:
                idx = dlg.choose_finish(result)
            except GLib.Error:
                return
            if idx == 1:
                then()

        dialog.choose(self, None, done)

    def _flash(self, kind: str, text: str, seconds: int = 7) -> None:
        self._banner_gen += 1
        gen = self._banner_gen
        for name in ("banner-ok", "banner-warn", "banner-err"):
            self.banner_box.remove_css_class(name)
        self.banner_box.add_css_class(f"banner-{kind}")
        self.banner_label.set_text(text)
        self.banner.set_reveal_child(True)
        GLib.timeout_add_seconds(seconds, lambda: self._hide_banner(gen))

    def _hide_banner(self, gen: int) -> bool:
        if gen == self._banner_gen:
            self.banner.set_reveal_child(False)
        return False


class Application(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self) -> None:  # noqa: N802
        win = self.props.active_window
        if win is None:
            win = MainWindow(self)
        win.present()


def main(argv: list[str] | None = None) -> int:
    GLib.set_prgname("pipewire-dac-gui")
    GLib.set_application_name("PipeWire DAC 設定")
    install_desktop_entry()
    app = Application()
    return app.run(argv if argv is not None else sys.argv)
