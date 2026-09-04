"""
Task -> lane routing.

Deliberately NOT a learned router. RouterBench graded 405K outcomes across 8
domains and found that on most general tasks a learned router does no better than
always picking one fixed model. Routing pays off when the task classes are clean
and separable — which they are here, because the workflow names them.

So: cheap rules first (free, sub-millisecond), the always-resident 4B model as the
fallback classifier (~200-500ms), and never a black-box quality prediction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "config" / "router.yaml"

_cfg = yaml.safe_load(ROUTER_PATH.read_text(encoding="utf-8"))
LANES: dict = _cfg["lanes"]
RULES: list = _cfg["rules"]
FALLBACK_LANE: str = _cfg["fallback"]["default"]

_compiled = [
    (r["lane"], re.compile("|".join(re.escape(k) for k in r["any_of"]), re.I))
    for r in RULES
]


class LaneChoice(BaseModel):
    lane: str = Field(description="one of: " + ", ".join(LANES))
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class Route:
    lane: str
    model: str
    how: str          # "rule" | "classifier" | "default" | "forced"
    sampling: dict


def _sampling_for(lane: str) -> dict:
    spec = LANES.get(lane, {})
    s: dict = {}
    if spec.get("reasoning_effort"):
        s["reasoning_effort"] = spec["reasoning_effort"]
    if lane == "writer":
        s.update(temperature=0.8, top_p=0.95)
    if lane in ("tools", "reflex"):
        s.update(temperature=0.2)
    return s


def route(request: str, *, force_lane: str | None = None, has_image: bool = False) -> Route:
    if force_lane:
        lane, how = force_lane, "forced"
    elif has_image:
        lane, how = "vision", "rule"
    else:
        lane, how = _match_rules(request)

    spec = LANES.get(lane) or LANES[FALLBACK_LANE]
    return Route(lane=lane, model=spec.get("model", "vajren-workhorse"),
                 how=how, sampling=_sampling_for(lane))


def _match_rules(request: str) -> tuple[str, str]:
    for lane, pattern in _compiled:
        if pattern.search(request):
            return lane, "rule"
    return _classify(request)


def _classify(request: str) -> tuple[str, str]:
    """Ask the pinned 4B model. It is always resident, so this never pays a swap cost."""
    from core.llm import structured  # imported late to avoid a cycle

    options = ", ".join(k for k in LANES if k not in ("escalate", "ocr"))
    try:
        choice = structured(
            [
                {"role": "system", "content":
                 f"Classify the user's request into exactly one lane: {options}. "
                 "writer = email/message/post/rewrite. coder = code, repos, shell. "
                 "planner = strategy, decomposition, tradeoffs. tools = calling APIs "
                 "or extracting structured data. vision = an image is involved. "
                 "reflex = trivial lookup or one-line answer. Output the lane only."},
                {"role": "user", "content": request[:1200]},
            ],
            LaneChoice,
            lane="reflex",
        )
        if choice.lane in LANES and choice.confidence >= 0.5:
            return choice.lane, "classifier"
    except Exception:
        pass
    return FALLBACK_LANE, "default"


def prewarm(lane: str) -> None:
    """
    Fire a 1-token request at the target lane the moment routing decides, so
    llama-swap starts loading (~26s off NVMe, ~58s off SATA) while the graph is
    still doing its own bookkeeping. Never block on this.
    """
    import threading

    from core.llm import chat

    def _go() -> None:
        try:
            chat([{"role": "user", "content": "."}], lane=lane, max_tokens=1)
        except Exception:
            pass

    threading.Thread(target=_go, daemon=True).start()
