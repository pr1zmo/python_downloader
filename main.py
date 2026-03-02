#!/usr/bin/env python3
"""
main.py – Unified audio pipeline: FLAC→Opus conversion, verification, and compression.

Three-step pipeline:
  1. Convert all .flac files to .opus preserving metadata, lyrics, and cover art
  2. Verify every .flac has a converted .opus counterpart
  3. Compress non-FLAC audio files (mp3, m4a, ogg, etc.) above a size threshold

Output is saved to <input_dir>_Opus.

Requirements:
  - ffmpeg  (system binary)
  - mutagen (pip install mutagen) – for cover art / lyrics transfer

Usage:
  python3 main.py /path/to/music/dir
  python3 main.py /path/to/music/dir -b 128k -j 4
  python3 main.py /path/to/music/dir --skip-compress
"""

import argparse
import base64
import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

try:
	from mutagen.flac import FLAC
	from mutagen.oggopus import OggOpus
	from mutagen.oggvorbis import OggVorbis
	import mutagen
	HAS_MUTAGEN = True
except ImportError:
	HAS_MUTAGEN = False


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

SIZE_MB = 1024 * 1024

# Non-FLAC audio extensions handled by the compress step
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wma", ".wav", ".mp4"}
# Containers that support embedded picture streams (ID3 / MP4)
CONTAINERS_WITH_ART = {".mp3", ".m4a", ".mp4", ".aac", ".wma"}
# Containers that do NOT support picture streams (ogg family)
CONTAINERS_NO_ART = {".opus", ".ogg", ".wav"}


# ──────────────────────────────────────────────────────────────────────────────
# Shared utilities
# ──────────────────────────────────────────────────────────────────────────────

def format_size(size_bytes):
	"""Format bytes as human-readable string."""
	for unit in ("B", "KB", "MB", "GB"):
		if abs(size_bytes) < 1024:
			return f"{size_bytes:.1f} {unit}"
		size_bytes /= 1024
	return f"{size_bytes:.1f} TB"


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


def find_ffprobe(ffmpeg_bin):
	"""Derive ffprobe path from ffmpeg path."""
	if ffmpeg_bin == "ffmpeg":
		return "ffprobe"
	parent = os.path.dirname(ffmpeg_bin)
	probe = os.path.join(parent, "ffprobe")
	if os.path.isfile(probe) and os.access(probe, os.X_OK):
		return probe
	return "ffprobe"


# ──────────────────────────────────────────────────────────────────────────────
# Metadata helpers – FLAC→Opus (cover art + lyrics)
# ──────────────────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────────────────
# Metadata helpers – generic (compress step)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_cover_art(filepath):
	"""Extract cover art bytes from any audio file using mutagen.

	Returns a list of raw picture block bytes (for METADATA_BLOCK_PICTURE),
	or an empty list if no art found.
	"""
	if not HAS_MUTAGEN:
		print("  ⚠ Mutagen not available – cannot extract cover art.")
		print("Run this script in a Python environment with mutagen installed to enable cover art transfer.")
		print("python3 -m venv /goinfre/zelbassa/p_scripts && /goinfre/zelbassa/p_scripts/bin/pip install mutagen")
		print("pip3 install -r requirements.txt")
		return []

	try:
		audio = mutagen.File(str(filepath))
		if audio is None:
			return []

		# FLAC
		if hasattr(audio, "pictures") and audio.pictures:
			return [pic.write() for pic in audio.pictures]

		# OGG/Opus – metadata_block_picture vorbis comment
		if hasattr(audio, "tags") and audio.tags:
			pics = audio.tags.get("metadata_block_picture", [])
			if pics:
				from mutagen.flac import Picture
				raw = []
				for p in pics:
					raw.append(Picture(base64.b64decode(p)).write())
				return raw

		# MP3 – ID3 APIC frames
		if hasattr(audio, "tags") and audio.tags:
			from mutagen.id3 import APIC
			apic_frames = audio.tags.getall("APIC") if hasattr(audio.tags, "getall") else []
			if apic_frames:
				from mutagen.flac import Picture
				raw = []
				for frame in apic_frames:
					pic = Picture()
					pic.type = frame.type
					pic.mime = frame.mime
					pic.desc = frame.desc
					pic.data = frame.data
					raw.append(pic.write())
				return raw

		# MP4/M4A – covr atom
		if hasattr(audio, "tags") and audio.tags:
			covr = audio.tags.get("covr", [])
			if covr:
				from mutagen.flac import Picture
				raw = []
				for img in covr:
					pic = Picture()
					pic.type = 3  # front cover
					pic.mime = "image/jpeg" if bytes(img)[:3] == b"\xff\xd8\xff" else "image/png"
					pic.data = bytes(img)
					raw.append(pic.write())
				return raw

	except Exception:
		pass
	return []


