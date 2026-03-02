#!/usr/bin/env python3
"""
flac_to_opus.py – Convert .flac files to .opus (128k) preserving all metadata.

Converts all FLAC files in a directory to Opus format at a specified bitrate,
while keeping tags, lyrics, and cover art fully intact.

Requirements:
- ffmpeg (system binary)
- mutagen (pip install mutagen) – for cover art transfer

Usage:
python3 flac_to_opus.py /path/to/flac/dir
python3 flac_to_opus.py /path/to/flac/dir -o /path/to/output -b 128k
"""

import argparse
import base64
import multiprocessing
import os
import subprocess
import sys
from functools import partial
from pathlib import Path

try:
	from mutagen.flac import FLAC
	from mutagen.oggopus import OggOpus
	HAS_MUTAGEN = True
except ImportError:
	HAS_MUTAGEN = False


def ensure_dir(path):
	"""Create directory (and parents) if it doesn't exist."""
	Path(path).mkdir(parents=True, exist_ok=True)


def find_ffmpeg():
	"""Locate a usable ffmpeg binary."""
	# 1. Try imageio-ffmpeg
	try:
		import imageio_ffmpeg
		ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
		if ffmpeg_bin and os.path.isfile(ffmpeg_bin) and os.access(ffmpeg_bin, os.X_OK):
			return ffmpeg_bin
	except Exception:
		pass

	# 2. Environment variable
	env_ffmpeg = os.environ.get("FFMPEG")
	if env_ffmpeg and os.path.isfile(env_ffmpeg) and os.access(env_ffmpeg, os.X_OK):
		return env_ffmpeg

	# 3. ~/bin/ffmpeg
	home_ffmpeg = os.path.expanduser("~/bin/ffmpeg")
	if os.path.isfile(home_ffmpeg) and os.access(home_ffmpeg, os.X_OK):
		return home_ffmpeg

	# 4. System PATH
	try:
		subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
		return "ffmpeg"
	except (FileNotFoundError, subprocess.CalledProcessError):
		pass

	return None


def transfer_metadata(flac_path, opus_path):
	"""Copy cover art and lyrics from FLAC to Opus in a single pass."""
	if not HAS_MUTAGEN:
		return False

	try:
		flac = FLAC(str(flac_path))
		opus = OggOpus(str(opus_path))
		changed = False

		# --- Cover art ---
		pictures = flac.pictures
		if pictures:
			encoded_pics = []
			for pic in pictures:
				encoded_pics.append(base64.b64encode(pic.write()).decode("ascii"))
			opus["metadata_block_picture"] = encoded_pics
			changed = True

		# --- Lyrics ---
		lyric_keys = {"lyrics", "unsyncedlyrics", "syncedlyrics"}
		if flac.tags:
			for tag_key, tag_val in flac.tags:
				if tag_key.lower() in lyric_keys:
					existing = opus.get(tag_key)
					if not existing:
						opus[tag_key] = [tag_val] if isinstance(tag_val, str) else tag_val
						changed = True

		if changed:
			opus.save()
		return True
	except Exception as e:
		print(f"  ⚠ Metadata transfer failed: {e}")
		return False


def convert_file(ffmpeg_bin, flac_path, opus_path, bitrate):
	"""Convert a single FLAC file to Opus, preserving metadata."""
	ensure_dir(opus_path.parent)

	# ffmpeg: convert audio to opus, copy all metadata tags
	cmd = [
		ffmpeg_bin,
		"-i", str(flac_path),
		"-map_metadata", "0",     # copy all metadata tags
		"-c:a", "libopus",        # encode audio as Opus
		"-b:a", bitrate,          # target bitrate
		"-vn",                    # skip picture streams (handled by mutagen)
		"-y",                     # overwrite output
		str(opus_path),
	]

	result = subprocess.run(cmd, capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-500:]}")

	# Transfer cover art + lyrics in one pass (ffmpeg can't embed pictures in opus)
	transfer_metadata(flac_path, opus_path)


def format_size(size_bytes):
	"""Format bytes as human-readable string."""
	for unit in ("B", "KB", "MB", "GB"):
		if abs(size_bytes) < 1024:
			return f"{size_bytes:.1f} {unit}"
		size_bytes /= 1024
	return f"{size_bytes:.1f} TB"


