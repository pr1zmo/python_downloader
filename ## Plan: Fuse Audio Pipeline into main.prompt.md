## Plan: Fuse Audio Pipeline into main.py

**TL;DR** — Merge flac_to_opus.py, check_flac_converted.py, and compress.py into a single main.py that runs a 3-step pipeline: (1) convert all FLACs to Opus, (2) verify every FLAC has a converted counterpart, (3) compress non-FLAC audio files (mp3, m4a, ogg, etc.) above a size threshold to 128k. Output goes to `<input_dir>_Opus`. Original FLACs are never deleted — only reported.

**Steps**

1. **Create main.py with unified imports** — Merge all imports from the three scripts. Deduplicate: `format_size`, `find_ffmpeg`, `ensure_dir` appear in multiple files. Define them once. Add `find_ffprobe` from compress.py. Keep the `HAS_MUTAGEN` flag with a single try/except importing `mutagen`, `FLAC`, `OggOpus`, `OggVorbis` (the superset of both scripts).

2. **Shared utilities section** — Place these deduplicated functions at the top:
   - `format_size(size_bytes)` — identical across all three files
   - `ensure_dir(path)` — from flac_to_opus.py
   - `find_ffmpeg()` — identical in flac_to_opus.py and compress.py
   - `find_ffprobe(ffmpeg_bin)` — from compress.py

3. **Metadata helpers section** — Consolidate cover art + lyrics transfer:
   - Keep `transfer_metadata(flac_path, opus_path)` from flac_to_opus.py for the FLAC→Opus step (handles both cover art AND lyrics — the compress.py version does not handle lyrics)
   - Keep `_extract_cover_art`, `_embed_cover_art_ogg`, `_embed_cover_art_id3` from compress.py for the compress step (handles all audio formats generically)

4. **Step 1 function: `step_convert_flac_to_opus(input_dir, output_dir, bitrate, jobs, force)`** — Extracted from flac_to_opus.py main(). Collects `.flac` files via `rglob("*.flac")`, builds work list, runs parallel conversion via `multiprocessing.Pool` using `convert_file` + `transfer_metadata`. Returns `(success_count, skipped_count, failed_count)`.

5. **Step 2 function: `step_verify_conversion(input_dir, output_dir)`** — Extracted from check_flac_converted.py main(). For each `.flac` in `input_dir`, checks if corresponding `.opus` exists in `output_dir`. Prints report of converted vs unconverted. Returns `(converted_list, unconverted_list)`. Never deletes — only reports.

6. **Step 3 function: `step_compress_non_flac(input_dir, output_dir, bitrate, min_size_mb, jobs)`** — Extracted from compress.py main(). Scans `input_dir` for non-FLAC audio files (mp3, m4a, ogg, opus, aac, wma, wav, mp4) above `min_size_mb`. Probes bitrate via ffprobe, skips files already at/below target. Re-encodes into `output_dir` preserving format (except `.wav` → `.opus`). Preserves cover art via extract→re-embed. Returns `(success_count, skipped_count, failed_count)`.

7. **Worker functions** — Two separate workers:
   - `_flac_worker(args_tuple)` — wraps `convert_file` for step 1, returns `(ok, name, src_size, dst_size, error)`
   - `_compress_worker(args_tuple)` — wraps `compress_file` for step 3, returns `(ok, src_size, dst_size, msg, filepath)`

8. **CLI with argparse (no subcommands)** — Single positional `input_dir` arg (default: `/goinfre/zelbassa/spotiFLAC`). Optional flags:
   - `-b` / `--bitrate` (default `128k`)
   - `-j` / `--jobs` (default `cpu_count()`)
   - `-f` / `--force` (re-convert existing)
   - `--min-size` (float MB, default `5.0`, for compress step)
   - `--skip-compress` (skip step 3 if user only wants FLAC conversion)
   - Output dir is auto-computed as `<input_dir>_Opus`

9. **`main()` orchestration** — Sequential pipeline:
   ```
   1. Validate input_dir, find ffmpeg, compute output_dir
   2. Print banner with settings
   3. Call step_convert_flac_to_opus()
   4. Call step_verify_conversion()
   5. If unconverted files exist, print warning
   6. Unless --skip-compress, call step_compress_non_flac()
   7. Print final summary of all steps
   ```

10. **Normalize indentation** — Use tabs consistently (matching flac_to_opus.py and check_flac_converted.py style, which the user appears to prefer).

**Verification**
- Run `python3 main.py --help` to confirm CLI args parse correctly
- Run `python3 main.py /path/to/test/dir` with a small test directory containing a few `.flac` and `.mp3` files
- Verify output directory `<input_dir>_Opus` is created with `.opus` files
- Verify the verification step reports all FLACs as converted
- Verify large non-FLAC files are compressed with metadata/art/lyrics preserved
- Run `python3 -m py_compile main.py` to check syntax

**Decisions**
- Output dir: `<input_dir>_Opus` (sibling, not subdirectory)
- Lyrics transfer preserved from `transfer_metadata` (compress.py lacks this)
- FLAC files never deleted — only reported
- Single command pipeline (no subcommands)
- `--skip-compress` flag for users who only need FLAC→Opus
