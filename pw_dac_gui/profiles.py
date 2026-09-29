from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .hardware import ALL_RATES, Card

PROFILE_DIR = Path.home() / ".config" / "pipewire-dac-gui"
PROFILE_PATH = PROFILE_DIR / "dacs.json"

_DOT = r"\\."

CHANNEL_LAYOUTS = [
    ("aux", "AUX（番号順）"),
    ("stereo", "Stereo FL/FR"),
    ("surround51", "5.1"),
    ("surround71", "7.1"),
]
LAYOUT_CHANNELS = {
    "aux": [],
    "stereo": ["FL", "FR"],
    "surround51": ["FL", "FR", "FC", "LFE", "RL", "RR"],
    "surround71": ["FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR"],
}
LAYOUT_LABELS = [label for _, label in CHANNEL_LAYOUTS]


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (s[:48] if s else "usb-dac")


def unique_id(base: str, existing: set[str]) -> str:
    slug = slugify(base)
    if slug not in existing:
        return slug
    n = 2
    while f"{slug}-{n}" in existing:
        n += 1
    return f"{slug}-{n}"


def wp_escape(text: str) -> str:
    return text.replace(".", _DOT)


def map_alsa_format(formats: list[str]) -> str:
    for fmt in formats:
        key = fmt.replace("SPECIAL ", "").strip()
        if key.startswith("DSD"):
            continue
        if key == "S32_LE":
            return "S32LE"
        if key == "S24_3LE":
            return "S24_3LE"
        if key == "S24_LE":
            return "S24LE"
        if key == "S16_LE":
            return "S16LE"
        compact = key.replace("_", "")
        if compact in {"S32LE", "S24LE", "S16LE", "S243LE"}:
            return "S24_3LE" if compact == "S243LE" else compact
    return "S32LE"


@dataclass
class DacProfile:
    id: str
    name: str
    enabled: bool = True
    role: str = "dac"
    usb_id: str = ""
    pw_id: str = ""
    native_rates: list[int] = field(default_factory=lambda: list(ALL_RATES))
    format: str = "S32LE"
    headroom: int = 256
    suspend_seconds: int = 3
    pro_audio: bool = True
    fl_fr: bool = True
    channel_layout: str = ""
    disable_inputs: bool = False
    output_suffixes: list[str] = field(default_factory=lambda: [""])
    disable_suffixes: list[str] = field(default_factory=list)
    notes: str = ""

    def resolved_layout(self) -> str:
        if self.channel_layout in LAYOUT_CHANNELS:
            return self.channel_layout
        return "stereo" if self.fl_fr else "aux"

    def channel_positions(self) -> list[str]:
        return list(LAYOUT_CHANNELS[self.resolved_layout()])

    def copy(self) -> "DacProfile":
        d = asdict(self)
        d["native_rates"] = list(self.native_rates)
        d["output_suffixes"] = list(self.output_suffixes)
        d["disable_suffixes"] = list(self.disable_suffixes)
        return DacProfile(**d)

    def device_rates(self, graph_rates: list[int]) -> list[int]:
        native = [r for r in self.native_rates if r in ALL_RATES] or list(ALL_RATES)
        overlap = [r for r in graph_rates if r in native]
        return overlap or native

    def matches_card(self, card: Card) -> bool:
        if self.usb_id and card.usb_id and self.usb_id.lower() == card.usb_id.lower():
            return True
        if self.pw_id and card.pw_id and self.pw_id == card.pw_id:
            return True
        if self.pw_id:
            blob = " ".join([card.pw_id or "", *card.node_names, card.longname])
            if self.pw_id in blob:
                return True
        return False