def _embed_cover_art_ogg(output_path, picture_blocks):
	"""Embed cover art into an ogg-family file via METADATA_BLOCK_PICTURE."""
	if not HAS_MUTAGEN or not picture_blocks:
		return

	try:
		ext = Path(output_path).suffix.lower()
		if ext == ".opus":
			audio = OggOpus(str(output_path))
		elif ext == ".ogg":
			audio = OggVorbis(str(output_path))
		else:
			return

		encoded = [base64.b64encode(raw).decode("ascii") for raw in picture_blocks]
		audio["metadata_block_picture"] = encoded
		audio.save()
	except Exception:
		pass


def _embed_cover_art_id3(output_path, picture_blocks):
	"""Embed cover art into MP3 (ID3) or MP4/M4A files."""
	if not HAS_MUTAGEN or not picture_blocks:
		return

	try:
		from mutagen.flac import Picture
		ext = Path(output_path).suffix.lower()

		if ext == ".mp3":
			from mutagen.id3 import ID3, APIC
			try:
				tags = ID3(str(output_path))
			except Exception:
				tags = ID3()
			for raw in picture_blocks:
				pic = Picture(raw)
				tags.add(APIC(
					encoding=3,
					mime=pic.mime,
					type=pic.type,
					desc=pic.desc,
					data=pic.data,
				))
			tags.save(str(output_path))

		elif ext in (".m4a", ".mp4", ".aac"):
			from mutagen.mp4 import MP4, MP4Cover
			audio = MP4(str(output_path))
			covers = []
			for raw in picture_blocks:
				pic = Picture(raw)
				fmt = MP4Cover.FORMAT_JPEG if pic.mime == "image/jpeg" else MP4Cover.FORMAT_PNG
				covers.append(MP4Cover(pic.data, imageformat=fmt))
			audio["covr"] = covers
			audio.save()

	except Exception:
		pass


# ──────────────────────────────────────────────────────────────────────────────
# Compress helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_audio_bitrate(ffprobe_bin, filepath):
	"""Get the current audio bitrate in kbps using ffprobe. Returns None on failure."""
	cmd = [
		ffprobe_bin,
		"-v", "quiet",
		"-select_streams", "a:0",
		"-show_entries", "stream=bit_rate",
		"-of", "json",
		str(filepath),
	]
	try:
		result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
		if result.returncode == 0:
			data = json.loads(result.stdout)
			streams = data.get("streams", [])
			if streams and streams[0].get("bit_rate"):
				return int(streams[0]["bit_rate"]) // 1000  # bps → kbps
	except Exception:
		pass
	return None


def get_codec_for_ext(ext):
	"""Return the appropriate ffmpeg audio codec for a given extension."""
	codecs = {
		".mp3": "libmp3lame",
		".ogg": "libvorbis",
		".opus": "libopus",
		".m4a": "aac",
		".aac": "aac",
		".wma": "wmav2",
		".wav": "libopus",  # wav at 128k makes no sense → re-encode to opus
		".mp4": "aac",
	}
	return codecs.get(ext, "libmp3lame")


def get_output_ext(ext):
	"""Some formats change extension when re-encoded to a sane codec."""
	if ext == ".wav":
		return ".opus"
	return ext


