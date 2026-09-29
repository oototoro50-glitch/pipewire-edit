from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PROC_ASOUND = Path("/proc/asound")

ALL_RATES = [44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000]
QUANTUMS = [64, 128, 256, 512, 1024, 2048, 4096, 8192]
FORMATS = ["S32LE", "S24_3LE", "S24LE", "S16LE"]

RATE_LABEL = {
    44100: "44.1 kHz",
    48000: "48 kHz",
    88200: "88.2 kHz",
    96000: "96 kHz",
    176400: "176.4 kHz",
    192000: "192 kHz",
    352800: "352.8 kHz",
    384000: "384 kHz",
}


@dataclass
class Card:
    index: int
    alsa_id: str
    name: str
    longname: str
    kind: str = "other"
    usb_id: str | None = None
    sync: str | None = None
    formats: list[str] = field(default_factory=list)
    rates: list[int] = field(default_factory=list)
    packet_interval_us: int | None = None
    playback: str | None = None
    pw_id: str | None = None
    node_names: list[str] = field(default_factory=list)
    profile_id: str | None = None
    profile_name: str | None = None


def _classify_builtin(blob: str) -> str:
    if re.search(r"FiiO.*K9|K9 Pro", blob, re.I):
        return "k9"
    if re.search(r"CUBILUX|\bCA7\b", blob, re.I):
        return "ca7"
    if re.search(r"PCH|ALC256|HDA Intel|sof-hda", blob, re.I):
        return "intel"
    return "other"


def probe_cards() -> list[Card]:
    cards_path = PROC_ASOUND / "cards"
    if not cards_path.exists():
        return []
    text = cards_path.read_text(errors="replace")
    found: list[Card] = []
    for m in re.finditer(
        r"^\s*(\d+)\s+\[([^\]]+)\]:\s+(\S+)\s+-\s+(.+)$",
        text,
        re.M,
    ):
        idx = int(m.group(1))
        alsa_id = m.group(2).strip()
        name = m.group(3).strip()
        longname = m.group(4).strip()
        blob = f"{alsa_id} {name} {longname}"
        extra = PROC_ASOUND / f"card{idx}" / "id"
        if extra.exists():
            blob += " " + extra.read_text(errors="replace")
        card = Card(
            index=idx,
            alsa_id=alsa_id,
            name=name,
            longname=longname,
            kind=_classify_builtin(blob),
        )
        usbid = PROC_ASOUND / f"card{idx}" / "usbid"
        if usbid.exists():
            card.usb_id = usbid.read_text(errors="replace").strip()
        _fill_stream(card)
        _fill_pcm_state(card)
        found.append(card)
    enrich_cards(found)
    return found


def _fill_stream(card: Card) -> None:
    card_dir = PROC_ASOUND / f"card{card.index}"
    streams = sorted(card_dir.glob("stream*"))
    if not streams:
        return
    text = streams[0].read_text(errors="replace")
    sm = re.search(r"Status:\s+(\S+)", text)
    if sm:
        card.playback = sm.group(1)
    card.formats = [f.strip() for f in re.findall(r"Format:\s+(.+)", text)]
    rates: list[int] = []
    for rm in re.finditer(r"Rates:\s+([0-9, ]+)", text):
        for n in re.findall(r"\d+", rm.group(1)):
            v = int(n)
            if v not in rates:
                rates.append(v)
    card.rates = rates
    pm = re.search(r"Data packet interval:\s+(\d+)\s+us", text)
    if pm:
        card.packet_interval_us = int(pm.group(1))
    if "ASYNC" in text:
        card.sync = "ASYNC"
    elif "ADAPTIVE" in text:
        card.sync = "ADAPTIVE"
    elif re.search(r"\bSYNC\b", text):
        card.sync = "SYNC"


def _fill_pcm_state(card: Card) -> None:
    status = PROC_ASOUND / f"card{card.index}" / "pcm0p" / "sub0" / "status"
    if not status.exists():
        return
    text = status.read_text(errors="replace").strip()
    if text.lower() == "closed" or re.search(r"state:\s+CLOSED", text, re.I):
        if not card.playback:
            card.playback = "Stop"
        return
    sm = re.search(r"state:\s+(\S+)", text, re.I)
    if sm:
        card.playback = sm.group(1)


def pcm_holder(card_index: int) -> str | None:
    status = PROC_ASOUND / f"card{card_index}" / "pcm0p" / "sub0" / "status"
    if not status.exists():
        return None
    text = status.read_text(errors="replace")
    if text.strip().lower() == "closed":
        return None
    m = re.search(r"owner_pid\s*:\s*(\d+)", text)
    if not m:
        return None
    pid = m.group(1)
    comm_path = Path(f"/proc/{pid}/comm")
    comm = comm_path.read_text(errors="replace").strip() if comm_path.exists() else "?"
    return f"{comm} (pid {pid})"


def format_hz(rate: int) -> str:
    if rate % 1000 == 0:
        return f"{rate // 1000} kHz"
    return f"{rate / 1000:.1f} kHz".replace(".0", "")


def _pw_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def enrich_cards(cards: list[Card]) -> None:
    if not cards:
        return
    try:
        proc = subprocess.run(
            ["pw-dump"],
            capture_output=True,
            text=True,
            timeout=8,
            env=_pw_env(),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return
        data = json.loads(proc.stdout)
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return
    by_index: dict[str, dict] = {}
    for obj in data:
        props = (obj.get("info") or {}).get("props") or {}
        alsa_card = props.get("alsa.card")
        if alsa_card is None:
            continue
        key = str(alsa_card)
        slot = by_index.setdefault(key, {"pw_id": None, "nodes": []})
        name = props.get("device.name") or props.get("node.name") or ""
        if name:
            slot["nodes"].append(name)
        if name.startswith("alsa_card."):
            slot["pw_id"] = name[len("alsa_card.") :]
        elif name.startswith("alsa_output.") and slot["pw_id"] is None:
            rest = name[len("alsa_output.") :]
            slot["pw_id"] = rest.rsplit(".", 1)[0] if "." in rest else rest
    for card in cards:
        slot = by_index.get(str(card.index))
        if not slot:
            continue
        card.pw_id = slot["pw_id"]
        card.node_names = list(dict.fromkeys(slot["nodes"]))


def apply_profiles(cards: list[Card], dacs) -> None:
    for card in cards:
        for dac in dacs:
            if dac.matches_card(card):
                card.profile_id = dac.id
                card.profile_name = dac.name
                card.kind = dac.id
                break
