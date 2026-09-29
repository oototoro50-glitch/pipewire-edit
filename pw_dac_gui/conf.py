from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .hardware import ALL_RATES, FORMATS, QUANTUMS, RATE_LABEL
from .profiles import DacProfile, builtins, load_dacs, node_match, save_dacs

HOME = Path.home()
PW = HOME / ".config" / "pipewire"
WP = HOME / ".config" / "wireplumber"
CLOCK_PATH = PW / "pipewire.conf.d" / "10-clock.conf"
RATES_PATH = PW / "pipewire.conf.d" / "99-allowed-rates.conf"
CLIENT_RESAMPLE_PATH = PW / "client.conf.d" / "10-resample.conf"
PULSE_RESAMPLE_PATH = PW / "pipewire-pulse.conf.d" / "10-resample.conf"
QBZ_PATH = PW / "client.conf.d" / "99-qbz-bitperfect.conf"
LEGACY_K9 = WP / "wireplumber.conf.d" / "99-k9-pro.conf"
LEGACY_CA7 = WP / "wireplumber.conf.d" / "99-ca7.conf"


def dac_conf_path(profile_id: str) -> Path:
    return WP / "wireplumber.conf.d" / f"50-dac-{profile_id}.conf"


@dataclass
class Settings:
    clock_rate: int = 48000
    quantum: int = 512
    min_quantum: int = 128
    max_quantum: int = 2048
    quantum_limit: int = 8192
    allowed_rates: list[int] = field(default_factory=lambda: list(ALL_RATES))
    resample_quality: int = 10
    qbz_bitperfect: bool = True
    dacs: list[DacProfile] = field(default_factory=builtins)

    def copy(self) -> "Settings":
        out = Settings()
        out.clock_rate = self.clock_rate
        out.quantum = self.quantum
        out.min_quantum = self.min_quantum
        out.max_quantum = self.max_quantum
        out.quantum_limit = self.quantum_limit
        out.allowed_rates = list(self.allowed_rates)
        out.resample_quality = self.resample_quality
        out.qbz_bitperfect = self.qbz_bitperfect
        out.dacs = [d.copy() for d in self.dacs]
        return out

    def dac(self, dac_id: str) -> DacProfile | None:
        for item in self.dacs:
            if item.id == dac_id:
                return item
        return None


def apply_preset(name: str, settings: Settings) -> Settings:
    s = settings.copy()
    s.clock_rate = 48000
    s.max_quantum = 2048
    s.resample_quality = 10
    if name == "stable":
        s.quantum = 512
        s.min_quantum = 128
    elif name == "lowlat":
        s.quantum = 256
        s.min_quantum = 128
        for dac in s.dacs:
            if dac.role == "dac" and dac.headroom > 0:
                dac.headroom = min(dac.headroom, 128)
    elif name == "hires":
        s.quantum = 512
        s.min_quantum = 128
        rates: list[int] = []
        for dac in s.dacs:
            if dac.enabled and dac.role == "dac":
                for rate in dac.native_rates:
                    if rate not in rates:
                        rates.append(rate)
        s.allowed_rates = [r for r in ALL_RATES if r in rates] or list(ALL_RATES)
    return s


def usb_hs_period_us(min_quantum: int, max_rate: int) -> float:
    if max_rate <= 0:
        return 0.0
    return min_quantum / max_rate * 1_000_000.0


def latency_ms(quantum: int, rate: int) -> float:
    if rate <= 0:
        return 0.0
    return quantum / rate * 1000.0


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _assign_int(text: str, key: str, default: int) -> int:
    m = re.search(rf"^\s*{re.escape(key)}\s*=\s*(-?\d+)", text, re.M)
    return int(m.group(1)) if m else default


def _assign_str(text: str, key: str, default: str) -> str:
    m = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else default


def _assign_ints(text: str, key: str, default: list[int]) -> list[int]:
    m = re.search(rf"{re.escape(key)}\s*=\s*\[([^\]]*)\]", text)
    if not m:
        return list(default)
    values = [int(n) for n in re.findall(r"\d+", m.group(1))]
    return values or list(default)


def _overlay_legacy(dacs: list[DacProfile]) -> None:
    k9_text = _read(LEGACY_K9)
    ca7_text = _read(LEGACY_CA7)
    for dac in dacs:
        text = ""
        if dac.id == "k9-pro":
            text = k9_text
        elif dac.id == "ca7":
            text = ca7_text
        path = dac_conf_path(dac.id)
        if path.exists():
            text = _read(path)
        if not text:
            continue
        if dac.format:
            dac.format = _assign_str(text, "audio.format", dac.format)
        if dac.headroom >= 0:
            dac.headroom = _assign_int(text, "api.alsa.headroom", dac.headroom)
        if dac.suspend_seconds >= 0:
            dac.suspend_seconds = _assign_int(text, "session.suspend-timeout-seconds", dac.suspend_seconds)