# ──────────────────────────────────────────────────────────────────────────────
# Step 1 – Convert FLAC to Opus
# ──────────────────────────────────────────────────────────────────────────────

def convert_file(ffmpeg_bin, flac_path, opus_path, bitrate):
	"""Convert a single FLAC file to Opus, preserving metadata."""
	ensure_dir(opus_path.parent)

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


def _flac_worker(args_tuple):
	"""Worker function for FLAC→Opus multiprocessing pool."""
	ffmpeg_bin, flac_path, opus_path, bitrate = args_tuple
	try:
		convert_file(ffmpeg_bin, flac_path, opus_path, bitrate)
		src_size = flac_path.stat().st_size
		dst_size = opus_path.stat().st_size
		return (True, flac_path.name, src_size, dst_size, None)
	except Exception as e:
		return (False, flac_path.name, 0, 0, str(e))


def step_convert_flac_to_opus(input_dir, output_dir, bitrate, jobs, force, ffmpeg_bin):
	"""Step 1: Convert all .flac files in input_dir to .opus in output_dir.

	Returns (success_count, skipped_count, failed_count).
	"""
	print("=" * 60)
	print("  STEP 1: Convert FLAC → Opus")
	print("=" * 60)

	flac_files = sorted(input_dir.rglob("*.flac"))
	if not flac_files:
		print(f"  No .flac files found in {input_dir}")
		return (0, 0, 0)

	print(f"  Found {len(flac_files)} FLAC file(s)")
	print(f"  Output: {output_dir}")
	print(f"  Bitrate: {bitrate}")
	print(f"  Jobs: {jobs}")
	print()

	ensure_dir(output_dir)

	# Build work list, skipping existing files
	work = []
	skipped = 0
	for flac_path in flac_files:
		rel = flac_path.relative_to(input_dir)
		opus_path = output_dir / rel.with_suffix(".opus")
		ensure_dir(opus_path.parent)

		if opus_path.exists() and not force:
			print(f"  SKIP (exists): {rel}")
			skipped += 1
			continue
		work.append((ffmpeg_bin, flac_path, opus_path, bitrate))

	if not work:
		print("  Nothing to convert (all files already exist).")
		return (0, skipped, 0)

	print(f"  Converting {len(work)} file(s) ...\n")

	success = 0
	failed = 0
	total_src_size = 0
	total_dst_size = 0

	with multiprocessing.Pool(processes=jobs) as pool:
		for idx, result in enumerate(pool.imap_unordered(_flac_worker, work), 1):
			ok, name, src_size, dst_size, err = result
			if ok:
				total_src_size += src_size
				total_dst_size += dst_size
				ratio = (1 - dst_size / src_size) * 100 if src_size else 0
				print(f"  [{idx}/{len(work)}] ✓ {name}  {format_size(src_size)} → {format_size(dst_size)} ({ratio:.0f}% smaller)")
				success += 1
			else:
				print(f"  [{idx}/{len(work)}] ✗ {name}  FAILED: {err}")
				failed += 1

	print()
	if total_src_size:
		saved = total_src_size - total_dst_size
		print(f"  Total: {format_size(total_src_size)} → {format_size(total_dst_size)} (saved {format_size(saved)})")
	print(f"  Step 1 done: {success} converted, {skipped} skipped, {failed} failed\n")

	return (success, skipped, failed)


# ──────────────────────────────────────────────────────────────────────────────
# Step 2 – Verify conversion
# ──────────────────────────────────────────────────────────────────────────────

