"""
Hardware detection and backend selection.

Nothing else in VAJREN knows what GPU it is running on. This module answers three
questions and writes the answer to config/hardware.json:

  1. Which llama.cpp backend?      (CUDA / ROCm / Vulkan / Metal / SYCL / CPU)
  2. Which features are safe?      (some are backend-dependent landmines)
  3. Which model tier fits?        (by usable VRAM, or unified memory on Apple)

Design rule, borrowed from Ollama because it is the pattern that actually survives
contact with real machines: **predict, then verify, then react.** The formula gives
a first guess so the benchmark sweep starts near the answer; llama-bench gives the
real number; an OOM at load time is caught and retried smaller rather than crashing.

Detection is read-only and runs unattended. Anything that would *change* the
machine — installing a driver, a toolkit, or a service — is deliberately not here.
See config/policy.yaml and J-025.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "config" / "hardware.json"


# --------------------------------------------------------------------------- #
#  Data
# --------------------------------------------------------------------------- #
@dataclass
class GPU:
    vendor: str          # nvidia | amd | intel | apple | none
    name: str
    vram_gb: float
    extra: dict = field(default_factory=dict)


@dataclass
class Profile:
    os: str
    arch: str
    cpu: str
    cores: int
    ram_gb: float
    gpus: list[GPU]
    backend: str             # cuda | hip | vulkan | metal | sycl | cpu
    backend_reason: str
    budget_gb: float         # usable weight budget: VRAM + a slice of RAM
    tier: str                # cpu | tiny | small | mid | large | xlarge
    features: dict[str, bool]
    feature_notes: dict[str, str]

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2)


# --------------------------------------------------------------------------- #
#  Probes — all read-only
# --------------------------------------------------------------------------- #
def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def _nvidia() -> list[GPU]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,compute_cap",
                "--format=csv,noheader,nounits"])
    gpus = []
    for line in filter(None, (l.strip() for l in out.splitlines())):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            gpus.append(GPU("nvidia", parts[0], round(float(parts[1]) / 1024, 1),
                            {"compute_cap": parts[2] if len(parts) > 2 else "?"}))
    return gpus


def _amd() -> list[GPU]:
    """
    ROCm presence is a separate question from 'is this an AMD card'. rocminfo
    listing a gfx target is the only trustworthy signal that HIP will actually work.
    """
    gpus: list[GPU] = []
    info = _run(["rocminfo"]) if shutil.which("rocminfo") else ""
    gfx = sorted(set(re.findall(r"gfx\d{3,4}", info)))
    smi = _run(["rocm-smi", "--showmeminfo", "vram", "--csv"]) if shutil.which("rocm-smi") else ""
    vram = 0.0
    m = re.search(r"(\d{9,})", smi)
    if m:
        vram = round(int(m.group(1)) / 1024**3, 1)
    if gfx:
        gpus.append(GPU("amd", "/".join(gfx), vram, {"gfx": gfx, "rocm_visible": True}))
    return gpus


_WIN_VRAM_PS = r"""
$k = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
Get-ChildItem $k -ErrorAction SilentlyContinue | ForEach-Object {
  $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
  if ($p.DriverDesc) {
    $bytes = $p.'HardwareInformation.qwMemorySize'
    if (-not $bytes) { $bytes = 0 }
    "$($p.DriverDesc)|$bytes"
  }
}
"""


def _windows_gpus() -> list[GPU]:
    """
    Windows fallback.

    WMI's Win32_VideoController.AdapterRAM is a signed 32-bit field: it caps at
    4 GB and reports nonsense for any modern card. The registry key
    HardwareInformation.qwMemorySize is 64-bit and correct - use that.
    """
    out = _run(["powershell", "-NoProfile", "-Command", _WIN_VRAM_PS])
    gpus = []
    for line in filter(None, (l.strip() for l in out.splitlines())):
        if "|" not in line:
            continue
        name, _, raw = line.rpartition("|")
        try:
            vram = round(int(raw) / 1024**3, 1)
        except ValueError:
            vram = 0.0
        low = name.lower()
        vendor = ("nvidia" if any(k in low for k in ("nvidia", "geforce", "rtx", "quadro"))
                  else "amd" if any(k in low for k in ("radeon", "amd"))
                  else "intel" if any(k in low for k in ("intel", "arc", "iris"))
                  else "unknown")
        integrated = vram < 2.0 and vendor in ("amd", "intel")
        gpus.append(GPU(vendor, name.strip(), vram,
                        {"source": "registry", "integrated": integrated}))
    return gpus


def _vulkan_ok() -> bool:
    if shutil.which("vulkaninfo"):
        return "deviceName" in _run(["vulkaninfo", "--summary"])
    return Path(r"C:\Windows\System32\vulkan-1.dll").exists()


def _apple() -> list[GPU]:
    if platform.system() != "Darwin":
        return []
    mem = _run(["sysctl", "-n", "hw.memsize"]).strip()
    total = round(int(mem) / 1024**3, 1) if mem.isdigit() else 0.0
    chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    # Unified memory: the GPU can address most of RAM. Reserve ~25% for the OS.
    return [GPU("apple", chip or "Apple Silicon", round(total * 0.75, 1),
                {"unified": True, "total_ram_gb": total})]


def _ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / 1024**3, 1)
    except Exception:
        pass
    if platform.system() == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
        d = re.search(r"\d+", out)
        return round(int(d.group()) / 1024**3, 1) if d else 0.0
    if platform.system() == "Darwin":
        m = _run(["sysctl", "-n", "hw.memsize"]).strip()
        return round(int(m) / 1024**3, 1) if m.isdigit() else 0.0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                return round(int(re.search(r"\d+", line).group()) / 1024**2, 1)
    except Exception:
        pass
    return 0.0


# --------------------------------------------------------------------------- #
#  Backend selection
# --------------------------------------------------------------------------- #
# RDNA1/RDNA2 and older. AMD's HIP SDK does not support these, on any OS it
# currently ships. Do not attempt ROCm on them regardless of what rocminfo says.
ROCM_UNSUPPORTED = {"gfx1010", "gfx1011", "gfx1012",
                    "gfx1030", "gfx1031", "gfx1032", "gfx1034", "gfx1035", "gfx1036"}


def choose_backend(gpus: list[GPU], os_name: str) -> tuple[str, str]:
    if not gpus:
        return "cpu", "no GPU detected"

    # An integrated GPU sharing system RAM is not the card we want to plan around.
    discrete = [g for g in gpus if not g.extra.get("integrated")] or gpus
    primary = max(discrete, key=lambda g: g.vram_gb)

    if primary.vendor == "apple":
        return "metal", "Apple Silicon — Metal is default and unified memory is the budget"

    if primary.vendor == "nvidia":
        return "cuda", f"NVIDIA {primary.name} — CUDA is the fastest and best-supported path"

    if primary.vendor == "amd":
        gfx = set(primary.extra.get("gfx", []))
        if gfx & ROCM_UNSUPPORTED:
            return "vulkan", (f"AMD {'/'.join(sorted(gfx))} — RDNA2 or older, which AMD's HIP "
                              "SDK does not support on any current OS. Vulkan needs only the "
                              "stock driver and benchmarks level with ROCm on this class.")
        if os_name == "Windows":
            return "vulkan", ("AMD on Windows — ROCm's Windows support is narrow and version-"
                              "fragile; Vulkan is the durable choice")
        if primary.extra.get("rocm_visible"):
            return "hip", ("AMD with ROCm visible on Linux. Benchmark against Vulkan before "
                           "committing: on RDNA4, Vulkan now measures 9-20% FASTER than ROCm. "
                           "Do not assume ROCm wins just because it is AMD's own stack.")
        return "vulkan", "AMD without a working ROCm install — Vulkan"

    if primary.vendor == "intel":
        return "sycl", ("Intel GPU — SYCL, or the new OpenVINO backend if this machine has an "
                        "NPU (Core Ultra). Vulkan is the safe fallback if either misbehaves.")

    return ("vulkan", "unrecognised GPU but Vulkan is present") if _vulkan_ok() else \
           ("cpu", "unrecognised GPU and no Vulkan runtime")


# --------------------------------------------------------------------------- #
#  Feature flags — these are the landmines
# --------------------------------------------------------------------------- #
def features_for(backend: str) -> tuple[dict[str, bool], dict[str, str]]:
    on: dict[str, bool] = {}
    why: dict[str, str] = {}

    # Speculative decoding: catastrophic on Vulkan, fine elsewhere.
    on["speculative_decoding"] = backend in ("cuda", "metal", "hip")
    why["speculative_decoding"] = (
        "llama.cpp #23126 — Vulkan serialises draft and target models on a single "
        "queue: 33 tok/s collapses to 0.014 tok/s. Open, closed as not-planned."
        if backend == "vulkan" else "supported on this backend")

    # Vision projector: a correctness bug, not a performance one.
    on["mmproj_on_gpu"] = backend in ("cuda", "metal")
    why["mmproj_on_gpu"] = (
        "llama.cpp #20081 — Vulkan mmproj offload produces GARBLED output on AMD "
        "(a drone photo described as Vietnamese text) where CUDA and CPU are correct. "
        "Run the projector on CPU and keep the text model on GPU."
        if backend in ("vulkan", "hip") else "verified correct on this backend")

    on["flash_attention"] = True
    why["flash_attention"] = ("supported, though the benefit on RDNA2 Vulkan measured ~1-2%; "
                              "keep it on for the KV-cache saving"
                              if backend == "vulkan" else "real speedup on this backend")

    on["kv_cache_quant"] = backend != "metal"
    why["kv_cache_quant"] = (
        "llama.cpp #21450 — Metal has failed on mixed-quantised KV in 2026 builds. "
        "Canary-test before enabling." if backend == "metal"
        else "use symmetric q8_0 for both K and V; asymmetric quant silently drops "
             "off the fused flash-attention path")

    on["cpu_moe_offload"] = backend != "cpu"
    why["cpu_moe_offload"] = ("buffer-placement override, believed backend-agnostic but not "
                              "documented as guaranteed — verify by measurement per GPU family")

    # STT: faster-whisper's GPU path is CUDA-only and falls back silently.
    on["faster_whisper_gpu"] = backend == "cuda"
    why["faster_whisper_gpu"] = (
        "faster-whisper (CTranslate2) has no ROCm/Vulkan/Metal GPU path — it will "
        "silently run on CPU. Use whisper.cpp, which has real backends everywhere."
        if backend != "cuda" else "CUDA GPU path available")

    on["multi_gpu"] = backend in ("cuda", "hip", "vulkan")
    why["multi_gpu"] = ("--split-mode layer is the safe default; 'row' is deprecated with poor "
                        "performance; 'tensor' needs fast interconnect. No cross-vendor split "
                        "is supported — pin to one GPU when vendors differ.")
    return on, why


# --------------------------------------------------------------------------- #
#  Sizing
# --------------------------------------------------------------------------- #
def budget_and_tier(gpus: list[GPU], ram_gb: float) -> tuple[float, str]:
    """
    VRAM and RAM are NOT fungible, and treating them as one number is how you end
    up recommending a 32 GB model to a 12 GB card.

    VRAM is the hard constraint: attention, the KV cache and the router must be
    resident. System RAM only buys you MoE *expert* offload, which works because
    only ~3B of a 35B model activates per token. So the tier is chosen by VRAM,
    and RAM decides whether MoE offload is available at all.

    Apple is the exception: unified memory has no PCIe hop, so the pool really is
    one budget, and offload is structurally cheaper there than on a discrete GPU.
    """
    if gpus and gpus[0].vendor == "apple":
        pool = gpus[0].vram_gb
        for limit, tier in ((8, "tiny"), (20, "small"), (40, "mid"), (80, "large")):
            if pool < limit:
                return round(pool, 1), tier
        return round(pool, 1), "xlarge"

    # Discrete: pick the largest NON-integrated GPU. An iGPU sharing system RAM
    # must never be mistaken for the real card.
    discrete = [g for g in gpus if not g.extra.get("integrated")]
    vram = max((g.vram_gb for g in discrete), default=0.0)

    # RAM available to hold offloaded experts, after the OS and the rest of the stack.
    spare_ram = max(0.0, ram_gb - 10.0)
    budget = vram + (spare_ram if vram >= 6 else 0.0)

    for limit, tier in ((1, "cpu"), (8, "tiny"), (14, "small"),
                        (26, "mid"), (50, "large")):
        if vram < limit:
            return round(budget, 1), tier
    return round(budget, 1), "xlarge"


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #
def detect(write: bool = True) -> Profile:
    os_name = platform.system()
    gpus = _nvidia() + _amd() + _apple()
    if not gpus and os_name == "Windows":
        gpus = _windows_gpus()

    ram = _ram_gb()
    backend, reason = choose_backend(gpus, os_name)
    feats, notes = features_for(backend)
    budget, tier = budget_and_tier(gpus, ram)

    p = Profile(
        os=f"{os_name} {platform.release()}",
        arch=platform.machine(),
        cpu=platform.processor() or "unknown",
        cores=os.cpu_count() or 0,
        ram_gb=ram,
        gpus=gpus,
        backend=backend,
        backend_reason=reason,
        budget_gb=budget,
        tier=tier,
        features=feats,
        feature_notes=notes,
    )
    if write:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(p.to_json(), encoding="utf-8")
    return p


def load() -> Profile | None:
    if not CACHE.exists():
        return None
    d = json.loads(CACHE.read_text(encoding="utf-8"))
    d["gpus"] = [GPU(**g) for g in d["gpus"]]
    return Profile(**d)


def _ascii(s: str) -> str:
    """Windows consoles are still cp1252 by default; keep the summary readable."""
    return (s.replace("—", "-").replace("–", "-")
             .replace("’", "'").replace("‘", "'"))


if __name__ == "__main__":
    p = detect()
    print(f"  os      : {p.os} ({p.arch})")
    print(f"  cpu     : {p.cpu}  x{p.cores}")
    print(f"  ram     : {p.ram_gb} GB")
    for g in p.gpus:
        tag = " [integrated]" if g.extra.get("integrated") else ""
        print(f"  gpu     : {g.name}  {g.vram_gb} GB{tag}")
    print(f"\n  backend : {p.backend}")
    print(f"  because : {_ascii(p.backend_reason)}")
    print(f"  budget  : {p.budget_gb} GB  ->  tier '{p.tier}'")

    off = [k for k, v in p.features.items() if not v]
    if off:
        print("\n  DISABLED on this machine:")
        for k in off:
            print(f"    - {k}\n        {_ascii(p.feature_notes[k])}")
    print(f"\n  written to {CACHE}")
