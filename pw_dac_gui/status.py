from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field

from .hardware import Card, pcm_holder, probe_cards


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def run(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env(),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:
        return 1, "", str(exc)


@dataclass
class LiveStatus:
    cards: list[Card] = field(default_factory=list)
    clock_rate: int | None = None
    clock_quantum: int | None = None
    force_rate: int | None = None
    force_quantum: int | None = None
    allowed_rates: list[int] = field(default_factory=list)
    default_sink: str | None = None
    default_sink_desc: str | None = None
    sink_spec: str | None = None
    sink_state: str | None = None
    pcm_holder: str | None = None
    error: str | None = None


def _parse_metadata(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"key:'([^']+)'\s+value:'([^']*)'", text):
        out[m.group(1)] = m.group(2)
    return out


def _as_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_int_list(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(n) for n in re.findall(r"\d+", value)]


def collect() -> LiveStatus:
    status = LiveStatus(cards=probe_cards())
    for card in status.cards:
        if card.usb_id or (card.pw_id or "").startswith("usb-"):
            holder = pcm_holder(card.index)
            if holder:
                status.pcm_holder = f"{card.longname}: {holder}"
                break

    code, meta_out, meta_err = run(["pw-metadata", "-n", "settings"])
    if code != 0:
        status.error = meta_err.strip() or "PipeWire の metadata を読めません"
        _fill_from_pactl(status)
        return status

    meta = _parse_metadata(meta_out)
    status.clock_rate = _as_int(meta.get("clock.rate"))
    status.clock_quantum = _as_int(meta.get("clock.quantum"))
    status.force_rate = _as_int(meta.get("clock.force-rate")) or None
    status.force_quantum = _as_int(meta.get("clock.force-quantum")) or None
    status.allowed_rates = _parse_int_list(meta.get("clock.allowed-rates"))
    _fill_from_pactl(status)
    if not status.default_sink:
        _fill_from_dump(status)
    return status


def _fill_from_pactl(status: LiveStatus) -> None:
    code, name, _ = run(["pactl", "get-default-sink"])
    if code == 0:
        status.default_sink = name.strip() or None
    code, text, _ = run(["pactl", "list", "sinks"])
    if code != 0 or not text:
        return
    blocks = re.split(r"\nSink #", text)
    target = status.default_sink
    chosen = None
    for block in blocks:
        nm = re.search(r"^\s*Name:\s+(\S+)", block, re.M)
        if not nm:
            continue
        if target and nm.group(1) != target:
            continue
        chosen = block
        if target:
            break
    if chosen is None:
        return
    dm = re.search(r"^\s*Description:\s+(.+)$", chosen, re.M)
    if dm:
        status.default_sink_desc = dm.group(1).strip()
    sm = re.search(r"^\s*State:\s+(\S+)", chosen, re.M)
    if sm:
        status.sink_state = sm.group(1)
    spec = re.search(r"^\s*Sample Specification:\s+(.+)$", chosen, re.M)
    if spec:
        status.sink_spec = spec.group(1).strip()
    if not status.default_sink:
        nm = re.search(r"^\s*Name:\s+(\S+)", chosen, re.M)
        if nm:
            status.default_sink = nm.group(1)


def _fill_from_dump(status: LiveStatus) -> None:
    code, text, _ = run(["pw-dump"], timeout=8.0)
    if code != 0 or not text.strip():
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return
    for obj in data:
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        if props.get("node.nick") or props.get("node.description"):
            status.default_sink = props.get("node.name")
            status.default_sink_desc = props.get("node.description") or props.get("node.nick")
            break


def set_force_rate(rate: int) -> tuple[bool, str]:
    code, _, err = run(["pw-metadata", "-n", "settings", "0", "clock.force-rate", str(rate)])
    if code != 0:
        return False, err.strip() or "force-rate の設定に失敗しました"
    return True, ""


def set_force_quantum(quantum: int) -> tuple[bool, str]:
    code, _, err = run(["pw-metadata", "-n", "settings", "0", "clock.force-quantum", str(quantum)])
    if code != 0:
        return False, err.strip() or "force-quantum の設定に失敗しました"
    return True, ""


def restart_pipewire() -> tuple[bool, str]:
    code, out, err = run(
        [
            "systemctl",
            "--user",
            "restart",
            "pipewire.socket",
            "pipewire.service",
            "pipewire-pulse.service",
            "wireplumber.service",
        ],
        timeout=20.0,
    )
    if code != 0:
        return False, (err or out).strip() or "再起動に失敗しました"
    return True, ""