def load() -> Settings:
    clock = _read(CLOCK_PATH)
    rates = _read(RATES_PATH)
    client = _read(CLIENT_RESAMPLE_PATH)
    s = Settings()
    s.clock_rate = _assign_int(clock, "default.clock.rate", s.clock_rate)
    s.quantum = _assign_int(clock, "default.clock.quantum", s.quantum)
    s.min_quantum = _assign_int(clock, "default.clock.min-quantum", s.min_quantum)
    s.max_quantum = _assign_int(clock, "default.clock.max-quantum", s.max_quantum)
    s.quantum_limit = _assign_int(clock, "default.clock.quantum-limit", s.quantum_limit)
    s.allowed_rates = _assign_ints(rates, "default.clock.allowed-rates", s.allowed_rates)
    s.resample_quality = _assign_int(client, "resample.quality", s.resample_quality)
    s.qbz_bitperfect = QBZ_PATH.exists()
    stored = load_dacs()
    s.dacs = stored or builtins()
    if stored is None:
        _overlay_legacy(s.dacs)
    s.allowed_rates = [r for r in ALL_RATES if r in s.allowed_rates] or list(ALL_RATES)
    if s.clock_rate not in s.allowed_rates:
        s.clock_rate = s.allowed_rates[0]
    return s


def _spa_rates(rates: list[int]) -> str:
    return "[ " + " ".join(str(r) for r in rates) + " ]"


def _wp_rates(rates: list[int]) -> str:
    return "[ " + ", ".join(str(r) for r in rates) + " ]"


def render_clock(s: Settings) -> str:
    return f"""# DSP clock. USB HS packet interval is 125 us; min-quantum 16 at 192 kHz
# is 83 us and caused ALSA broken-pipe recoveries.
# Written by pipewire-dac-gui. Restart:
#   systemctl --user restart pipewire pipewire-pulse wireplumber
context.properties = {{
    default.clock.rate          = {s.clock_rate}
    default.clock.quantum       = {s.quantum}
    default.clock.min-quantum   = {s.min_quantum}
    default.clock.max-quantum   = {s.max_quantum}
    default.clock.quantum-limit = {s.quantum_limit}
    clock.power-of-two-quantum  = true
    cpu.zero.denormals          = true
}}
"""


def render_rates(s: Settings) -> str:
    names = ", ".join(d.name for d in s.dacs if d.enabled)
    return f"""# Graph sample rates. Enabled DACs: {names or "(none)"}.
# A missing rate here keeps the UI playing while the DAC stays silent
# if a client force-rates to it.
# Written by pipewire-dac-gui.
context.properties = {{
    default.clock.allowed-rates = {_spa_rates(s.allowed_rates)}
}}
"""


def render_client_resample(s: Settings) -> str:
    return f"""# High-quality resampler when conversion is unavoidable.
# Written by pipewire-dac-gui.
stream.properties = {{
    resample.quality = {s.resample_quality}
}}
"""


def render_pulse_resample(s: Settings) -> str:
    return f"""# Pulse/Chromium clients: same sinc quality as native clients.
# Written by pipewire-dac-gui.
stream.properties = {{
    resample.quality = {s.resample_quality}
}}

pulse.properties = {{
    pulse.min.req     = {s.quantum}/{s.clock_rate}
    pulse.min.quantum = {s.quantum}/{s.clock_rate}
}}
"""


def _props_block(dac: DacProfile, s: Settings) -> str:
    rates = _wp_rates(dac.device_rates(s.allowed_rates))
    lines = []
    pos = dac.channel_positions()
    if pos:
        lines.append("        audio.position = [ " + ", ".join(pos) + " ]")
    layout = dac.resolved_layout()
    if layout in ("surround51", "surround71"):
        lines.append("        channelmix.upmix = false")
        lines.append("        channelmix.mix-lfe = false")
    lines.append(f"        audio.allowed-rates = {rates}")
    if dac.format:
        lines.append(f'        audio.format = "{dac.format}"')
    lines.append(f"        resample.quality = {s.resample_quality}")
    if dac.suspend_seconds >= 0:
        lines.append(f"        session.suspend-timeout-seconds = {dac.suspend_seconds}")
        lines.append("        node.pause-on-idle = true")
    if dac.headroom >= 0:
        lines.append(f"        api.alsa.headroom = {dac.headroom}")
    return "\n".join(lines)


