#!/usr/bin/env python3
"""
check_flac_converted.py – Verify all .flac files have a converted counterpart.

Finds every .flac file in the source directory (spotiFLAC) and checks if a
corresponding .opus file exists in the output directory (spotiFLAC_opus).
Reports unconverted FLACs and optionally deletes the ones that have been converted.

Usage:
python3 check_flac_converted.py
python3 check_flac_converted.py /path/to/flac /path/to/opus --delete
"""

import argparse
import os
import sys
from pathlib import Path


def format_size(size_bytes):
	for unit in ("B", "KB", "MB", "GB"):
		if abs(size_bytes) < 1024:
			return f"{size_bytes:.1f} {unit}"
		size_bytes /= 1024
	return f"{size_bytes:.1f} TB"


def main():
	parser = argparse.ArgumentParser(
		description="Check that every .flac in spotiFLAC has a .opus counterpart in spotiFLAC_opus."
	)
	parser.add_argument(
		"flac_dir",
		nargs="?",
		default="/goinfre/zelbassa/spotiFLAC",
		help="Directory containing .flac files (default: /goinfre/zelbassa/spotiFLAC)",
	)
	parser.add_argument(
		"opus_dir",
		nargs="?",
		default="/goinfre/zelbassa/spotiFLAC_opus",
		help="Directory containing .opus files (default: /goinfre/zelbassa/spotiFLAC_opus)",
	)
	parser.add_argument(
		"--delete",
		action="store_true",
		help="Delete .flac files that have a converted .opus counterpart",
	)
	args = parser.parse_args()

	flac_root = Path(args.flac_dir).resolve()
	opus_root = Path(args.opus_dir).resolve()

	if not flac_root.is_dir():
		print(f"Error: FLAC directory not found: {flac_root}")
		sys.exit(1)
	if not opus_root.is_dir():
		print(f"Error: Opus directory not found: {opus_root}")
		sys.exit(1)

	flac_files = sorted(flac_root.rglob("*.flac"))
	if not flac_files:
		print(f"No .flac files found in {flac_root}")
		sys.exit(0)

	converted = []    # (flac_path, opus_path)
	unconverted = []  # flac_path with no .opus counterpart

	for flac in flac_files:
		# Mirror the relative path from flac_root into opus_root, swapping extension
		rel = flac.relative_to(flac_root)
		opus_path = opus_root / rel.with_suffix(".opus")

		if opus_path.is_file():
			converted.append((flac, opus_path))
		else:
			unconverted.append(flac)

	# Report
	print(f"FLAC dir: {flac_root}")
	print(f"Opus dir: {opus_root}")
	print(f"Scanned {len(flac_files)} .flac file(s)\n")

	if converted:
		print(f"CONVERTED ({len(converted)}) - .opus exists:")
		total_flac_size = 0
		for flac, opus in converted:
			rel = flac.relative_to(flac_root)
			fsize = flac.stat().st_size
			total_flac_size += fsize
			print(f"  {rel}  ({format_size(fsize)})")
		print(f"  Total FLAC size: {format_size(total_flac_size)}\n")

	if unconverted:
		print(f"NOT CONVERTED ({len(unconverted)}) - no .opus found:")
		for flac in unconverted:
			rel = flac.relative_to(flac_root)
			print(f"  {rel}  ({format_size(flac.stat().st_size)})")
		print()

	# Delete if requested
	if args.delete:
		if unconverted:
			print(f"WARNING: {len(unconverted)} FLAC file(s) have NO counterpart and will NOT be deleted.")

		if not converted:
			print("Nothing to delete.")
			sys.exit(0)

		freed = 0
		deleted = 0
		for flac, _ in converted:
			try:
				size = flac.stat().st_size
				flac.unlink()
				freed += size
				deleted += 1
			except OSError as e:
				print(f"  Error deleting {flac}: {e}")

		print(f"Deleted {deleted} .flac file(s), freed {format_size(freed)}")
	else:
		if unconverted:
			print(f"Run the converter first for the {len(unconverted)} missing file(s).")
		if converted:
			print(f"All {len(converted)} converted .flac file(s) can be safely deleted.")
			print(f"Re-run with --delete to remove them.")


if __name__ == "__main__":
	main()
