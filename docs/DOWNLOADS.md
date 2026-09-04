# Model downloads

**Save every file into `C:\vajren\models\` — flat, no subfolders. Keep the exact
filename.** The launcher matches on those names.

Verified against the HuggingFace API on 2026-09-04, so none of these 404.

## Why you might want to do this by hand

Measured throughput from this machine to the HF CDN is **0.35–0.64 MB/s**, and the
connection drops every few minutes. At that rate the full set is roughly a day.
A download manager (IDM, JDownloader, aria2, even Chrome) handles resume and
retry better than anything in this repo, and can queue them overnight.

If you'd rather it ran itself: `.\scripts\resume-download.ps1 -All` now retries
through dropped connections on its own. Watch it with `.\scripts\progress.ps1 -Watch`.

---

## Start here — this one unlocks testing

| | |
|---|---|
| **Lane** | reflex — the pinned classifier |
| **Size** | 2.3 GB |
| **File** | `Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf` |

https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf?download=true

This alone proves the whole stack: llama.cpp runs on the 6750 XT, the server
starts, LiteLLM routes, the graph plans, the gate fires, a tool executes. Too
small to plan well, perfect for proving the plumbing.

---

## The main brain — get this second

| | |
|---|---|
| **Lane** | workhorse — coding and planning |
| **Size** | ~22 GB |
| **File** | `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` |

https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf?download=true

⚠ Note the `UD-` in the filename. An earlier version of `scripts/fetch.py` had it
without, which would have 404'd. Fixed, but if you type the URL by hand, keep it.

---

## The specialists — any time

| Lane | File | Size |
|---|---|---|
| tools | `GLM-4.7-Flash-UD-Q4_K_XL.gguf` | ~17.5 GB |
| writer | `google_gemma-4-31B-it-Q4_K_M.gguf` | ~18.5 GB |
| vision | `Qwen3VL-8B-Instruct-Q4_K_M.gguf` | ~6 GB |

- https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF/resolve/main/GLM-4.7-Flash-UD-Q4_K_XL.gguf?download=true
- https://huggingface.co/bartowski/google_gemma-4-31B-it-GGUF/resolve/main/google_gemma-4-31B-it-Q4_K_M.gguf?download=true
- https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf?download=true

⚠ The vision lane also needs its **mmproj** projector file from the same repo —
the text weights alone will not see images. Grab it when you set up vision;
it is small. And on this AMD/Vulkan machine the projector must run on CPU
(llama.cpp #20081 garbles vision output when it is offloaded to the GPU).

---

## When they land

```powershell
cd C:\vajren
.\scripts\progress.ps1        # confirms which files it can see
.\scripts\04-start-stack.ps1  # starts a server per model present, then LiteLLM
```

`04-start-stack.ps1` skips any model that isn't there yet, so it works with one
file or all five. Nothing needs downloading in order.

## Disk

C: has ~420 GB free. The full set is ~66 GB. No problem.
