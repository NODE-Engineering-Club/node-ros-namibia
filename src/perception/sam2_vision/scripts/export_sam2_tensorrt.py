#!/usr/bin/env python3
"""Export a SAM 2 Hiera-Tiny checkpoint to a fixed-shape ONNX graph for
trtexec, matching the I/O contract sam2_inference_node.py expects:

    input "input_image"  float32 (1, 3, S, S)        ImageNet-normalized RGB
    input "input_boxes"  float32 (1, max_prompts, 4)  x1,y1,x2,y2 in [0,S)
    output "output_masks" float32 (1, max_prompts, S, S)  per-box mask logits

IMPORTANT — this script has not been run: this environment has no GPU, no
CUDA, and no PyTorch/SAM2 install, so it could not be executed or validated
against a real facebookresearch/sam2 checkout. It is written against the
public SAM2ImagePredictor API as documented at the time of writing. The
`sam2` package's internals have changed across releases — before trusting
this on real hardware:
  1. Run it once and load the resulting .onnx with onnxruntime on the CPU,
     feed a dummy image + a known box, and sanity-check the output shape
     and that the mask roughly matches the box location.
  2. Only then run build_trt_engines.sh's trtexec step.

The image encoder runs ONCE per frame; the decoder loop below re-uses that
one embedding for every box prompt, which is both the efficient and the
documented way to query SAM 2 with multiple prompts against one image — not
a fallback or a hack.
"""
import argparse

import torch
from torch import nn


class SAM2FixedPromptWrapper(nn.Module):
    """Wraps a SAM2ImagePredictor's underlying model so ONNX export sees one
    graph with a fixed (1, N, 4) box-prompt input and a fixed (1, N, S, S)
    mask output, image embedding computed once and reused for all N boxes.
    """

    def __init__(self, sam2_model, max_prompts: int):
        super().__init__()
        self.model = sam2_model
        self.max_prompts = max_prompts

    def forward(self, image: torch.Tensor, boxes: torch.Tensor) -> torch.Tensor:
        # image: (1, 3, S, S); boxes: (1, max_prompts, 4) in pixel coords.
        backbone_out = self.model.forward_image(image)
        _, vision_feats, vision_pos_embeds, feat_sizes = self.model._prepare_backbone_features(backbone_out)
        image_embed = vision_feats[-1].permute(1, 2, 0).reshape(1, -1, *feat_sizes[-1])

        masks_out = []
        for i in range(self.max_prompts):
            box = boxes[:, i, :]  # (1, 4)
            sparse_embeddings, dense_embeddings = self.model.sam_prompt_encoder(
                points=None, boxes=box, masks=None,
            )
            low_res_masks, _, _, _ = self.model.sam_mask_decoder(
                image_embeddings=image_embed,
                image_pe=self.model.sam_prompt_encoder.get_dense_pe(),
                sparse_prompt_embeddings=sparse_embeddings,
                dense_prompt_embeddings=dense_embeddings,
                multimask_output=False,
                repeat_image=False,
            )
            masks_out.append(torch.sigmoid(low_res_masks[:, 0]))  # (1, S', S')

        masks = torch.stack(masks_out, dim=1)  # (1, N, S', S')
        return nn.functional.interpolate(
            masks, size=image.shape[-2:], mode="bilinear", align_corners=False,
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", required=True, help="Path to sam2_hiera_tiny.pt")
    ap.add_argument("--output", required=True, help="Output .onnx path")
    ap.add_argument("--input-size", type=int, default=1024)
    ap.add_argument("--max-prompts", type=int, default=16, help="Must match sam2_params.yaml's max_prompts")
    ap.add_argument("--opset", type=int, default=17)
    args = ap.parse_args()

    # Deferred import: only needed when this script actually runs on the Jetson.
    from sam2.build_sam import build_sam2  # facebookresearch/sam2

    sam2_model = build_sam2("sam2_hiera_t.yaml", args.checkpoint, device="cuda")
    sam2_model.eval()
    wrapper = SAM2FixedPromptWrapper(sam2_model, args.max_prompts).cuda().eval()

    dummy_image = torch.randn(1, 3, args.input_size, args.input_size, device="cuda")
    dummy_boxes = torch.zeros(1, args.max_prompts, 4, device="cuda")
    dummy_boxes[:, :, 2:] = 1.0  # degenerate but valid (x2>x1, y2>y1) placeholder boxes

    torch.onnx.export(
        wrapper,
        (dummy_image, dummy_boxes),
        args.output,
        input_names=["input_image", "input_boxes"],
        output_names=["output_masks"],
        opset_version=args.opset,
        dynamic_axes=None,  # fixed shapes only — matches the Jetson engine's fixed-batch build
    )
    print(f"Exported fixed-shape ONNX graph to {args.output} "
          f"(input_size={args.input_size}, max_prompts={args.max_prompts}).")
    print("Next: validate with onnxruntime on CPU before running trtexec.")


if __name__ == "__main__":
    main()