def step_verify_conversion(input_dir, output_dir):
	"""Step 2: Verify every .flac in input_dir has a .opus in output_dir.

	Returns (converted_list, unconverted_list).
	"""
	print("=" * 60)
	print("  STEP 2: Verify FLAC → Opus conversion")
	print("=" * 60)

	flac_files = sorted(input_dir.rglob("*.flac"))
	if not flac_files:
		print(f"  No .flac files found in {input_dir}")
		return ([], [])

	converted = []     # (flac_path, opus_path)
	unconverted = []   # flac_path with no .opus counterpart

	for flac in flac_files:
		rel = flac.relative_to(input_dir)
		opus_path = output_dir / rel.with_suffix(".opus")

		if opus_path.is_file():
			converted.append((flac, opus_path))
		else:
			unconverted.append(flac)

	print(f"  Scanned {len(flac_files)} .flac file(s)\n")

	if converted:
		print(f"  CONVERTED ({len(converted)}) - .opus exists:")
		total_flac_size = 0
		for flac, opus in converted:
			rel = flac.relative_to(input_dir)
			fsize = flac.stat().st_size
			total_flac_size += fsize
			print(f"    {rel}  ({format_size(fsize)})")
		print(f"    Total FLAC size: {format_size(total_flac_size)}\n")

	if unconverted:
		print(f"  NOT CONVERTED ({len(unconverted)}) - no .opus found:")
		for flac in unconverted:
			rel = flac.relative_to(input_dir)
			print(f"    {rel}  ({format_size(flac.stat().st_size)})")
		print()
		print(f"  ⚠ WARNING: {len(unconverted)} FLAC file(s) were NOT converted!")
	else:
		print(f"  ✓ All {len(converted)} FLAC file(s) have been successfully converted.")

	print()
	return (converted, unconverted)


# ──────────────────────────────────────────────────────────────────────────────
# Step 3 – Compress non-FLAC audio
# ──────────────────────────────────────────────────────────────────────────────

def compress_file(ffmpeg_bin, filepath, output_path, bitrate):
	"""Re-encode a single audio file to output_path at the target bitrate.

	Returns (ok, src_size, dst_size, error_msg).
	"""
	filepath = Path(filepath)
	output_path = Path(output_path)
	src_size = filepath.stat().st_size
	ext = filepath.suffix.lower()
	out_ext = get_output_ext(ext)
	codec = get_codec_for_ext(ext)

	Path(output_path.parent).mkdir(parents=True, exist_ok=True)

	try:
		# Extract cover art from original BEFORE conversion
		picture_blocks = _extract_cover_art(filepath)

		# Audio-only ffmpeg conversion
		cmd = [
			ffmpeg_bin,
			"-i", str(filepath),
			"-map", "0:a",           # audio stream only
			"-map_metadata", "0",    # copy all metadata tags
			"-vn",                   # explicitly no video/picture
			"-c:a", codec,
			"-b:a", bitrate,
		]
		# Opus requires 48kHz
		if codec == "libopus":
			cmd += ["-ar", "48000"]

		cmd += ["-y", str(output_path)]

		result = subprocess.run(cmd, capture_output=True, text=True)
		if result.returncode != 0:
			if output_path.exists():
				output_path.unlink()
			cmd_str = " ".join(cmd)
			stderr = result.stderr.strip().split("\n")
			err_lines = "\n".join(stderr[-5:])
			return (False, src_size, 0, f"cmd: {cmd_str}\n{err_lines}")

		# Re-embed cover art from original into the compressed file
		if picture_blocks:
			if out_ext in CONTAINERS_NO_ART:
				_embed_cover_art_ogg(output_path, picture_blocks)
			else:
				_embed_cover_art_id3(output_path, picture_blocks)

		dst_size = output_path.stat().st_size

		if dst_size >= src_size:
			return (True, src_size, dst_size, "already optimal")

		return (True, src_size, dst_size, None)

	except Exception as e:
		if output_path.exists():
			output_path.unlink()
		return (False, src_size, 0, str(e))


def _compress_worker(args_tuple):
	"""Worker function for compress multiprocessing pool."""
	ffmpeg_bin, filepath, output_path, bitrate = args_tuple
	ok, src_size, dst_size, msg = compress_file(ffmpeg_bin, filepath, output_path, bitrate)
	return (ok, src_size, dst_size, msg, str(filepath))


