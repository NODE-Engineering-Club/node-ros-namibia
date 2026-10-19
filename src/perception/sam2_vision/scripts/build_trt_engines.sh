#!/usr/bin/env bash
# Build the YOLO11s-World and SAM 2 Hiera-Tiny TensorRT engines on the Jetson.
# Run this INSIDE the prod-jetson container (see Containerfile), after
# checkpoints have been placed under /opt/models/.
#
# Not run or validated in this change — no Jetson/CUDA/TensorRT is available
# in the environment this was written in. Every external command is checked
# for success (set -e) and for its expected output file, so a silent partial
# failure here becomes a loud one instead of an engine file that looks
# present but is actually from a stale/failed previous run.
set -euo pipefail

MODELS_DIR="${MODELS_DIR:-/opt/models}"
MAX_PROMPTS="${MAX_PROMPTS:-16}"
INPUT_SIZE="${INPUT_SIZE:-1024}"

mkdir -p "$MODELS_DIR"
cd "$MODELS_DIR"

require_file() {
  if [[ ! -f "$1" ]]; then
    echo "ERROR: expected output file '$1' was not produced." >&2
    exit 1
  fi
}

# ── 1. YOLO11s-World (open-vocabulary detector, used for vision prompts) ──
echo "==> Exporting YOLO11s-World to a TensorRT engine"
python3 -c "
from ultralytics import YOLO
model = YOLO('yolo11s-world.pt')
model.set_classes(['kelp canopy', 'demarcation buoy', 'floating object', 'rope', 'boat'])
model.export(format='engine', device=0, half=True, dynamic=False, imgsz=[800, 1280])
"
require_file "yolo11s-world.engine"

# ── 2. SAM 2 Hiera-Tiny (fixed-shape, max-prompts boxes per inference) ────
echo "==> Exporting SAM 2 Hiera-Tiny to ONNX (fixed shape, max_prompts=${MAX_PROMPTS})"
python3 "$(dirname "${BASH_SOURCE[0]}")/export_sam2_tensorrt.py" \
  --checkpoint "$MODELS_DIR/sam2_hiera_tiny.pt" \
  --output "$MODELS_DIR/sam2_hiera_tiny.onnx" \
  --input-size "$INPUT_SIZE" \
  --max-prompts "$MAX_PROMPTS"
require_file "sam2_hiera_tiny.onnx"

echo "==> Compiling SAM 2 Hiera-Tiny ONNX -> TensorRT engine"
trtexec \
  --onnx="$MODELS_DIR/sam2_hiera_tiny.onnx" \
  --saveEngine="$MODELS_DIR/sam2_hiera_tiny.engine" \
  --fp16 \
  --memPoolSize=workspace:2048MiB \
  --builderOptimizationLevel=5 \
  --device=0
require_file "sam2_hiera_tiny.engine"

echo "==> Done. Engines in $MODELS_DIR:"
ls -la "$MODELS_DIR"/*.engine
echo
echo "sam2_params.yaml's max_prompts MUST equal ${MAX_PROMPTS} (the value"
echo "this engine was built with) — they are not auto-synced."
