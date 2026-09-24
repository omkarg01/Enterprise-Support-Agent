"""
Shared layout helpers for the per-component LLD pages in architecture.tldr.

PageBuilder keeps a registry of box bounds so arrows can anchor to box edges,
lays out equal-height rows with text-based height estimates, and lets frames be
added after their contents while still drawing underneath them.
"""

import json
import math
import os

from generate_lld_page import _Idx, arrow, box, frame


def est_h(text, w, line_h=25, pad=26):
    """Estimate box height for size 's' sans text so labels don't overflow."""
    usable = max(w - 30, 50)
    lines = sum(max(1, math.ceil(len(line) * 8.8 / usable)) for line in text.split("\n"))
    return pad + lines * line_h


class PageBuilder:
    def __init__(self, pid, prefix):
        self.pid = pid
        self.prefix = prefix
        self.idx = _Idx(500)
        self.shapes = []
        self._bounds = {}

    def add(self, nid, text, x, y, w, h, color, fill="semi", ext=False, **kw):
        """Add a box. ext=True draws a grey dashed 'not owned' box instead."""
        self._bounds[nid] = (x, y, w, h)
        sid = f"shape:{self.prefix}_{nid}"
        if ext:
            self.shapes.append(frame(sid, text, x, y, w, h, "grey", self.idx, page_id=self.pid))
        else:
            self.shapes.append(box(sid, text, x, y, w, h, color, fill, self.idx, page_id=self.pid, **kw))

    def frame_behind(self, nid, label, x, y, w, h, color):
        """Add a section frame that renders beneath shapes added before it."""
        self.shapes.insert(0, frame(f"shape:{self.prefix}_f_{nid}", label, x, y, w, h, color, self.idx, page_id=self.pid))

    def _anchor(self, nid, side):
        x, y, w, h = self._bounds[nid]
        return {"R": (x + w, y + h / 2), "L": (x, y + h / 2),
                "T": (x + w / 2, y), "B": (x + w / 2, y + h)}[side]

    def connect(self, aid, a, z, fa="R", ta="L", label="", color="black", bend=0, size="s"):
        x1, y1 = self._anchor(a, fa)
        x2, y2 = self._anchor(z, ta)
        self.shapes.append(arrow(f"shape:{self.prefix}_a_{aid}", x1, y1, x2, y2, label, self.idx,
                                 bend=bend, color=color, size=size, page_id=self.pid))

    def row(self, y, items, color_default, x0, width, gap):
        """Lay out (nid, text, color|None, ext) boxes in one row with a shared height; returns it."""
        n = len(items)
        w = (width - (n - 1) * gap) / n
        h = max(est_h(t, w) for _, t, _, _ in items)
        for i, (nid, text, color, ext) in enumerate(items):
            self.add(nid, text, x0 + i * (w + gap), y, w, h, color or color_default, ext=ext)
        return h

    def records(self, name, page_index, zoom=0.4):
        # Frames were inserted at the front after being built; re-index so z-order follows list order.
        order = _Idx(500)
        for s in self.shapes:
            s["index"] = order()
        page = {"typeName": "page", "id": self.pid, "name": name, "index": page_index, "meta": {}}
        camera = {"typeName": "camera", "id": f"camera:{self.pid}", "x": 0, "y": 0, "z": zoom, "meta": {}}
        return page, camera, self.shapes


def write_page(page, camera, shapes, target="architecture.tldr"):
    """Replace one page (and its shapes/camera) in the .tldr file, leaving every other page untouched."""
    if not os.path.exists(target):
        print(f"Error: {target} not found!")
        return
    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    pid = page["id"]
    kept = [r for r in data.get("records", [])
            if r.get("id") not in (pid, f"camera:{pid}") and r.get("parentId") != pid]
    data["records"] = kept + [page, camera] + shapes
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Generated page '{page['name']}' with {len(shapes)} shapes in {target}.")
