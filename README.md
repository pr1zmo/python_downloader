# Audio Pipeline & Song Downloader

A collection of Python scripts for managing a music library: downloading songs from YouTube, converting FLAC to Opus, verifying conversions, and compressing audio files.

## Scripts

### `main.py` — Audio Pipeline

Three-step pipeline that converts, verifies, and compresses audio files:

1. **Convert** all `.flac` files to `.opus` preserving metadata, lyrics, and cover art
2. **Verify** every `.flac` has a converted `.opus` counterpart
3. **Compress** non-FLAC audio files above a size threshold

```bash
python3 main.py /path/to/music
python3 main.py /path/to/music -b 128k -j 4
python3 main.py /path/to/music --skip-compress
```

| Flag | Description |
|------|-------------|
| `-b`, `--bitrate` | Audio bitrate (default: `128k`) |
| `-j`, `--jobs` | Parallel workers (default: CPU count) |
| `-f`, `--force` | Re-convert existing files |
| `--min-size` | Only compress files larger than this in MB (default: `5`) |
| `--skip-compress` | Skip step 3 |

---

### `songDownload_audio_first.py` — YouTube Song Downloader

Downloads songs from a Spotify-exported CSV via YouTube, preferring audio-only uploads over music videos.

```bash
python3 songDownload_audio_first.py songs.csv -o /path/to/output
python3 songDownload_audio_first.py "https://youtube.com/watch?v=..." -o /path/to/output
```

| Flag | Description |
|------|-------------|
| `-o`, `--out-dir` | Output directory **(required)** |
| `--search-count` | YouTube results to scan per song (default: `15`) |
| `-t`, `--sleep-interval` | Seconds between downloads to avoid rate limiting |

---

### `duplicates.py` — Duplicate File Finder

Finds duplicate files in a directory using MD5 hashing.

```bash
python3 duplicates.py /path/to/search
```

## Requirements

- Python 3.9+
- `ffmpeg` in PATH (or set the `FFMPEG` env variable)
- Python packages:

```bash
pip install -r requirements.txt
```
