#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DEFAULT_INPUT="$PROJECT_ROOT/docs/Cover.mov"
DEFAULT_OUTPUT="$PROJECT_ROOT/docs/Cover.webp"

INPUT="${1:-$DEFAULT_INPUT}"
OUTPUT="${2:-$DEFAULT_OUTPUT}"

if ! command -v ffmpeg >/dev/null 2>&1; then
	echo "Error: ffmpeg is not installed or not in PATH."
	exit 1
fi

if [[ ! -f "$INPUT" ]]; then
	echo "Error: input file not found: $INPUT"
	exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"

echo "Converting: $INPUT"
echo "Output:     $OUTPUT"

ffmpeg -y -i "$INPUT" \
	-t 4 \
	-vf "fps=15,scale=800:-1:flags=lanczos,unsharp=5:5:0.8:3:3:0.4" \
	-c:v libwebp \
	-lossless 0 \
	-q:v 82 \
	-compression_level 6 \
	-preset picture \
	-loop 0 \
	-an \
	"$OUTPUT"

echo "Saved $OUTPUT"
