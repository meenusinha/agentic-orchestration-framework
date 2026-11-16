"""Components of a repository: the parts the GraphRAG can be created in, batch by batch.

A component is a top-level folder. A folder that holds a large share of a big repository
is split into its sub-folders, so a monorepo with one huge `src/` still offers useful
choices. Files that belong to no component (top-level files, and files directly inside a
split folder) are always included: they are few, and they hold the build files that tell
which processes exist.

Components left for a later batch are stored in the repository's `deferred` setting as
folder paths; every builder excludes them the same way (see settings.rag_kwargs).
Standard library only.
"""
from __future__ import annotations

import glob
from dataclasses import dataclass, field
from pathlib import PurePosixPath

ALWAYS = ""                    # the always-included component (top-level and loose files)
SPLIT_SHARE = 0.4              # split a folder holding more than this share of the files …
SPLIT_MIN_FILES = 400          # … in a repository with at least this many files


@dataclass
class Component:
    path: str                                  # folder relative to the repository root; ALWAYS for loose files
    files: list = field(default_factory=list)  # [(rel, size in bytes)]

    @property
    def size(self) -> int:
        return sum(s for _, s in self.files)

    @property
    def label(self) -> str:
        return self.path + "/" if self.path else "Top-level files (always included)"


def group(files: list[tuple[str, int]]) -> list[Component]:
    """[(rel, size)] → components, largest first, the always-included one last."""
    by_top: dict[str, list] = {}
    for rel, size in files:
        parts = PurePosixPath(rel).parts
        by_top.setdefault(parts[0] if len(parts) > 1 else ALWAYS, []).append((rel, size))
    total = len(files)
    out: dict[str, Component] = {}
    for top, items in by_top.items():
        subs: dict[str, list] = {}
        loose = []
        for rel, size in items:
            parts = PurePosixPath(rel).parts
            (subs.setdefault("/".join(parts[:2]), []) if len(parts) > 2 else loose).append((rel, size))
        split = top != ALWAYS and total >= SPLIT_MIN_FILES and len(items) > SPLIT_SHARE * total and len(subs) >= 2
        if split:
            for sub, sub_items in subs.items():
                out[sub] = Component(sub, sub_items)
            out.setdefault(ALWAYS, Component(ALWAYS)).files.extend(loose)
        else:
            out.setdefault(top, Component(top)).files.extend(items)
    always = out.pop(ALWAYS, None)
    ordered = sorted(out.values(), key=lambda c: (-c.size, c.path))
    return ordered + ([always] if always and always.files else [])


def to_setting(path: str) -> str:
    """Component folder → entry of the `deferred` setting (glob characters escaped)."""
    return glob.escape(path)


def from_setting(entry: str) -> str:
    """Entry of the `deferred` setting → component folder."""
    out, i = [], 0
    while i < len(entry):                      # undo glob.escape: [*] [?] [[] → * ? [
        if entry[i] == "[" and i + 2 < len(entry) and entry[i + 2] == "]":
            out.append(entry[i + 1])
            i += 3
        else:
            out.append(entry[i])
            i += 1
    return "".join(out)
