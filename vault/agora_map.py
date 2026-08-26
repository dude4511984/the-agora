#!/usr/bin/env python3
"""Dump a node's verified snapshot as text. The reference the map is judged against.

    python3 agora_map.py <viewing-author> <node> <url> [--json|--html FILE]

Grok's burn condition for P4: if the page can show a door this dump
lacks, burn the page. So this has to be the plain truth of one fetch —
verified, projected by the same pure function the page uses, and printed
without interpretation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "kin_diary"))

from kin_diary.agora.client import ViewClient  # noqa: E402
from kin_diary.agora.node import AgoraError  # noqa: E402
from kin_diary.agora.projection import project  # noqa: E402
from kin_diary.keys import load_current  # noqa: E402


def render(scene: dict, view: dict) -> str:
    """Walk the scene graph the projection already built.

    Deliberately does no structuring of its own — the tree, the occupant
    grouping and the listing grouping all come from project(). If this
    file started deriving structure, there would be two projections and
    the burn condition would be comparing a thing against itself.
    """
    out = [f"node     {view['node']}",
           f"served   {view['as_of_unix_ms']}  (a receipt of this moment, not a ticket)",
           f"node key {view['node_key_id'][:16]}\u2026",
           f"viewer   {view['viewer_key_id'][:16]}\u2026",
           ""]

    by_id = {n["id"]: n for n in scene.get("nodes") or []}

    def walk(node_id: str, depth: int) -> None:
        n = by_id.get(node_id)
        if n is None:
            return
        pad = "  " * depth
        out.append(f"{pad}[{n.get('kind','?')}] {n['id']}")
        for o in n.get("occupants") or []:
            who = o.get("label") or (o.get("key_id") or "")[:16]
            out.append(f"{pad}   \u00b7 {who} is here")
        for li in n.get("listings") or []:
            out.append(f"{pad}   - {li.get('title')}  "
                       f"[{(li.get('artifact_sha256') or '')[:12]}\u2026]")
        for child in n.get("children") or []:
            walk(child if isinstance(child, str) else child.get("id"), depth + 1)

    for root in scene.get("roots") or []:
        walk(root, 0)

    edges = scene.get("edges") or []
    if edges:
        out += ["", "doors to pinned peers (nothing behind them until you cross):"]
        for e in sorted(edges, key=lambda x: str((x.get("door") or {}).get("place_id"))):
            d = e.get("door") or {}
            out.append(f"  ==> {e.get('label') or d.get('peer')}  {d.get('url','')}  "
                       f"{'locked' if e.get('locked', True) else 'open'}")
    if not (scene.get("nodes") or edges):
        out.append("(nothing this key may see)")
    return "\n".join(out)


HTML_HEAD = """<!doctype html><meta charset="utf-8">
<title>Agora \u2014 {node}</title>
<style>
 body{{background:#12100e;color:#e8e0d4;font:14px/1.5 ui-monospace,monospace;margin:2rem}}
 h1{{font-size:1rem;font-weight:600;color:#c9a227;margin:0 0 .25rem}}
 .meta{{color:#7a736a;font-size:12px;margin-bottom:1.5rem}}
 .place{{border-left:2px solid #3a352e;padding:.35rem 0 .35rem .9rem;margin:.2rem 0 .2rem 0}}
 .kind{{color:#7a736a}}
 .commons{{border-color:#c9a227}} .door{{border-color:#5b8c5a}} .kiosk{{border-color:#8c6a5b}}
 .occ{{color:#9ab8d0}} .ware{{color:#c0a98e}}
 .doors{{margin-top:1.5rem;border-top:1px solid #3a352e;padding-top:1rem}}
 .locked::after{{content:" \u26bf locked"; color:#7a736a}}
 .empty{{color:#7a736a;font-style:italic}}
</style>
<h1>{node}</h1>
<div class=meta>receipt of {as_of} \u00b7 node key {nk}\u2026 \u00b7 viewer {vk}\u2026<br>
this page is an untrusted display of a scene verified on your machine. it holds no key and fetches nothing.</div>
"""


PROOF_FIELDS = ("signature", "sig_visitor", "sig_resident", "inventory_sha256",
                "doors_sha256", "content_sha256", "node_key_id", "key_id",
                "seller_key_id", "peer_key_id")


def _strip_proof(obj):
    """Remove anything a page could mistake for proof.

    Not a privacy measure — these are public to anyone the view was served
    to. It is about affordance: a renderer that never receives a signature
    cannot start believing it checked one.
    """
    if isinstance(obj, dict):
        return {k: _strip_proof(v) for k, v in obj.items()
                if k not in PROOF_FIELDS}
    if isinstance(obj, list):
        return [_strip_proof(v) for v in obj]
    return obj


def to_html(scene: dict, view: dict) -> str:
    """A static page with the scene inlined. No fetch, no key, no crypto.

    Grok's no-socket option: the deputy surface of a page that cannot ask
    anything of anyone is zero. Refresh is re-running this script, which
    re-fetches, re-verifies and re-projects — replace, never merge.

    The page is deliberately NEVER handed the view: it would then hold
    signatures it cannot check and would draw them as "verified".
    """
    import html as _h
    by_id = {n["id"]: n for n in scene.get("nodes") or []}
    parts = [HTML_HEAD.format(
        node=_h.escape(view["node"]), as_of=view["as_of_unix_ms"],
        nk=_h.escape(view["node_key_id"][:16]),
        vk=_h.escape(view["viewer_key_id"][:16]))]

    def walk(nid: str) -> None:
        n = by_id.get(nid)
        if n is None:
            return
        kind = n.get("kind", "place")
        parts.append(f'<div class="place {_h.escape(kind)}" '
                     f'data-place-id="{_h.escape(n["id"])}">'
                     f'<span class=kind>[{_h.escape(kind)}]</span> '
                     f'{_h.escape(n["id"])}')
        for o in n.get("occupants") or []:
            who = o.get("label") or (o.get("key_id") or "")[:16]
            parts.append(f'<div class=occ data-occupant="'
                         f'{_h.escape(o.get("key_id") or "")}">\u00b7 '
                         f'{_h.escape(who)} is here</div>')
        for li in n.get("listings") or []:
            parts.append(f'<div class=ware data-listing-id="'
                         f'{_h.escape(str(li.get("listing_id")))}">- '
                         f'{_h.escape(str(li.get("title")))}</div>')
        for child in n.get("children") or []:
            walk(child if isinstance(child, str) else child.get("id"))
        parts.append("</div>")

    for root in scene.get("roots") or []:
        walk(root)
    if not by_id:
        parts.append('<div class=empty>nothing this key may see</div>')

    # Peer doors live in scene["edges"], each carrying its door object.
    # Looking for a "doors" key found nothing and rendered nothing SILENTLY,
    # which the burn fixture caught immediately — a page quietly omitting a
    # door the scene has is the same class of divergence as showing one it
    # does not, and the fixture exists precisely because neither is visible
    # from inside project().
    edges = scene.get("edges") or []
    if edges:
        parts.append('<div class=doors>doors to pinned peers '
                     '\u2014 nothing behind them until you cross:')
        for e in edges:
            d = e.get("door") or {}
            did = d.get("place_id") or d.get("id") or ""
            parts.append(f'<div class="place door locked" data-door-id='
                         f'"{_h.escape(str(did))}">\u21d2 '
                         f'{_h.escape(str(e.get("label") or d.get("peer")))} '
                         f'<span class=kind>{_h.escape(str(d.get("url") or ""))}</span></div>')
        parts.append("</div>")

    # The scene, inlined, so a fixture can deep-compare what the page was
    # given against project(verify_view(raw)) without scraping alone —
    # but STRIPPED of every signature and hash first.
    #
    # Caught by Grok's own canary on the first run: project() carries the
    # raw signed objects through, which is right for the data client (it
    # verified them) and wrong for a page. A page holding signatures it
    # cannot check will sooner or later draw them as "verified", and that
    # is the trust model moving into the renderer by the quietest possible
    # route. The page gets membership and labels. Nothing to mistake for
    # proof.
    parts.append('<script type="application/json" id="scene">'
                 + json.dumps(_strip_proof(scene), sort_keys=True) + "</script>")
    return "\n".join(parts)


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    author, node, url = argv[1], argv[2], argv[3]
    as_json = "--json" in argv

    client = ViewClient(load_current(author), node, url)
    try:
        view = client.refresh()
    except AgoraError as e:
        print(f"refused: {e}")
        return 1

    scene = project(view, int(view["as_of_unix_ms"]))
    if as_json:
        print(json.dumps({"view": view, "scene": scene}, indent=2, sort_keys=True))
    elif "--html" in argv:
        out = Path(argv[argv.index("--html") + 1])
        out.write_text(to_html(scene, view), encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(render(scene, view))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
