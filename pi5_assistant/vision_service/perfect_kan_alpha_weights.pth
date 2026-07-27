"""
KAN-based adaptive camera controller for Vision Service.
Decides between RES-priority and FPS-priority mode based on scene semantics.

Inputs : [s_id, delta_s_id]
Output : alpha in [0, 1]
    alpha -> 0 : RES-priority  (2304x1296 @ 56 fps)
    alpha -> 1 : FPS-priority  (1536x864  @ 120 fps)
"""

import torch
import torch.nn as nn


# ── B-spline KAN implementation ───────────────────────────────────────────────

class KANLinear(nn.Module):
    def __init__(self, in_features, out_features, grid_size=5):
        super().__init__()
        self.in_features  = in_features
        self.out_features = out_features

        grid = torch.linspace(-1, 1, grid_size + 1).unsqueeze(0)
        self.register_buffer("grid", grid)

        self.base_weight   = nn.Parameter(torch.randn(out_features, in_features) * 0.1)
        self.spline_weight = nn.Parameter(torch.randn(out_features, in_features, grid_size) * 0.1)
        self.base_act      = nn.SiLU()

    def _b_splines(self, x):
        x = x.unsqueeze(-1)
        return ((x >= self.grid[:, :-1]) & (x < self.grid[:, 1:])).float()

    def forward(self, x):
        base_out   = nn.functional.linear(self.base_act(x), self.base_weight)
        spline_out = torch.einsum("bid,oid->bo", self._b_splines(x), self.spline_weight)
        return base_out + spline_out


class KAN(nn.Module):
    def __init__(self, layers_hidden):
        super().__init__()
        self.layers = nn.ModuleList([
            KANLinear(i, o)
            for i, o in zip(layers_hidden[:-1], layers_hidden[1:])
        ])

    def forward(self, x):
        for layer in self.layers:
            x = layer(x)
        return x


class StudentKAN(nn.Module):
    """Lightweight KAN student model: [s_id, delta_s_id] -> alpha."""

    def __init__(self):
        super().__init__()
        self.net = KAN([2, 4, 1])

    def forward(self, x):
        return torch.sigmoid(self.net(x))


# ── Helper functions ──────────────────────────────────────────────────────────

def compute_s_id(detections: list[dict], frame_w: int, frame_h: int) -> float:
    """Compute semantic information density from YOLO/Hailo detections."""
    if not detections:
        return 0.0
    frame_area  = frame_w * frame_h
    total_area  = sum(d["bbox"][2] * d["bbox"][3] for d in detections)
    area_ratio  = min(total_area / frame_area, 1.0)
    count_score = min(len(detections) / 10.0, 1.0)
    return 0.7 * area_ratio + 0.3 * count_score


def load_kan(weights_path: str) -> StudentKAN | None:
    """Load KAN from a .pth file. Returns None if file not found."""
    model = StudentKAN()
    try:
        model.load_state_dict(torch.load(weights_path, map_location="cpu"))
        model.eval()
        return model
    except Exception as e:
        print(f"[KAN] Failed to load weights: {e}")
        return None


def kan_infer(model: StudentKAN, s_id: float, delta_s_id: float) -> float:
    """Run KAN inference. Returns alpha in [0, 1]."""
    inp = torch.tensor([[s_id, delta_s_id]], dtype=torch.float32)
    with torch.no_grad():
        return float(model(inp).numpy()[0][0])