def builtins() -> list[DacProfile]:
    return [
        DacProfile(
            id="k9-pro",
            name="FiiO K9 Pro",
            usb_id="2972:0047",
            pw_id="usb-Guangzhou_FiiO_Electronics_Co.__Ltd_FiiO_K9_Pro-00",
            native_rates=list(ALL_RATES),
            format="S32LE",
            headroom=256,
            suspend_seconds=3,
            notes="USB HS ASYNC。DSD は MPD が hw:Pro,0 直結。",
        ),
        DacProfile(
            id="ca7",
            name="CUBILUX CA7",
            usb_id="",
            pw_id="usb-Generic_CUBILUX_CA7-00",
            native_rates=[44100, 48000, 96000, 176400, 192000, 352800, 384000],
            format="S24_3LE",
            headroom=256,
            suspend_seconds=-1,
            disable_inputs=True,
            output_suffixes=["pro-output-1"],
            disable_suffixes=["pro-output-0"],
            notes="アナログ 8ch（48 kHz）と入力は無効。88.2 kHz は非ネイティブ。",
        ),
        DacProfile(
            id="alc256",
            name="内蔵 ALC256",
            role="onboard",
            pw_id="pci-0000_00_1f.3",
            native_rates=[44100, 48000],
            format="",
            headroom=-1,
            suspend_seconds=-1,
            pro_audio=False,
            fl_fr=False,
            channel_layout="aux",
            notes="ハイレゾグラフで内蔵アナログが失敗しないよう 44.1/48 kHz に制限。",
        ),
    ]


def from_card(card: Card, existing_ids: set[str]) -> DacProfile:
    name = card.longname.strip() or f"USB DAC {card.index}"
    pid = unique_id(name if not name.lower().startswith("usb") else (card.usb_id or name), existing_ids)
    headroom = 256 if (card.packet_interval_us or 125) <= 125 else 128
    rates = [r for r in ALL_RATES if r in card.rates] or list(card.rates) or list(ALL_RATES)
    return DacProfile(
        id=pid,
        name=name,
        usb_id=card.usb_id or "",
        pw_id=card.pw_id or "",
        native_rates=rates,
        format=map_alsa_format(card.formats),
        headroom=headroom,
        suspend_seconds=3,
        notes=f"{card.sync or 'USB'} · hw:{card.index},{card.alsa_id}",
    )


def _from_dict(raw: dict) -> DacProfile:
    base = DacProfile(id="usb-dac", name="USB DAC")
    allowed = {k: v for k, v in raw.items() if k in base.__dataclass_fields__}
    p = DacProfile(**{**asdict(base), **allowed})
    p.native_rates = [int(r) for r in p.native_rates]
    p.output_suffixes = [str(x) for x in p.output_suffixes] or [""]
    p.disable_suffixes = [str(x) for x in p.disable_suffixes]
    if p.channel_layout not in LAYOUT_CHANNELS:
        p.channel_layout = "stereo" if p.fl_fr else "aux"
    p.fl_fr = p.channel_layout == "stereo"
    return p


def load_dacs() -> list[DacProfile] | None:
    if not PROFILE_PATH.exists():
        return None
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    items = data.get("dacs") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        return None
    out = [_from_dict(x) for x in items if isinstance(x, dict)]
    return out or None


def save_dacs(dacs: list[DacProfile]) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"dacs": [asdict(d) for d in dacs]}
    tmp = PROFILE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(PROFILE_PATH)


def match_profile(card: Card, dacs: list[DacProfile]) -> DacProfile | None:
    for dac in dacs:
        if dac.matches_card(card):
            return dac
    return None


def node_match(pw_id: str, kind: str, suffix: str = "") -> str:
    esc = wp_escape(pw_id)
    if kind == "card":
        return f"~^alsa_card{_DOT}{esc}$"
    if kind == "onboard":
        return f"~^alsa_.*{_DOT}{esc}{_DOT}"
    if kind == "input":
        return f"~^alsa_input{_DOT}{esc}{_DOT}"
    if suffix:
        return f"~^alsa_output{_DOT}{esc}{_DOT}{wp_escape(suffix)}($|{_DOT})"
    return f"~^alsa_output{_DOT}{esc}{_DOT}"
