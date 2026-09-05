"""16c - Why won't whisper load? Full traceback, no swallowing."""
import sys, traceback
from pathlib import Path
d = Path(r"C:\vajren\models\voice\whisper\small.en")
for p in sorted(d.iterdir()):
    print(f"  {p.name:<24} {p.stat().st_size:>12,} bytes")
print("\n  model.bin first 32 bytes:", (d / "model.bin").read_bytes()[:32])
try:
    from faster_whisper import WhisperModel
    m = WhisperModel(str(d), device="cpu", compute_type="int8")
    print("\n  LOADED OK")
except Exception:
    print()
    traceback.print_exc()
    sys.exit(1)
