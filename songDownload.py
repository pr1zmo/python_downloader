#!/usr/bin/env python3
"""
ytb_dl_playlist.py
Reads a Spotify-exported CSV and downloads YouTube audio as MP3.
Requires: Python 3.9+, ffmpeg in PATH, pip install yt-dlp
"""

import argparse
import csv
import os
import sys
import pathlib
from yt_dlp import YoutubeDL

SPOTIFY_TITLE_COL = "Track Name"
SPOTIFY_ARTIST_COL = "Artist Name(s)"

def ensure_dir(p: str) -> None:
	pathlib.Path(p).mkdir(parents=True, exist_ok=True)

def parse_csv(csv_path: str):
	pairs = []

	with open(csv_path, newline="", encoding="utf-8") as f:
		# Try headered CSV first
		sniff = f.read(4096)
		f.seek(0)
		dialect = csv.Sniffer().sniff(sniff) if sniff else csv.excel
		reader = csv.DictReader(f, dialect=dialect)

		if reader.fieldnames and (SPOTIFY_TITLE_COL in reader.fieldnames):
			for row in reader:
				title = (row.get(SPOTIFY_TITLE_COL) or "").strip()
				artist_raw = (row.get(SPOTIFY_ARTIST_COL) or "").strip()
				if not title:
					continue
				# Collapse "A, B, C" → "A B C"
				artist = " ".join(a.strip() for a in artist_raw.split(",") if a.strip())
				pairs.append((title, artist))
			return pairs

	# Fallback to simple CSV without headers: first two columns → title, artist
	with open(csv_path, newline="", encoding="utf-8") as f:
		reader = csv.reader(f)
		for row in reader:
			if not row:
					continue
			title = (row[0] or "").strip()
			artist = (row[1] or "").strip() if len(row) > 1 else ""
			if title:
					pairs.append((title, artist))
	return pairs

def build_ydl_opts(out_dir: str):
	return {
		"quiet": True,
		"noprogress": True,
		"nocheckcertificate": True,
		"ignoreerrors": True,
		"noplaylist": True,
		"default_search": "ytsearch1",
		"outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
		"postprocessors": [
			{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
		],
		"format": "bestaudio/best",
	}

def main():
	ap = argparse.ArgumentParser(description="Lookup songs on YouTube and download as MP3.")
	ap.add_argument("csv", help="Input CSV (Spotify export or title,artist)")
	ap.add_argument("-o", "--out-dir", required=True, help="Output directory for MP3 files")
	args = ap.parse_args()

	csv_path = os.path.abspath(args.csv)
	if not os.path.isfile(csv_path):
		print("ERROR: Input CSV not found", file=sys.stderr)
		sys.exit(1)

	base = os.path.splitext(os.path.basename(csv_path))[0]
	out_dir = os.path.abspath(args.out_dir)
	ensure_dir(out_dir)

	rows = parse_csv(csv_path)
	if not rows:
		print("ERROR: No rows parsed from CSV", file=sys.stderr)
		sys.exit(1)

	print(f"Parsed {len(rows)} tracks")
	print(f"Output directory: {out_dir}")

	failures = []
	succeeded = 0

	ydl_opts = build_ydl_opts(out_dir)
	with YoutubeDL(ydl_opts) as ydl:
		for idx, (title, artist) in enumerate(rows, 1):
			# Guard against raw Spotify links sneaking in as "title"
			if title.startswith(("spotify:", "https://open.spotify.com")):
				failures.append((title, artist, "spotify_url_not_supported"))
				print(f"[{idx}/{len(rows)}] SKIP: Spotify URL not supported → {title}")
				continue

			query = f"ytsearch1:{title} {artist}".strip()
			print(f"[{idx}/{len(rows)}] Searching: {title} — {artist}")

			try:
				info = ydl.extract_info(query, download=True)
				# Handle search results that may be a playlist-like dict
				if info is None:
					failures.append((title, artist, "no_result"))
					print(f"    NOT FOUND")
					continue

				chosen = info
				if "entries" in info:
					chosen = next((e for e in info["entries"] if e), None)

				if not chosen:
					failures.append((title, artist, "no_result"))
					print(f"    NOT FOUND")
					continue

				succeeded += 1
				disp = chosen.get("title") or title
				print(f"    DOWNLOADED: {disp}")
			except Exception as e:
				failures.append((title, artist, str(e)[:200]))
				print(f"    ERROR: {str(e)[:200]}")

	print(f"\nDone. Succeeded: {succeeded} / {len(rows)}")
	if failures:
		nf = os.path.join(out_dir, f"{base}_not_found.csv")
		with open(nf, "w", newline="", encoding="utf-8") as f:
			w = csv.writer(f)
			w.writerow(["title", "artist", "reason"])
			w.writerows(failures)
		print(f"Not found or failed: {len(failures)} → {nf}")

if __name__ == "__main__":
	main()