def _worker(args_tuple):
	"""Worker function for multiprocessing pool."""
	ffmpeg_bin, flac_path, opus_path, bitrate = args_tuple
	try:
		convert_file(ffmpeg_bin, flac_path, opus_path, bitrate)
		src_size = flac_path.stat().st_size
		dst_size = opus_path.stat().st_size
		return (True, flac_path.name, src_size, dst_size, None)
	except Exception as e:
		return (False, flac_path.name, 0, 0, str(e))


def main():
	parser = argparse.ArgumentParser(
		description="Convert FLAC files to Opus preserving all metadata, lyrics, and cover art."
	)
	parser.add_argument(
		"input_dir",
		nargs="?",
		default="/goinfre/zelbassa/spotiFLAC",
		help="Directory containing .flac files (default: /goinfre/zelbassa/spotiFLAC)",
	)
	parser.add_argument(
		"-o", "--output",
		default=None,
		help="Output directory (default: <input_dir>_opus)",
	)
	parser.add_argument(
		"-b", "--bitrate",
		default="128k",
		help="Audio bitrate (default: 128k)",
	)
	parser.add_argument(
		"-f", "--force",
		action="store_true",
		help="Re-convert even if output file already exists",
	)
	parser.add_argument(
		"-j", "--jobs",
		type=int,
		default=multiprocessing.cpu_count(),
		help="Parallel conversions (default: number of CPU cores)",
	)
	args = parser.parse_args()

	input_dir = Path(args.input_dir).resolve()
	output_dir = Path(args.output).resolve() if args.output else input_dir.parent / (input_dir.name + "_opus")

	if not input_dir.is_dir():
		print(f"Error: input directory not found: {input_dir}")
		sys.exit(1)

	# Locate ffmpeg
	ffmpeg_bin = find_ffmpeg()
	if not ffmpeg_bin:
		print("Error: ffmpeg not found. Install it or set the FFMPEG env variable.")
		sys.exit(1)
	print(f"Using ffmpeg: {ffmpeg_bin}")

	# Check mutagen
	if not HAS_MUTAGEN:
		print("Warning: mutagen not installed – cover art will NOT be transferred.")
		print("  Install it with: pip install mutagen")

	# Collect FLAC files
	flac_files = sorted(input_dir.rglob("*.flac"))
	if not flac_files:
		print(f"No .flac files found in {input_dir}")
		sys.exit(0)

	print(f"Found {len(flac_files)} FLAC file(s) in {input_dir}")
	print(f"Output directory: {output_dir}")
	print(f"Bitrate: {args.bitrate}")
	print(f"Parallel jobs: {args.jobs}")
	print()

	ensure_dir(output_dir)

	# Build work list, skipping existing files
	work = []
	skipped = 0
	for flac_path in flac_files:
		rel = flac_path.relative_to(input_dir)
		opus_path = output_dir / rel.with_suffix(".opus")
		ensure_dir(opus_path.parent)

		if opus_path.exists() and not args.force:
			print(f"SKIP (exists): {rel}")
			skipped += 1
			continue
		work.append((ffmpeg_bin, flac_path, opus_path, args.bitrate))

	if not work:
		print("Nothing to convert (all files already exist).")
		return

	print(f"Converting {len(work)} file(s) ...")

	success = 0
	failed = 0
	total_src_size = 0
	total_dst_size = 0

	with multiprocessing.Pool(processes=args.jobs) as pool:
		for idx, result in enumerate(pool.imap_unordered(_worker, work), 1):
			ok, name, src_size, dst_size, err = result
			if ok:
				total_src_size += src_size
				total_dst_size += dst_size
				ratio = (1 - dst_size / src_size) * 100 if src_size else 0
				print(f"[{idx}/{len(work)}] ✓ {name}  {format_size(src_size)} → {format_size(dst_size)} ({ratio:.0f}% smaller)")
				success += 1
			else:
				print(f"[{idx}/{len(work)}] ✗ {name}  FAILED: {err}")
				failed += 1

	# Summary
	print()
	print("=" * 50)
	print(f"Done! {success} converted, {skipped} skipped, {failed} failed")
	if total_src_size:
		saved = total_src_size - total_dst_size
		print(f"Total: {format_size(total_src_size)} → {format_size(total_dst_size)} (saved {format_size(saved)})")
	print("=" * 50)


if __name__ == "__main__":
	main()
