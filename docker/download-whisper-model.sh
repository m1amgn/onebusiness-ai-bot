#!/bin/sh
set -eu

model=${1:-${WHISPER_MODEL:-base}}
directory=${2:-/models}
case "$model" in
  base) checksum=60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe ;;
  *) echo "Invalid WHISPER_MODEL: supported model is base" >&2; exit 2 ;;
esac

mkdir -p "$directory"
target="$directory/ggml-$model.bin"
[ ! -f "$target" ] || exit 0

# A failed or interrupted transfer is never mistaken for the verified model.
partial="$target.part"
curl -fL --connect-timeout 10 --max-time 300 \
  --speed-limit 1024 --speed-time 20 \
  --retry 5 --retry-max-time 300 --retry-all-errors --output "$partial" \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$model.bin"
printf '%s  %s\n' "$checksum" "$partial" | sha256sum -c -
mv "$partial" "$target"
