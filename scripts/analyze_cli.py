#!/usr/bin/env python3
"""Analyse a VOD from the terminal (no dashboard).

    .venv/bin/python scripts/analyze_cli.py https://www.twitch.tv/videos/1234567890
    .venv/bin/python scripts/analyze_cli.py <link> --export 3 --vertical
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from highlight_finder import clipper, pipeline  # noqa: E402
from highlight_finder.config import AnalysisSettings  # noqa: E402
from highlight_finder.errors import HighlightFinderError  # noqa: E402
from highlight_finder.logging_setup import setup_logging  # noqa: E402
from highlight_finder.timeutil import format_time  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Find highlights in a Twitch VOD from chat activity.")
    parser.add_argument("link", help="Twitch VOD link or id ('demo' for synthetic data)")
    parser.add_argument("--sensitivity", type=int, default=5, help="1-10 (default 5)")
    parser.add_argument("--pre-roll", type=int, default=20, help="seconds before the chat reaction (default 20)")
    parser.add_argument("--max", type=int, default=20, help="max number of highlights")
    parser.add_argument("--export", type=int, default=0, metavar="N", help="export the N best highlights as MP4")
    parser.add_argument("--vertical", action="store_true", help="also export a 9:16 version")
    args = parser.parse_args()

    setup_logging()
    settings = AnalysisSettings(sensitivity=args.sensitivity, pre_roll=args.pre_roll, max_highlights=args.max)
    try:
        if args.link == "demo":
            run = pipeline.load_demo(settings)
        else:
            run = pipeline.analyze_vod(args.link, settings, progress=lambda stage, f, msg: print(f"  [{stage}] {msg}", end="\r"))
        print(f"\n{run.info.label} – {len(run.result.highlights)} highlights\n")
        print(f"{'#':>2}  {'tid':>8}  {'score':>5}  {'type':<8} {'beskeder':>8}  {'stigning':>8}  klip")
        for h in run.result.highlights:
            reactions = " ".join(f"{t}x{n}" for t, n in h.top_reactions[:3])
            print(f"{h.rank:>2}  {format_time(h.peak_time):>8}  {h.score:>5}  {h.kind:<8} {h.messages:>8}  "
                  f"{'+%d%%' % h.increase_pct:>8}  {format_time(h.clip_start)}-{format_time(h.clip_end)}  {reactions}")
        formats = ("16:9", "9:16") if args.vertical else ("16:9",)
        for h in run.result.highlights[: args.export]:
            for clip in clipper.export_clip(run.info, h.clip_start, h.clip_end, title=h.titles[0], formats=formats, score=h.score):
                print(f"gemt: {clip.path}")
    except HighlightFinderError as exc:
        print(f"\nFEJL: {exc}\n{exc.hint}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