def step_compress_non_flac(input_dir, output_dir, bitrate, min_size_mb, jobs, ffmpeg_bin):
	"""Step 3: Compress non-FLAC audio files above min_size_mb to target bitrate.

	Returns (success_count, skipped_count, failed_count).
	"""
	print("=" * 60)
	print("  STEP 3: Compress non-FLAC audio files")
	print("=" * 60)

	ffprobe_bin = find_ffprobe(ffmpeg_bin)
	min_bytes = int(min_size_mb * SIZE_MB)

	# Collect non-FLAC audio files above size threshold
	candidates = []
	for f in sorted(input_dir.rglob("*")):
		if not f.is_file():
			continue
		ext = f.suffix.lower()
		if ext == ".flac" or ext not in AUDIO_EXTENSIONS:
			continue
		size = f.stat().st_size
		if size > min_bytes:
			candidates.append(f)

	if not candidates:
		print(f"  No non-FLAC audio files >{min_size_mb}MB found in {input_dir}")
		return (0, 0, 0)

	print(f"  Found {len(candidates)} non-FLAC file(s) >{min_size_mb}MB")
	print(f"  Output: {output_dir}")
	print(f"  Target bitrate: {bitrate}")
	print(f"  Jobs: {jobs}")
	print()

	# Check current bitrates and decide what to compress
	already_ok = []
	to_compress = []
	target_kbps = int(bitrate.rstrip("kK"))

	for f in candidates:
		current_br = get_audio_bitrate(ffprobe_bin, f)
		size = f.stat().st_size
		rel = f.relative_to(input_dir)
		br_str = f"{current_br}k" if current_br else "?"

		if current_br and current_br <= target_kbps:
			print(f"    SKIP {rel}  ({format_size(size)}, {br_str} <= {bitrate})")
			already_ok.append(f)
		else:
			print(f"    QUEUE {rel}  ({format_size(size)}, {br_str} -> {bitrate})")
			to_compress.append(f)

	if not to_compress:
		print(f"\n  All {len(already_ok)} file(s) already at or below {bitrate}. Nothing to do.")
		return (0, len(already_ok), 0)

	print(f"\n  {len(to_compress)} file(s) to compress, {len(already_ok)} already optimal.\n")

	ensure_dir(output_dir)

	success = 0
	failed = 0
	skipped = 0
	total_saved = 0

	work = []
	for f in to_compress:
		rel = f.relative_to(input_dir)
		out_ext = get_output_ext(f.suffix.lower())
		out_path = output_dir / rel.with_suffix(out_ext)
		work.append((ffmpeg_bin, f, out_path, bitrate))

	with multiprocessing.Pool(processes=jobs) as pool:
		for idx, result in enumerate(pool.imap_unordered(_compress_worker, work), 1):
			ok, src_size, dst_size, msg, fpath = result
			name = Path(fpath).name
			if ok:
				if msg == "already optimal":
					print(f"  [{idx}/{len(work)}] = {name}  ({format_size(src_size)}, already smallest)")
					skipped += 1
				else:
					saved = src_size - dst_size
					total_saved += saved
					ratio = (1 - dst_size / src_size) * 100 if src_size else 0
					print(f"  [{idx}/{len(work)}] + {name}  {format_size(src_size)} → {format_size(dst_size)} ({ratio:.0f}% smaller)")
					success += 1
			else:
				print(f"  [{idx}/{len(work)}] ✗ {name}  FAILED: {msg}")
				failed += 1

	print()
	if total_saved:
		print(f"  Total saved: {format_size(total_saved)}")
	print(f"  Step 3 done: {success} compressed, {skipped} already optimal, {failed} failed\n")

	return (success, skipped, failed)


# ──────────────────────────────────────────────────────────────────────────────
# CLI & main
# ──────────────────────────────────────────────────────────────────────────────

