#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 <image-folder> [fps] [output.mp4]" >&2
    echo "Example: $0 data/20260926-1402 10 data/20260926-1402_stabilized.mp4" >&2
    exit 2
}

[[ $# -ge 1 && $# -le 3 ]] || usage
image_dir=$1
fps=${2:-10}
output=${3:-"${image_dir%/}_stabilized.mp4"}

command -v ffmpeg >/dev/null 2>&1 || { echo "Error: ffmpeg not found in PATH" >&2; exit 1; }
[[ -d "$image_dir" ]] || { echo "Error: image folder not found: $image_dir" >&2; exit 1; }
[[ "$fps" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "Error: fps must be a positive number" >&2; exit 2; }
awk -v fps="$fps" 'BEGIN { exit !(fps > 0) }' || { echo "Error: fps must be greater than zero" >&2; exit 2; }

available_filters=$(ffmpeg -hide_banner -filters 2>&1)
[[ "$available_filters" == *vidstabdetect* && "$available_filters" == *vidstabtransform* ]] || {
    echo "Error: this ffmpeg build does not provide vidstabdetect (libvidstab)" >&2
    exit 1
}

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
manifest="$tmpdir/images.ffconcat"
transforms="$tmpdir/transforms.trf"

# Use ffconcat so timestamped filenames and gaps in numbering are supported.
# Sorting in the C locale gives stable, bytewise filename order.
mapfile -d '' files < <(find "$image_dir" -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.jpeg' \) -print0 | LC_ALL=C sort -z)
((${#files[@]} > 0)) || { echo "Error: no JPG/JPEG images found in $image_dir" >&2; exit 1; }

python3 - "$manifest" "$fps" "${files[@]}" <<'PY'
import pathlib
import sys

manifest, fps, *files = sys.argv[1:]
duration = 1 / float(fps)
lines = ["ffconcat version 1.0"]
for filename in files:
    # ffconcat uses single quotes; escape embedded quotes and backslashes.
    escaped = pathlib.Path(filename).resolve().as_posix().replace("'", "'\\''")
    lines.extend((f"file '{escaped}'", f"duration {duration:.12g}"))
# The concat demuxer needs the final file repeated for the last duration to apply.
escaped = pathlib.Path(files[-1]).resolve().as_posix().replace("'", "'\\''")
lines.append(f"file '{escaped}'")
pathlib.Path(manifest).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

echo "Analyzing ${#files[@]} images at ${fps} fps..."
ffmpeg -hide_banner -y -f concat -safe 0 -i "$manifest" \
    -vf "vidstabdetect=shakiness=1:accuracy=15:stepsize=4:mincontrast=0.3:result=$transforms" \
    -f null -

echo "Stabilizing and encoding: $output"
ffmpeg -hide_banner -y -f concat -safe 0 -i "$manifest" \
    -vf "vidstabtransform=input=$transforms:smoothing=10:zoom=0:optzoom=0:crop=black" \
    -r "$fps" -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p "$output"

echo "Done: $output"
