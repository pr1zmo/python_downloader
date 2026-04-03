#!/usr/bin/env python3
"""
compress.py – Re-encode non-FLAC audio files >5MB to 128k to reduce size.

Loops through a music directory, finds non-FLAC audio files larger than a
size threshold, and re-encodes them at 128k bitrate into a separate output
directory (compressed/) while preserving all metadata, lyrics, and cover art.

Requirements:
    - ffmpeg (system binary)
    - mutagen (pip install mutagen) – for cover art on opus/ogg files

Usage:
    python3 compress.py /path/to/music/dir
    python3 compress.py /path/to/music/dir -b 128k --min-size 5
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
    import mutagen
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

SIZE_MB = 1024 * 1024

# Supported audio extensions (excluding .flac – handled by flac_to_opus.py)
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wma", ".wav", ".mp4"}
# Containers that support embedded picture streams
CONTAINERS_WITH_ART = {".mp3", ".m4a", ".mp4", ".aac", ".wma"}
# Containers that do NOT support picture streams (ogg family)
CONTAINERS_NO_ART = {".opus", ".ogg", ".wav"}


def _extract_cover_art(filepath):
    """Extract cover art bytes from any audio file using mutagen.

    Returns a list of raw picture block bytes (for METADATA_BLOCK_PICTURE),
    or an empty list if no art found.
    """
    if not HAS_MUTAGEN:
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
                import base64 as b64
                from mutagen.flac import Picture
                raw = []
                for p in pics:
                    raw.append(Picture(b64.b64decode(p)).write())
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


def find_ffmpeg():
    """Locate a usable ffmpeg binary."""
    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_bin and os.path.isfile(ffmpeg_bin) and os.access(ffmpeg_bin, os.X_OK):
            return ffmpeg_bin
    except Exception:
        pass

    env_ffmpeg = os.environ.get("FFMPEG")
    if env_ffmpeg and os.path.isfile(env_ffmpeg) and os.access(env_ffmpeg, os.X_OK):
        return env_ffmpeg

    home_ffmpeg = os.path.expanduser("~/bin/ffmpeg")
    if os.path.isfile(home_ffmpeg) and os.access(home_ffmpeg, os.X_OK):
        return home_ffmpeg

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


def compress_file(ffmpeg_bin, filepath, output_path, bitrate):
    """Re-encode a single audio file to output_path at the target bitrate.

    Strategy: always do audio-only ffmpeg (simple, never fails), then copy
    cover art from the original to the output via mutagen.

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
        # Step 1: Extract cover art from original BEFORE conversion
        picture_blocks = _extract_cover_art(filepath)

        # Step 2: Audio-only ffmpeg conversion (no picture streams, no issues)
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

        # Step 3: Re-embed cover art from original into the compressed file
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


def _worker(args_tuple):
    """Worker function for multiprocessing pool."""
    ffmpeg_bin, filepath, output_path, bitrate = args_tuple
    ok, src_size, dst_size, msg = compress_file(ffmpeg_bin, filepath, output_path, bitrate)
    return (ok, src_size, dst_size, msg, str(filepath))


def format_size(size_bytes):
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def main():
    parser = argparse.ArgumentParser(
        description="Re-encode non-FLAC audio files >5MB to 128k to save space."
    )
    parser.add_argument(
        "input_dir",
        help="Directory containing audio files",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output directory (default: <input_dir>/compressed)",
    )
    parser.add_argument(
        "-b", "--bitrate",
        default="128k",
        help="Target audio bitrate (default: 128k)",
    )
    parser.add_argument(
        "--min-size",
        type=float,
        default=5.0,
        help="Only compress files larger than this (in MB, default: 5)",
    )
    parser.add_argument(
        "-j", "--jobs",
        type=int,
        default=multiprocessing.cpu_count(),
        help="Parallel conversions (default: number of CPU cores)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be compressed without doing it",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output).resolve() if args.output else input_dir / "compressed"
    min_bytes = int(args.min_size * SIZE_MB)

    if not input_dir.is_dir():
        print(f"Error: directory not found: {input_dir}")
        sys.exit(1)

    ffmpeg_bin = find_ffmpeg()
    if not ffmpeg_bin:
        print("Error: ffmpeg not found. Install it or set the FFMPEG env variable.")
        sys.exit(1)
    print(f"Using ffmpeg: {ffmpeg_bin}")

    ffprobe_bin = find_ffprobe(ffmpeg_bin)

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
        print(f"No non-FLAC audio files >{args.min_size}MB found in {input_dir}")
        sys.exit(0)

    print(f"Found {len(candidates)} non-FLAC file(s) >{args.min_size}MB in {input_dir}")
    print(f"Output directory: {output_dir}")
    print(f"Target bitrate: {args.bitrate}")
    print(f"Parallel jobs: {args.jobs}")

    # Check current bitrates and decide what to compress
    print()
    already_ok = []
    to_compress = []
    target_kbps = int(args.bitrate.rstrip("kK"))

    for f in candidates:
        current_br = get_audio_bitrate(ffprobe_bin, f)
        size = f.stat().st_size
        rel = f.relative_to(input_dir)
        br_str = f"{current_br}k" if current_br else "?"

        if current_br and current_br <= target_kbps:
            print(f"  SKIP {rel}  ({format_size(size)}, {br_str} <= {args.bitrate})")
            already_ok.append(f)
        else:
            print(f"  QUEUE {rel}  ({format_size(size)}, {br_str} -> {args.bitrate})")
            to_compress.append(f)

    if not to_compress:
        print(f"\nAll {len(already_ok)} file(s) already at or below {args.bitrate}. Nothing to do.")
        sys.exit(0)

    print(f"\n{len(to_compress)} file(s) to compress, {len(already_ok)} already optimal.")

    if args.dry_run:
        print("(dry run - no files modified)")
        sys.exit(0)

    print()

    success = 0
    failed = 0
    skipped = 0
    total_saved = 0

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    work = []
    for f in to_compress:
        rel = f.relative_to(input_dir)
        out_ext = get_output_ext(f.suffix.lower())
        out_path = output_dir / rel.with_suffix(out_ext)
        work.append((ffmpeg_bin, f, out_path, args.bitrate))

    with multiprocessing.Pool(processes=args.jobs) as pool:
        for idx, result in enumerate(pool.imap_unordered(_worker, work), 1):
            ok, src_size, dst_size, msg, fpath = result
            name = Path(fpath).name
            if ok:
                if msg == "already optimal":
                    print(f"[{idx}/{len(work)}] = {name}  ({format_size(src_size)}, already smallest)")
                    skipped += 1
                else:
                    saved = src_size - dst_size
                    total_saved += saved
                    ratio = (1 - dst_size / src_size) * 100 if src_size else 0
                    print(f"[{idx}/{len(work)}] + {name}  {format_size(src_size)} -> {format_size(dst_size)} ({ratio:.0f}% smaller)")
                    success += 1
            else:
                print(f"[{idx}/{len(work)}] x {name}  FAILED: {msg}")
                failed += 1

    # Summary
    print()
    print("=" * 50)
    print(f"Done! {success} compressed, {skipped} already optimal, {failed} failed")
    if total_saved:
        print(f"Total saved: {format_size(total_saved)}")
    print("=" * 50)


if __name__ == "__main__":
    main()