def main():
	parser = argparse.ArgumentParser(
		description=(
			"Unified audio pipeline: convert FLAC→Opus, verify conversion, "
			"and compress non-FLAC audio files – preserving metadata, lyrics, and cover art."
		),
	)
	parser.add_argument(
		"input_dir",
		nargs="?",
		default="/goinfre/zelbassa/spotiFLAC",
		help="Directory containing audio files (default: /goinfre/zelbassa/spotiFLAC)",
	)
	parser.add_argument(
		"-b", "--bitrate",
		default="128k",
		help="Audio bitrate for conversion/compression (default: 128k)",
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
		help="Number of parallel conversions (default: number of CPU cores)",
	)
	parser.add_argument(
		"--min-size",
		type=float,
		default=5.0,
		help="Only compress non-FLAC files larger than this (in MB, default: 5)",
	)
	parser.add_argument(
		"--skip-compress",
		action="store_true",
		help="Skip step 3 (non-FLAC compression) – only convert and verify FLACs",
	)
	args = parser.parse_args()

	input_dir = Path(args.input_dir).resolve()
	output_dir = input_dir.parent / (input_dir.name + "_Opus")

	if not input_dir.is_dir():
		print(f"Error: directory not found: {input_dir}")
		sys.exit(1)

	# Locate ffmpeg
	ffmpeg_bin = find_ffmpeg()
	if not ffmpeg_bin:
		print("Error: ffmpeg not found. Install it or set the FFMPEG env variable.")
		sys.exit(1)

	# Check mutagen
	if not HAS_MUTAGEN:
		print("Warning: mutagen not installed – cover art and lyrics will NOT be transferred.")
		print("  Install it with: pip install mutagen")

	# Banner
	print()
	print("╔" + "═" * 58 + "╗")
	print("║      Audio Pipeline – FLAC→Opus + Verify + Compress      ║")
	print("╚" + "═" * 58 + "╝")
	print()
	print(f"  Input directory : {input_dir}")
	print(f"  Output directory: {output_dir}")
	print(f"  ffmpeg          : {ffmpeg_bin}")
	print(f"  Bitrate         : {args.bitrate}")
	print(f"  Parallel jobs   : {args.jobs}")
	print(f"  Force re-convert: {args.force}")
	if not args.skip_compress:
		print(f"  Compress min MB : {args.min_size}")
	else:
		print(f"  Compress step   : SKIPPED")
	print()

	# ── Step 1: Convert FLAC → Opus ──────────────────────────────────────
	s1_ok, s1_skip, s1_fail = step_convert_flac_to_opus(
		input_dir, output_dir, args.bitrate, args.jobs, args.force, ffmpeg_bin
	)

	# ── Step 2: Verify conversion ────────────────────────────────────────
	converted_list, unconverted_list = step_verify_conversion(input_dir, output_dir)

	# ── Step 3: Compress non-FLAC audio ──────────────────────────────────
	s3_ok, s3_skip, s3_fail = 0, 0, 0
	if not args.skip_compress:
		s3_ok, s3_skip, s3_fail = step_compress_non_flac(
			input_dir, output_dir, args.bitrate, args.min_size, args.jobs, ffmpeg_bin
		)

	# ── Final summary ────────────────────────────────────────────────────
	print("╔" + "═" * 58 + "╗")
	print("║                     FINAL SUMMARY                        ║")
	print("╠" + "═" * 58 + "╣")
	print(f"║  Step 1 (FLAC→Opus) : {s1_ok:>4} converted, {s1_skip:>4} skipped, {s1_fail:>4} failed  ║")
	print(f"║  Step 2 (Verify)    : {len(converted_list):>4} OK, {len(unconverted_list):>4} missing                ║")
	if not args.skip_compress:
		print(f"║  Step 3 (Compress)  : {s3_ok:>4} compressed, {s3_skip:>3} optimal, {s3_fail:>4} failed  ║")
	else:
		print(f"║  Step 3 (Compress)  : SKIPPED                            ║")
	print("╠" + "═" * 58 + "╣")
	print(f"║  Output: {str(output_dir):<49}║")
	print("╚" + "═" * 58 + "╝")

	if unconverted_list:
		print(f"\n⚠  {len(unconverted_list)} FLAC file(s) have NO .opus counterpart.")

	if s1_fail or s3_fail:
		sys.exit(1)


if __name__ == "__main__":
	main()
