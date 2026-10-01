# Optional same-version editing

Use `letools editor` (or `letools-editor`) for v2.1-to-v2.1 and v3.0-to-v3.0
task, episode, feature and existing-MP4 edits. `convert` changes versions and
does not transcode existing LeRobot videos. Distributed conversion does not
execute editor plans.

## Environment

Check `letools editor --help` in the environment owning the active command.
The base runtime report does not verify this addon. From a recursive checkout,
an editor-enabled user tool is installed with:

```bash
uv tool install --force --with ./packages/letools-editor --with-executables-from letools-editor .
```

This source build needs Rust 1.88+ and a platform linker, but no FFmpeg SDK.
For an existing checkout `.venv`, install `uv pip install -e ./packages/letools-editor`
and use `.venv/bin/letools` or `uv run --no-sync`. A base-only sync can remove
the addon. Do not assume `uv pip install` changes an isolated `uv tool` runtime.

## Plan and execute

Inspect features/tasks and resolve **source episode IDs** before editing.
Ranges such as `5:8` select 5, 6 and 7. Task overrides replace all frame tasks
within that episode; do not invent task text or alter unrelated episodes.

```bash
letools editor inspect SOURCE --limit 20
letools editor plan SOURCE DESTINATION \
  --delete-episodes 1,5:8 --set-task '3=Fold the cloth'
letools editor apply SOURCE DESTINATION \
  --delete-episodes 1,5:8 --set-task '3=Fold the cloth'
```

`plan` is read-only but may scan H.264 packets to prove safe deletion. It never
decodes pixels or writes a cached plan. `apply` inspects again; prior planning
is optional. Honor a plan-only request. Execute inside the intended allocation
when authorized, so resource caps reflect that allocation.

The editor has no `--auto`, `--no-validate` or conversion cache. Omit both
`--workers` and `--codec-threads` for automatic caps. Explicit values use the
documented conservative resource caps; check effective values and warnings.

## Preserve the operation boundary

- Write a separate output; source/destination overlap is rejected. Existing
  output replacement requires the user's overwrite authority. The input stays
  immutable throughout planning and execution.
- Use repeatable `--remove-feature` for numeric/image/video features. Required
  index/time/task features cannot be removed. At least one episode must remain.
- Video transform options request encoding, defaulting to libx264. A task-only
  edit needs no video flags. `--video-key` limits a requested transform and
  cannot be used alone; without it, all retained cameras are transformed.
- Use `--resize WIDTHxHEIGHT` for MP4 cameras; embedded image features cannot
  be resized. Exact resizing can change aspect ratio.
- Editor flags are `--pixel-format` and `--codec-threads`, unlike conversion's
  `--video-pixel-format` and `--video-codec-threads`. Editor `--preset` means an
  x264 speed preset, not an HDF5 mapping. H.264 uses `--crf`/`--preset`; MJPEG
  uses `--quality` and requires `--video-codec mjpeg`.
- Inspect remux/transcode counts and warnings. Safe H.264/MJPEG deletion keeps
  packets; unsupported inter-frame cuts reencode affected files at H.264 CRF 0.
  Do not promise packet or pixel identity for that fallback. Encoded-output
  statistics are recomputed after transcoding, never copied from source pixels.

## Validate and report

Retain the operation's built-in validation result and episode index map. Deep
validate when required by the task. Comparing the entire source and edited
output for equality is inappropriate after intentional edits; check retained
rows/tasks and expected index changes. Packet comparison is meaningful for
media that was reused/remuxed, not lossy transforms. Report output version,
counts, effective resources, stages, warnings and any transcoding fallback.

The canonical details are `docs/EDITOR.md`; measured performance and its
codec/statistics limitations are in `docs/EDITOR_BENCHMARK.md`. Deletion's
remux-friendly speedup does not imply faster-than-official reencoding.
