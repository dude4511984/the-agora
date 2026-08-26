"""A viewing client: fetch a node's snapshot, verify it, hold exactly one.

This is the piece that holds a private key and talks to a socket, which is
the shape of every authentication bug in this repository's history. So the
rules it enforces on itself are deliberately narrow:

* It holds **one current view per node**. Not a cache, not a history — a
  single slot that is replaced wholesale. Currency is the last fetch
  (agora.md, "View freshness"), and a client that keeps a pile of
  snapshots it liked is the Marvin bug waiting for an algorithm.
* It **verifies before storing**, never after. An unverified view never
  reaches the slot, so nothing downstream has to remember to check.
* It **pins on first sight** and refuses a node key that changed. Trust on
  first use is the honest bootstrap; what pinning buys is that a swap is
  loud instead of silent.
* It hands out the view and nothing else. It has no method that takes a
  view and performs an action, because the moment one exists the snapshot
  has become a second permission system.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..keys import KeyRecord
from .node import AgoraError
from .places import verify_view
from .wire import sign_request


class ViewClient:
    def __init__(self, key: KeyRecord, node: str, url: str, *, timeout: float = 10):
        self.key = key
        self.node = node
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._pinned_node_key: str | None = None
        self._current: dict | None = None

    # ── fetch ──────────────────────────────────────────────────────────────

    def refresh(self) -> dict:
        """Fetch, verify, replace. The only way the slot ever changes."""
        path = "/view"
        req = urllib.request.Request(
            self.url + path,
            headers=sign_request(self.key, self.node, path),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                view = json.load(r)
        except urllib.error.HTTPError as e:
            raise AgoraError(f"node refused the view request: {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AgoraError(f"node unreachable at {self.url}: {e}") from e
        except ValueError as e:
            raise AgoraError(f"node returned malformed JSON: {e}") from e

        if view.get("node") != self.node:
            raise AgoraError(
                f"asked {self.node} for a view and got one claiming to be "
                f"{view.get('node')!r}"
            )

        # Verify BEFORE the slot changes. A failed refresh must leave the
        # previous view untouched rather than half-replacing it — a client
        # left holding nothing renders an empty world, which is its own lie.
        verify_view(
            view,
            expected_node_key_id=self._pinned_node_key,
            expected_viewer_key_id=self.key.key_id,
        )

        if self._pinned_node_key is None:
            self._pinned_node_key = view["node_key_id"].lower()

        self._current = view      # replace, never merge
        return view

    # ── read ───────────────────────────────────────────────────────────────

    @property
    def current(self) -> dict | None:
        """The one view being projected, or None before the first refresh.

        Returning None rather than a stale one is deliberate: there is no
        such thing as "the last good view" in this design. There is the
        current fetch, or there is nothing to draw.
        """
        return self._current

    @property
    def pinned_node_key(self) -> str | None:
        return self._pinned_node_key

    def unpin(self) -> None:
        """Explicitly forget a node key. Loud on purpose — this is the only
        way past a pin conflict, so re-trusting a changed node is always a
        deliberate human act, never a silent recovery.
        """
        self._pinned_node_key = None
        self._current = None
