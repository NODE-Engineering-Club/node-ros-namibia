#!/usr/bin/env bash
# Model weights too large for git. Idempotent: skips files already present and checked.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models/ewasr
# eWaSR ResNet-18 water/sky/obstacle segmentation (MaSTr1325), Apache-2.0,
# https://github.com/tersekmatija/eWaSR release 0.1.0. Used by sensors/oak_rgbd (water_seg_model).
f=models/ewasr/ewasr_resnet18.onnx
sum=15e520fc5f7e910c9367556db9a9c1107bdc9c74e7982b3bf89c7050509411d7
if ! echo "$sum  $f" | sha256sum -c --quiet 2>/dev/null; then
  curl -fL --retry 3 -o "$f" https://github.com/tersekmatija/eWaSR/releases/download/0.1.0/ewasr_resnet18.onnx
  echo "$sum  $f" | sha256sum -c
fi
echo "models ok"
