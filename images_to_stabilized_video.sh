#!/usr/bin/env bash
set -euo pipefail

STABILIZER=/home/ayaka/procjets/stabilizer/build/video_marker_offline
UPLOAD_YOUTUBE=false

usage() {
    echo "Usage: $0 <image-folder> [fps] [output.mp4] [bgm-audio] [volume]" >&2
    echo "Example: $0 data/20260926-1402 10 output.mp4 music.mp3 0.50" >&2
    exit 2
}

[[ $# -ge 1 && $# -le 5 ]] || usage
image_dir=$1
fps=${2:-10}
output=${3:-"${image_dir%/}.mp4"}
bgm=${4:-}
volume=${5:-0.50}
echo $image_dir
echo $fps
echo $output

command -v ffmpeg >/dev/null 2>&1 || { echo "Error: ffmpeg not found in PATH" >&2; exit 1; }
[[ -d "$image_dir" ]] || { echo "Error: image folder not found: $image_dir" >&2; exit 1; }
[[ "$fps" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "Error: fps must be a positive number" >&2; exit 2; }
awk -v fps="$fps" 'BEGIN { exit !(fps > 0) }' || { echo "Error: fps must be greater than zero" >&2; exit 2; }
if [[ -n "$bgm" ]]; then
    [[ -f "$bgm" ]] || { echo "Error: BGM file not found: $bgm" >&2; exit 1; }
    [[ "$volume" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "Error: volume must be a number from 0 to 2" >&2; exit 2; }
    awk -v volume="$volume" 'BEGIN { exit !(volume >= 0 && volume <= 2) }' || { echo "Error: volume must be between 0 and 2" >&2; exit 2; }
fi

available_filters=$(ffmpeg -hide_banner -filters 2>&1)
[[ "$available_filters" == *vidstabdetect* && "$available_filters" == *vidstabtransform* ]] || {
    echo "Error: this ffmpeg build does not provide vidstabdetect (libvidstab)" >&2
    exit 1
}

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
manifest="$tmpdir/images.ffconcat"
transforms="$tmpdir/transforms.trf"

# Use ffconcat so numbered filenames and gaps in numbering are supported.
# Version sorting orders 00002.jpg before 00010.jpg and remains deterministic
# for older timestamp-named images in folders created before numbered names.
mapfile -d '' files < <(find "$image_dir" -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.jpeg' \) -print0 | LC_ALL=C sort -z -V)
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

echo "Encoding ${#files[@]} images at ${fps} fps..."

if [[ -n "$bgm" ]]; then
    ffmpeg -hide_banner -y \
        -f concat -safe 0 -i "$manifest" \
        -stream_loop -1 -i "$bgm" \
        -map 0:v:0 -map 1:a:0 \
        -af "volume=${volume}" \
        -shortest \
        -r "$fps" \
        -c:v libx264 \
        -crf 18 \
        -preset medium \
        -pix_fmt yuv420p \
        -c:a aac \
        "$output"
else
    ffmpeg -hide_banner -y \
        -f concat -safe 0 -i "$manifest" \
        -r "$fps" \
        -c:v libx264 \
        -crf 18 \
        -preset medium \
        -pix_fmt yuv420p \
        "$output"
fi


echo "Done: $output"
output2="${output%.*}_stabilized.${output##*.}"
echo "$output2"
"$STABILIZER" "$output" "$output2" --crop-valid 
echo "Stabilized: $output2"

if [ "$UPLOAD_YOUTUBE" = true ]; then
    # Upload the completed video with the last captured image as its thumbnail.
    script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
    python=${script_dir}/venv/bin/python
    [[ -x "$python" ]] || { echo "Error: project virtualenv Python not found: $python" >&2; exit 1; }
    title=$(basename -- "$output")
    echo "Uploading to YouTube: $title"
    "$python" "$script_dir/upload_youtube.py" "$output2" "${files[-1]}" "$title"
fi
