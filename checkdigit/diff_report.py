"""
diff_report.py
==============
Visualization layer for a CorrectionReport. Renders a human-readable before/after
diff for the CLI / tests. Kept separate from correction logic: the web frontend
(later pass) renders the SAME change record with a JS diff viewer; this is the
text-mode equivalent. Format-agnostic -- works for EDIFACT (1 occurrence per
change) and SNX (N synced occurrences) alike.
"""
from __future__ import annotations

import difflib
from typing import List, Optional

from correction_report import CorrectionReport


def render(report: CorrectionReport, *, max_items: Optional[int] = None,
           show_segments: bool = False) -> str:
    s = report.summary()
    lines: List[str] = [
        f"owner_policy={report.owner_policy}  containers={s['containers']}  "
        f"corrected={s['corrected']}  flagged={s['flagged']}  valid={s['valid']}  "
        f"empty_id={s['empty_id']}  invalid={s['invalid']}"
    ]

    shown = report.corrected if max_items is None else report.corrected[:max_items]
    for c in shown:
        locs = ", ".join(o.label for o in c.occurrences)
        lines.append(f"  {c.old}  ->  {c.new}    (check {c.printed_check}->{c.computed_check})"
                     f"   [{len(c.occurrences)}x: {locs}]")
        if show_segments:
            for o in c.occurrences:
                lines.append(f"      @{o.offset:<6} - {o.before}")
                lines.append(f"      @{o.offset:<6} + {o.after}")
    if max_items is not None and len(report.corrected) > max_items:
        lines.append(f"  ... ({len(report.corrected) - max_items} more corrections)")

    if report.flagged:
        lines.append(f"  flagged (left unchanged): {len(report.flagged)}")
        fshown = report.flagged if max_items is None else report.flagged[:max_items]
        for f in fshown:
            mult = f" [{f.occurrences}x]" if f.occurrences > 1 else ""
            lines.append(f"    @{f.offset:<6} {f.eqid}{mult}   (suggested check {f.suggested_check})")
        if max_items is not None and len(report.flagged) > max_items:
            lines.append(f"    ... ({len(report.flagged) - max_items} more flagged)")

    return "\n".join(lines)


def unified_diff(original: str, corrected: str, *, filename: str = "message",
                 context: int = 0) -> str:
    """Standard unified line diff. Meaningful for one-segment/element-per-line files."""
    a, b = original.splitlines(), corrected.splitlines()
    return "\n".join(difflib.unified_diff(
        a, b, fromfile=f"{filename} (original)", tofile=f"{filename} (corrected)",
        n=context, lineterm=""))
