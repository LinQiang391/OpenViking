#!/usr/bin/env python
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Play recorder CLI tool.

Replay recorded IO operations and compare performance across different backends.

Usage:
    uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf
    uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --stats-only
    uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --fs
    uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --vikingdb
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from openviking.storage.recorder.playback import (
    IOPlayback,
    PlaybackStats,
    get_record_stats,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def print_stats(stats: dict, title: str = "Record Statistics") -> None:
    """Print statistics in a formatted way."""
    print(f"\n{'=' * 60}")
    print(f"{title}")
    print(f"{'=' * 60}")

    print(f"\nFile: {stats.get('file', 'N/A')}")
    print(f"Total Records: {stats.get('total_records', 0)}")
    print(f"FS Operations: {stats.get('fs_count', 0)}")
    print(f"VikingDB Operations: {stats.get('vikingdb_count', 0)}")
    print(f"Total Latency: {stats.get('total_latency_ms', 0):.2f} ms")

    if stats.get("time_range"):
        time_range = stats["time_range"]
        print(f"\nTime Range:")
        print(f"  Start: {time_range.get('start', 'N/A')}")
        print(f"  End: {time_range.get('end', 'N/A')}")

    if stats.get("operations"):
        print(f"\nOperations Breakdown:")
        print(f"{'Operation':<30} {'Count':>10} {'Avg Latency (ms)':>18}")
        print(f"{'-' * 60}")
        for op, data in sorted(stats["operations"].items()):
            count = data["count"]
            avg_latency = data["total_latency_ms"] / count if count > 0 else 0
            print(f"{op:<30} {count:>10} {avg_latency:>18.2f}")


def print_playback_stats(stats: PlaybackStats) -> None:
    """Print playback statistics."""
    print(f"\n{'=' * 60}")
    print("Playback Results")
    print(f"{'=' * 60}")

    print(f"\nTotal Records: {stats.total_records}")
    print(f"Successful: {stats.success_count}")
    print(f"Failed: {stats.error_count}")
    print(f"Success Rate: {stats.success_count / stats.total_records * 100:.1f}%" if stats.total_records > 0 else "N/A")

    print(f"\nPerformance:")
    print(f"  Original Total Latency: {stats.total_original_latency_ms:.2f} ms")
    print(f"  Playback Total Latency: {stats.total_playback_latency_ms:.2f} ms")

    speedup = stats.to_dict().get("speedup_ratio", 0)
    if speedup > 0:
        if speedup > 1:
            print(f"  Speedup: {speedup:.2f}x (playback is faster)")
        else:
            print(f"  Slowdown: {1/speedup:.2f}x (playback is slower)")

    if stats.fs_stats:
        print(f"\nFS Operations:")
        print(f"{'Operation':<30} {'Count':>10} {'Orig Avg (ms)':>15} {'Play Avg (ms)':>15}")
        print(f"{'-' * 72}")
        for op, data in sorted(stats.fs_stats.items()):
            count = data["count"]
            orig_avg = data["total_original_latency_ms"] / count if count > 0 else 0
            play_avg = data["total_playback_latency_ms"] / count if count > 0 else 0
            print(f"{op:<30} {count:>10} {orig_avg:>15.2f} {play_avg:>15.2f}")

    if stats.vikingdb_stats:
        print(f"\nVikingDB Operations:")
        print(f"{'Operation':<30} {'Count':>10} {'Orig Avg (ms)':>15} {'Play Avg (ms)':>15}")
        print(f"{'-' * 72}")
        for op, data in sorted(stats.vikingdb_stats.items()):
            count = data["count"]
            orig_avg = data["total_original_latency_ms"] / count if count > 0 else 0
            play_avg = data["total_playback_latency_ms"] / count if count > 0 else 0
            print(f"{op:<30} {count:>10} {orig_avg:>15.2f} {play_avg:>15.2f}")


async def main_async(args: argparse.Namespace) -> int:
    """Main async function."""
    record_file = Path(args.record_file)
    if not record_file.exists():
        logger.error(f"Record file not found: {record_file}")
        return 1

    if args.stats_only:
        stats = get_record_stats(str(record_file))
        print_stats(stats)
        return 0

    enable_fs = args.fs
    enable_vikingdb = args.vikingdb

    if not enable_fs and not enable_vikingdb:
        enable_fs = True
        enable_vikingdb = True

    playback = IOPlayback(
        config_file=args.config_file,
        compare_response=args.compare_response,
        fail_fast=args.fail_fast,
        enable_fs=enable_fs,
        enable_vikingdb=enable_vikingdb,
    )

    stats = await playback.play(
        record_file=str(record_file),
        limit=args.limit,
        offset=args.offset,
        io_type=args.io_type,
        operation=args.operation,
    )

    print_playback_stats(stats)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(stats.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"Results saved to: {args.output}")

    return 0 if stats.error_count == 0 else 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Play recorded IO operations and compare performance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show statistics only
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --stats-only

  # Playback with remote config
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov-remote.conf

  # Only test FS operations
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --fs

  # Only test VikingDB operations
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --vikingdb

  # Filter by operation type
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --io-type fs --operation read

  # Save results to file
  uv run play_recorder.py --record_file ./records/io_recorder_20260214.jsonl --config_file ./ov.conf --output results.json
        """,
    )

    parser.add_argument(
        "--record_file",
        type=str,
        required=True,
        help="Path to the record JSONL file",
    )
    parser.add_argument(
        "--config_file",
        type=str,
        default=None,
        help="Path to OpenViking config file (ov.conf)",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Only show statistics without playback",
    )
    parser.add_argument(
        "--fs",
        action="store_true",
        help="Only play FS operations (default: both FS and VikingDB)",
    )
    parser.add_argument(
        "--vikingdb",
        action="store_true",
        help="Only play VikingDB operations (default: both FS and VikingDB)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of records to play",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of records to skip",
    )
    parser.add_argument(
        "--io-type",
        type=str,
        choices=["fs", "vikingdb"],
        default=None,
        help="Filter by IO type",
    )
    parser.add_argument(
        "--operation",
        type=str,
        default=None,
        help="Filter by operation name (e.g., read, search)",
    )
    parser.add_argument(
        "--compare-response",
        action="store_true",
        help="Compare playback response with original",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first error",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file for results (JSON)",
    )

    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
