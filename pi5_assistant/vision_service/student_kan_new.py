"""
Inputs : [s_id, delta_s_id]
Output : alpha in [0, 1]
    alpha -> 0 : RES-priority  (2304x1296 @ 56 fps)
    alpha -> 1 : FPS-priority  (1536x864  @ 120 fps)
"""

import numpy as np


def _silu(x):
    return x / (1.0 + np.exp(-x))  # ability to curve the output of the linear layer


class KANLinear:
    def __init__(self, base_weight, spline_weight, grid):
        self.base_weight   = base_weight    # [out_features, in_features]
        self.spline_weight = spline_weight  # [out_features, in_features, grid_size]
        self.grid          = grid           # [-1,-0.6,-0.2,0.2,0.6,1]

    def _b_splines(self, x):
        x = x[:, :, None]  # add a dimension: [batch, in] -> [batch, in, 1]
        return ((x >= self.grid[:, :-1]) & (x < self.grid[:, 1:])).astype(np.float32)

    def __call__(self, x):
        base_out   = _silu(x) @ self.base_weight.T
        spline_out = np.einsum("bid,oid->bo", self._b_splines(x), self.spline_weight)
        # b=sample, i=in_features, o=out_features, d=grid_size
        # out_features=4, Midpoint 1: responsible for determining when there are many targets
        # Midpoint 2: responsible for determining when changes occur rapidly
        # Midpoint 3: responsible for determining when the scene is crowded
        # Midpoint 4: responsible for determining when the scene is static
        return base_out + spline_out


class KAN:
    def __init__(self, layers):
        # two KANLinear layers: first takes 2 inputs -> 4, second takes 4 -> 1
        # to calculate the alpha value
        self.layers = layers

    def __call__(self, x):
        for layer in self.layers:  # self.layers = [KANLinear(2,4), KANLinear(4,1)]
            x = layer(x)
        return x


class StudentKAN:
    def __init__(self, weights_path="kan_weights.npz"):
        w = np.load(weights_path)
        layers = []
        i = 0
        while f"net.layers.{i}.base_weight" in w:
            layers.append(KANLinear(
                w[f"net.layers.{i}.base_weight"],
                w[f"net.layers.{i}.spline_weight"],
                w[f"net.layers.{i}.grid"],
            ))
            i += 1
        self.net = KAN(layers)

    def __call__(self, x):
        z = self.net(x)
        return 1.0 / (1.0 + np.exp(-z))  # sigmoid, ensure output between 0 and 1 (range for alpha)


def compute_s_id(detections: list[dict], frame_w: int, frame_h: int) -> float:
    if not detections:
        return 0.0
    frame_area  = frame_w * frame_h
    total_area  = sum(d["bbox"][2] * d["bbox"][3] for d in detections)  # bbox=[x, y, w, h] -> area = w * h
    area_ratio  = min(total_area / frame_area, 1.0)  # total area of targets, max value is 1
    count_score = min(len(detections) / 10.0, 1.0)   # number of targets, max value is 1
    return 0.7 * area_ratio + 0.3 * count_score
# the formula above is used to calculate the s_id value, which is a measure of the scene complexity.
# It takes into account both the area of the detected objects and the number of detected objects.
# The area_ratio represents how much of the frame is occupied by detected objects, while the count_score represents how many objects are detected.
# The final s_id value is a weighted sum of these two scores, with more weight given to the area_ratio.
# 0 represents a simple scene with few or small objects, need resolution priority
# while 1 represents a complex scene with many or large objects, need fps priority


def load_kan(weights_path="kan_weights.npz"):
    try:
        return StudentKAN(weights_path)
    except Exception as e:
        print(f"[KAN] Failed to load weights: {e}")
        return None


def kan_infer(model, s_id: float, delta_s_id: float) -> float:
    inp = np.array([[s_id, delta_s_id]], dtype=np.float32)
    return float(model(inp)[0][0])
    # def select_profile(self, s_id, delta_s_id):
    # alpha = kan_infer(self.kan, s_id, delta_s_id)
    # return "high_motion" if alpha > 0.5 else "low_motion"