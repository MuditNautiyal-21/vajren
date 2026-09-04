# Design, without a GPU that can do design

Most "design" work is layout, not pixels — and layout is code. That matters here
because image generation on an RDNA2 card under Windows 10 runs through ZLUDA or
DirectML, both of which are workaround stacks that break on driver updates.

**So: code first, generative second, cloud third.**

---

## The main move: HTML template → Playwright screenshot

This is the sleeper answer for social graphics, quote cards, OG images, report
covers and slide art. Build a parameterised HTML/CSS template, fill it with data,
render it headless at an exact pixel size, screenshot it.

Why it beats a diffusion model for this job:

- Runs on the CPU. Zero GPU dependency, so the whole AMD problem disappears.
- Sub-second per image, versus 20–45 seconds for local SDXL.
- Pixel-perfect and **reproducible** — same template plus same data equals the
  same image, every time. A diffusion model cannot promise that.
- Full CSS: grid, flexbox, gradients, blend modes, real fonts, inline SVG.
- Brand-consistent by construction, because every template reads the same tokens.

```python
# core/tools/design.py — sketch
from playwright.sync_api import sync_playwright
from jinja2 import Template

def render_card(template_path: str, data: dict, out: str, w=1080, h=1080):
    html = Template(open(template_path, encoding="utf-8").read()).render(**data)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
        pg.set_content(html, wait_until="networkidle")
        pg.screenshot(path=out)
        b.close()
    return out
```

**Satori** (Vercel, MIT) is the lighter sibling — JSX/HTML+CSS straight to SVG with
no browser at all. Faster, but only a flexbox subset of CSS. Use Playwright for
grid-based layouts, Satori for simple cards where speed matters.

### Design tokens
One `config/brand.css` with custom properties — colours, font stack, spacing scale,
logo SVG — referenced by every template, the Typst theme and the python-pptx theme.
That is how output stays on-brand with no model in the loop.

---

## Everything else, by job

| Job | Tool | Licence |
|---|---|---|
| Slide decks | `python-pptx`, Marp, Slidev, Quarto | MIT / free |
| PDF + documents | **Typst** (modern LaTeX alternative), WeasyPrint (HTML→PDF), ReportLab | free / BSD |
| Word docs | `python-docx` | MIT |
| Diagrams | **Mermaid**, Graphviz, **D2** (nicest defaults), PlantUML | free / Apache |
| Simple photo edit | Pillow + `rembg` (background removal) | free |
| Charts | matplotlib / plotly → PNG, or a Playwright-rendered HTML chart | free |
| Stock photos | **Pexels API** (no attribution required, commercial OK), Openverse | free |
| Icons / fonts | Lucide (MIT), Google Fonts | free |

---

## When you genuinely need a generated image

### Locally — ZLUDA + ComfyUI

`patientx/ComfyUI-Zluda` explicitly supports this card's category (Vega → 6700 on
HIP SDK 6.2.4). DirectML via SD.Next is the stability fallback when ZLUDA breaks.

⚠ **ZLUDA lost its funding and is back to hobby status.** Treat it as fragile
infrastructure that needs re-validating after every driver update. Keep DirectML
installed as the fallback.

⚠ Known gfx1031 issue: VAE-decode `RuntimeError`, fixed by the CFZ CUDNN Toggle
node in the ComfyUI-Zluda repo. Install path must have no spaces.

**Model to use: Z-Image Turbo** (Alibaba, 6B, **Apache 2.0**). It is the best
combination available for this card: fully commercial licence, fits 12 GB with
room to spare even at bf16, and its 8-step turbo design makes it fast — roughly
15–35 s per image here.

| Model | Licence | Commercial? | Fits 12 GB | Speed here |
|---|---|---|---|---|
| **Z-Image Turbo** 6B | Apache 2.0 | **Yes** | Easily | ~15–35 s |
| SDXL / SDXL-Turbo | OpenRAIL++-M | Yes | Comfortably | ~20–45 s / ~5–10 s |
| SD 3.5 Medium | Stability Community | Yes under $1M rev | Yes | ~SDXL |
| FLUX.1-schnell 12B | **Apache 2.0** | Yes | Needs GGUF Q4/Q5 | 40–90 s |
| FLUX.1-dev / Kontext / FLUX.2-klein | **Non-commercial** | **No** | Needs GGUF | slow |
| Qwen-Image 20B | Apache 2.0 | Yes | Barely, Q4 + offload | minutes |
| Qwen-Image-Edit | Apache 2.0 | Yes | Q4 + CPU offload | minutes per edit |

### Free cloud image APIs

| Provider | Free reality | API? |
|---|---|---|
| **Cloudflare Workers AI** | 10,000 neurons/day; FLUX-schnell ≈ 4.8 neurons per 512² tile → dozens of images/day | **Yes, best pick** |
| **Pollinations.ai** | Freemium, MIT project, commercial use allowed | Yes |
| Hugging Face Inference | $0.10/month credit — effectively 1–2 images | Yes, but trivial |
| Together AI FLUX-schnell | Promo free endpoint — re-verify before relying on it | Yes |
| **Google Gemini image** | **No free tier via API.** $0.034–$0.24/image. Free "Nano Banana" exists only in the consumer app, not the developer API | Paid only |
| Bing / Designer, Firefly, Ideogram | UI credits only | No API |

---

## Verdict per task

| Task | Do it |
|---|---|
| Social graphic | **CODE** (HTML template), generative only for novel imagery inside it |
| Slide deck | **CODE** (python-pptx / Marp) |
| Diagram | **CODE** (Mermaid / D2) |
| Document, PDF, report | **CODE** (Typst / WeasyPrint) |
| Crop, resize, remove background | **CODE** (Pillow + rembg) |
| Understand a screenshot or chart | **LOCAL** (Qwen3-VL-8B) |
| Extract text from a scan | **LOCAL CPU** (RapidOCR — no GPU at all) |
| Novel photographic image | **LOCAL** (Z-Image Turbo) or **FREE CLOUD** (Cloudflare) |
| Instruct-edit a photo | **LOCAL**, slowly (Qwen-Image-Edit Q4) — batch it, don't iterate |
| Logo | **NOT AUTOMATED.** Needs taste and iteration. Use a cloud UI and your own judgement |
| Generative video | **NOT ACHIEVABLE** on this card. Assemble rendered frames with ffmpeg instead |