def render_dac(dac: DacProfile, s: Settings) -> str:
    if not dac.pw_id:
        return f"# {dac.name}: PipeWire ID が空なので規則を書いていません。\n"
    rules: list[str] = []
    note = dac.notes.replace("\n", " ").strip()
    if dac.role == "onboard":
        match = node_match(dac.pw_id, "onboard")
        rates = _wp_rates(dac.native_rates)
        rules.append(
            f"""  {{
    matches = [
      {{ node.name = "{match}" }}
    ]
    actions = {{
      update-props = {{
        audio.allowed-rates = {rates}
      }}
    }}
  }}"""
        )
    else:
        if dac.pro_audio:
            card = node_match(dac.pw_id, "card")
            rules.append(
                f"""  {{
    matches = [
      {{ device.name = "{card}" }}
    ]
    actions = {{
      update-props = {{
        device.profile = "pro-audio"
      }}
    }}
  }}"""
            )
        disable_matches = []
        for suffix in dac.disable_suffixes:
            disable_matches.append(node_match(dac.pw_id, "output", suffix))
        if dac.disable_inputs:
            disable_matches.append(node_match(dac.pw_id, "input"))
        if disable_matches:
            inner = "\n".join(f'      {{ node.name = "{m}" }}' for m in disable_matches)
            rules.append(
                f"""  {{
    matches = [
{inner}
    ]
    actions = {{
      update-props = {{
        node.disabled = true
      }}
    }}
  }}"""
            )
        suffixes = dac.output_suffixes or [""]
        for suffix in suffixes:
            match = node_match(dac.pw_id, "output", suffix)
            rules.append(
                f"""  {{
    matches = [
      {{ node.name = "{match}" }}
    ]
    actions = {{
      update-props = {{
        node.description = "{dac.name.replace(chr(34), "")}"
{_props_block(dac, s)}
      }}
    }}
  }}"""
            )
    joined = "\n".join(rules)
    return f"""# {dac.name} ({dac.usb_id or dac.pw_id})
# {note}
# Dots in regex MUST be escaped.
# Written by pipewire-dac-gui.
monitor.alsa.rules = [
{joined}
]
"""


def render_qbz() -> str:
    return """# QBZ: skip resampling when the graph already matches the track.
# Do not disable channelmix — USB DACs often use FL/FR; QBZ may send AUX.
# Written by pipewire-dac-gui.
stream.rules = [
    {
        matches = [
            { application.process.binary = "qbz" }
            { application.name = "PipeWire ALSA [qbz]" }
        ]
        actions = {
            update-props = {
                resample.disabled = true
            }
        }
    }
]
"""


def _managed_wp_files() -> list[Path]:
    found = []
    folder = WP / "wireplumber.conf.d"
    if folder.exists():
        found.extend(sorted(folder.glob("50-dac-*.conf")))
    for legacy in (LEGACY_K9, LEGACY_CA7):
        found.append(legacy)
    return found


def render_all(s: Settings) -> dict[Path, str | None]:
    files: dict[Path, str | None] = {
        CLOCK_PATH: render_clock(s),
        RATES_PATH: render_rates(s),
        CLIENT_RESAMPLE_PATH: render_client_resample(s),
        PULSE_RESAMPLE_PATH: render_pulse_resample(s),
        QBZ_PATH: render_qbz() if s.qbz_bitperfect else None,
    }
    for path in _managed_wp_files():
        files[path] = None
    for dac in s.dacs:
        if dac.enabled and dac.pw_id:
            files[dac_conf_path(dac.id)] = render_dac(dac, s)
    return files


def validate(s: Settings) -> list[str]:
    errors: list[str] = []
    if not s.allowed_rates:
        errors.append("サンプルレートを 1 つ以上選んでください。")
    if s.clock_rate not in s.allowed_rates:
        errors.append("既定レートは許可レートに含まれている必要があります。")
    if not (s.min_quantum <= s.quantum <= s.max_quantum):
        errors.append("min-quantum ≤ quantum ≤ max-quantum にしてください。")
    if s.quantum_limit < s.max_quantum:
        errors.append("quantum-limit は max-quantum 以上にしてください。")
    ids = [d.id for d in s.dacs]
    if len(ids) != len(set(ids)):
        errors.append("DAC プロファイルの ID が重複しています。")
    for dac in s.dacs:
        if dac.enabled and dac.role == "dac" and not dac.pw_id:
            errors.append(f"{dac.name} の PipeWire ID が空です。接続中から追加するか ID を入力してください。")
    return errors


def save(s: Settings) -> Path:
    save_dacs(s.dacs)
    files = render_all(s)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = PW / "gui-backup" / stamp
    backup.mkdir(parents=True, exist_ok=True)
    for path, body in files.items():
        if path.exists():
            dest = backup / path.name
            shutil.copy2(path, dest)
        if body is None:
            if path.exists():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
    return backup
