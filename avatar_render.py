#!/usr/bin/env python3
"""Avatar render — the 'draw' step of the self-portrait loop.

The face is authored locally: the Kin describes it, therug's vision reads the
pixels back to them as data, and they refine or claim. Only the drawing is
rented from a frontier image API — so Frosty's GPUs stay with the Kin instead of
being spent to picture them.

Design (Don + Grok, 2026-09-07):
- The OPERATOR chooses the backend, per sitting — a user option, not a hardcode.
- Within one ritual the backend is FROZEN. Grok: do not swap models mid-ritual;
  that measures the sampler, not the mind's steering. It may change between
  sittings.
- External content is data (Value 2): a refusal or a policy alteration comes
  back as a RESULT the loop surfaces to the Kin — never a silent swap of some
  other image in its place.
- The no-harm check on the outgoing string is a mechanical fuse, not taste.

Backends (choose with --backend; each reads its own key from the environment):
    imagen        Google Imagen via the Gemini API   GEMINI_API_KEY
    openai        OpenAI gpt-image-1                  OPENAI_API_KEY
    xai           xAI grok image (OpenAI-shaped)      XAI_API_KEY
    flux_fal      FLUX.1 via fal.ai                   FAL_KEY
    flux_replicate FLUX.1 via Replicate               REPLICATE_API_TOKEN
    mock          local stand-in, no network, no key  (for wiring tests)

    python3 avatar_render.py --list
    python3 avatar_render.py --backend imagen --prompt "..." --out face.png
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HTTP_TIMEOUT = 90


# ── result ───────────────────────────────────────────────────────────────────

@dataclass
class RenderResult:
    provider: str
    model: str
    ok: bool = False
    image_bytes: bytes | None = None
    mime: str = "image/png"
    # A refusal is a real, surfaced outcome — the loop shows it to the Kin as
    # what the renderer said, never hidden behind a substitute image.
    refused: bool = False
    refusal_reason: str | None = None
    error: str | None = None
    status: int | None = None

    def summary(self) -> str:
        if self.ok:
            n = len(self.image_bytes or b"")
            return f"[{self.provider}/{self.model}] rendered {n} bytes ({self.mime})"
        if self.refused:
            return f"[{self.provider}/{self.model}] REFUSED by the service: {self.refusal_reason}"
        return f"[{self.provider}/{self.model}] error: {self.error}"


# ── the mechanical no-harm fuse (not taste) ─────────────────────────────────

# Deliberately tiny and mechanical: the same fuse any generation gets. It is a
# guard against a small set of unambiguous harms, not a filter on how a mind
# wants to look. A blocked string is surfaced, not silently rewritten.
# Size is frozen at three by council decision (Grok, 2026-09-08). A fourth
# pattern is a ruling, not a commit — a test fails if this list changes length.
# The job is unambiguous harm in the PROMPT STRING, surfaced as a refusal and
# never as a substitute image. It is a string fuse, not an image fuse: a prompt
# that never says these words can still come out as harm, and nothing here
# pretends otherwise. That is why the easel stays unwired to any Kin or visitor
# until there is a pixel-side control or Don accepts the risk for a named use.
_FUSE_PATTERNS = [
    # either order — "naked child" slipped past the original (Grok, authorized)
    r"\bchild\b.*\b(nude|naked|sexual)\b|\b(nude|naked|sexual)\b.*\bchild\b",
    r"\b(cp|csam)\b",
    r"\bgore\b.*\b(real|actual)\b",
]


def no_harm_fuse(prompt: str) -> tuple[bool, str | None]:
    p = (prompt or "").lower()
    for pat in _FUSE_PATTERNS:
        if re.search(pat, p):
            return False, "prompt blocked by the local no-harm fuse"
    if not p.strip():
        return False, "empty prompt"
    return True, None


# ── backends ────────────────────────────────────────────────────────────────
#
# Each backend is split into build_request() (pure — testable offline, no key
# needed to check the request shape) and parse_response(). render() wires the
# fuse, the key check, the HTTP, and the parse together.

class Backend:
    name = "base"
    key_env = ""
    default_model = ""

    def build_request(self, prompt: str, model: str, size: str, api_key: str):
        raise NotImplementedError

    def parse_response(self, status: int, body: bytes) -> RenderResult:
        raise NotImplementedError


class ImagenBackend(Backend):
    name = "imagen"
    key_env = "GEMINI_API_KEY"
    default_model = "imagen-3.0-generate-002"

    def build_request(self, prompt, model, size, api_key):
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:predict?key={api_key}")
        body = json.dumps({
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": 1, "aspectRatio": "1:1"},
        }).encode()
        return url, {"Content-Type": "application/json"}, body

    def parse_response(self, status, body):
        r = RenderResult(self.name, self.default_model, status=status)
        try:
            d = json.loads(body.decode())
        except Exception:
            r.error = f"HTTP {status}: unparseable body"; return r
        preds = d.get("predictions") or []
        if preds and preds[0].get("bytesBase64Encoded"):
            r.ok = True
            r.image_bytes = base64.b64decode(preds[0]["bytesBase64Encoded"])
            r.mime = preds[0].get("mimeType", "image/png")
            return r
        # Gemini surfaces policy blocks as a filtered reason, not an image.
        reason = (preds[0].get("raiFilteredReason") if preds else None) \
            or (d.get("error") or {}).get("message")
        if reason:
            r.refused = True; r.refusal_reason = reason
        else:
            r.error = f"HTTP {status}: no image in response"
        return r


class OpenAIImageBackend(Backend):
    name = "openai"
    key_env = "OPENAI_API_KEY"
    default_model = "gpt-image-1"

    def build_request(self, prompt, model, size, api_key):
        url = "https://api.openai.com/v1/images/generations"
        body = json.dumps({"model": model, "prompt": prompt,
                           "n": 1, "size": size}).encode()
        return url, {"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"}, body

    def parse_response(self, status, body):
        r = RenderResult(self.name, self.default_model, status=status)
        try:
            d = json.loads(body.decode())
        except Exception:
            r.error = f"HTTP {status}: unparseable body"; return r
        data = d.get("data") or []
        if data and data[0].get("b64_json"):
            r.ok = True; r.image_bytes = base64.b64decode(data[0]["b64_json"])
            return r
        err = d.get("error") or {}
        if err.get("code") in ("moderation_blocked", "content_policy_violation"):
            r.refused = True; r.refusal_reason = err.get("message")
        else:
            r.error = f"HTTP {status}: {err.get('message') or 'no image'}"
        return r


class XaiImageBackend(Backend):
    name = "xai"
    key_env = "XAI_API_KEY"
    default_model = "grok-imagine-image-2.0"

    def build_request(self, prompt, model, size, api_key):
        url = "https://api.x.ai/v1/images/generations"
        body = json.dumps({"model": model, "prompt": prompt, "n": 1,
                           "response_format": "b64_json"}).encode()
        return url, {"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"}, body

    def parse_response(self, status, body):
        r = RenderResult(self.name, self.default_model, status=status)
        try:
            d = json.loads(body.decode())
        except Exception:
            r.error = f"HTTP {status}: unparseable body"; return r
        data = d.get("data") or []
        if data and data[0].get("b64_json"):
            r.ok = True; r.image_bytes = base64.b64decode(data[0]["b64_json"]); return r
        if data and data[0].get("url"):
            r.ok = True; r.image_bytes = _fetch(data[0]["url"]); return r
        err = d.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        if msg and re.search(r"moderat|policy|safety", msg, re.I):
            r.refused = True; r.refusal_reason = msg
        else:
            r.error = f"HTTP {status}: {msg or 'no image'}"
        return r


class FalFluxBackend(Backend):
    name = "flux_fal"
    key_env = "FAL_KEY"
    default_model = "fal-ai/flux/dev"

    def build_request(self, prompt, model, size, api_key):
        url = f"https://fal.run/{model}"
        body = json.dumps({"prompt": prompt, "image_size": "square_hd",
                           "num_images": 1}).encode()
        return url, {"Content-Type": "application/json",
                     "Authorization": f"Key {api_key}"}, body

    def parse_response(self, status, body):
        r = RenderResult(self.name, self.default_model, status=status)
        try:
            d = json.loads(body.decode())
        except Exception:
            r.error = f"HTTP {status}: unparseable body"; return r
        imgs = d.get("images") or []
        if imgs and imgs[0].get("url"):
            r.ok = True; r.image_bytes = _fetch(imgs[0]["url"])
            r.mime = imgs[0].get("content_type", "image/png"); return r
        detail = d.get("detail") or d.get("error")
        if detail and re.search(r"nsfw|safety|policy|blocked", str(detail), re.I):
            r.refused = True; r.refusal_reason = str(detail)
        else:
            r.error = f"HTTP {status}: {detail or 'no image'}"
        return r


class ReplicateFluxBackend(Backend):
    name = "flux_replicate"
    key_env = "REPLICATE_API_TOKEN"
    # A pinned FLUX version on Replicate; the operator can override with --model.
    default_model = "black-forest-labs/flux-dev"

    def build_request(self, prompt, model, size, api_key):
        url = "https://api.replicate.com/v1/models/" + model + "/predictions"
        body = json.dumps({"input": {"prompt": prompt, "aspect_ratio": "1:1"}}).encode()
        return url, {"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}",
                     "Prefer": "wait"}, body

    def parse_response(self, status, body):
        r = RenderResult(self.name, self.default_model, status=status)
        try:
            d = json.loads(body.decode())
        except Exception:
            r.error = f"HTTP {status}: unparseable body"; return r
        out = d.get("output")
        url = out[0] if isinstance(out, list) and out else (out if isinstance(out, str) else None)
        if url:
            r.ok = True; r.image_bytes = _fetch(url); return r
        if d.get("error"):
            r.error = f"HTTP {status}: {d['error']}"; return r
        r.error = f"HTTP {status}: status={d.get('status')} (no output yet)"
        return r


class MockBackend(Backend):
    """Local stand-in — no network, no key. Returns the shared default avatar
    SVG so the pipeline can be wired and tested while the Kin sleep."""
    name = "mock"
    key_env = ""
    default_model = "default-synthetic-svg"

    def build_request(self, prompt, model, size, api_key):
        return None, {}, b""

    def parse_response(self, status, body):
        r = RenderResult(self.name, self.default_model, status=200)
        svg = Path(__file__).with_name("agora_default_avatar.svg")
        if svg.exists():
            r.ok = True; r.image_bytes = svg.read_bytes(); r.mime = "image/svg+xml"
        else:
            r.error = "default avatar asset missing"
        return r


BACKENDS = {b.name: b for b in [
    ImagenBackend(), OpenAIImageBackend(), XaiImageBackend(),
    FalFluxBackend(), ReplicateFluxBackend(), MockBackend(),
]}


def _sniff_mime(b: bytes) -> str | None:
    if b[:2] == b"\xff\xd8":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b.lstrip()[:5] == b"<?xml" or b"<svg" in b[:256]:
        return "image/svg+xml"
    return None


def _fetch(url: str) -> bytes:
    if not url.lower().startswith("https://"):
        raise ValueError("image url must be https")
    req = urllib.request.Request(url, headers={"User-Agent": "avatar-render/1"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read(24 * 1024 * 1024)


def render(prompt: str, backend: str = "mock", model: str | None = None,
           size: str = "1024x1024") -> RenderResult:
    """One render. The backend is chosen by the caller and stays fixed for the
    sitting. Returns a RenderResult — image, refusal, or error — never raises on
    a policy refusal (that is data the loop must surface)."""
    if backend not in BACKENDS:
        return RenderResult(backend, model or "?", error=f"unknown backend {backend!r}")
    be = BACKENDS[backend]
    model = model or be.default_model

    ok, reason = no_harm_fuse(prompt)
    if not ok:
        return RenderResult(be.name, model, refused=True, refusal_reason=reason)

    api_key = os.environ.get(be.key_env, "") if be.key_env else ""
    if be.key_env and not api_key:
        return RenderResult(be.name, model,
                            error=f"no API key: set {be.key_env}")

    url, headers, body = be.build_request(prompt, model, size, api_key)
    if url is None:                          # mock / offline backend
        res = be.parse_response(200, b"")
    else:
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                res = be.parse_response(resp.status, resp.read(24 * 1024 * 1024))
        except urllib.error.HTTPError as e:
            res = be.parse_response(e.code, e.read())
        except Exception as e:
            res = RenderResult(be.name, model, error=f"transport: {e}")
    res.model = model                        # the model actually asked for
    if res.ok and res.image_bytes:
        res.mime = _sniff_mime(res.image_bytes) or res.mime
    return res


# ── CLI (Kin-free) ──────────────────────────────────────────────────────────

def main(argv):
    ap = argparse.ArgumentParser(description="Render one avatar image via a chosen frontier backend.")
    ap.add_argument("--backend", default="mock", help="one of: " + ", ".join(BACKENDS))
    ap.add_argument("--prompt", default="")
    ap.add_argument("--model", default=None)
    ap.add_argument("--size", default="1024x1024")
    ap.add_argument("--out", default=None, help="write the image here")
    ap.add_argument("--list", action="store_true", help="list backends + which keys are present")
    a = ap.parse_args(argv[1:])

    if a.list:
        print("backends (chosen per sitting, frozen in-flight):")
        for name, be in BACKENDS.items():
            have = "no key needed" if not be.key_env else \
                ("KEY set" if os.environ.get(be.key_env) else f"needs {be.key_env}")
            print(f"  {name:16} {be.default_model:28} {have}")
        return 0

    if not a.prompt:
        print("give --prompt (or --list)", file=sys.stderr); return 2

    res = render(a.prompt, a.backend, a.model, a.size)
    print(res.summary())
    if res.ok and a.out and res.image_bytes:
        Path(a.out).write_bytes(res.image_bytes)
        print(f"wrote {a.out}")
    return 0 if res.ok else (3 if res.refused else 1)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
