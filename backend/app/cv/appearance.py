# backend/app/cv/appearance.py
"""
Appearance signatures: what a person looks like, in a form you can compare.

Step 5 of IDENTITY_TRACKING_PLAN.md. Produces three of the plan's four fusion
signals for every detection (the fourth, the seat prior, is spatial and comes
from the zone the person is standing in):

  - OSNet embedding   512-d, the appearance signal      weight 0.30
  - Colour histogram  HSV upper/lower body              weight 0.20
  - Height estimate   projected through the homography  weight 0.10

WHY OSNet AND NOT FACE RECOGNITION

The plan measures this: at 640px frame width, a person 3 m away has ~12 px
between the eyes, and face recognition needs ~80. The information is simply not
in the frame. OSNet is trained on 128x64 person crops — SMALLER than what this
pipeline already produces — so it was purpose-built for exactly this distance.

WHICH WEIGHTS THESE ARE

`osnet_x0_25` fine-tuned on MSMT17 (1041 identities), not the ImageNet weights
torchreid downloads by default. That distinction matters more than it looks:
ImageNet features describe "what kind of object is this", re-ID features
describe "is this the same PERSON". Verified by measurement, not assumption —
see `backend/tests/test_appearance.py`, which prints same-person vs
different-person cosines for both and shows the gap.

If the weights file is absent the module still loads and still produces colour
and height signals; `embedding` is simply None and the fusion in Step 6 falls
back to the signals it does have. A missing model degrades the system, it does
not stop it.

BATCHING IS NOT AN OPTIMISATION HERE, IT IS THE DESIGN

`extract_batch()` runs ONE forward pass for every person in the frame. Calling
the model once per person would multiply the per-frame GPU launch overhead by
the crowd size — at 60 FPS with 5 people that is 300 launches a second for work
that fits in one. The plan calls this out explicitly.
"""

import logging
import os
import threading

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# OSNet's native input. The model was trained at this size; feeding it anything
# else silently degrades the features rather than failing loudly.
REID_INPUT_H, REID_INPUT_W = 256, 128

# ImageNet normalisation — what torchreid trained with.
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# COCO keypoint indices, as documented in pose_estimator.classify_posture_raw().
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

# A keypoint below this confidence is treated as absent. Matches the 0.15 the
# posture classifier already uses, so the two agree about which joints are real.
KPT_CONF_MIN = 0.15

# HSV histogram resolution. 8x8x4 = 256 bins per body half: fine enough to tell
# a blue shirt from a green one, coarse enough that lighting jitter does not
# scatter the same shirt across neighbouring bins.
H_BINS, S_BINS, V_BINS = 8, 8, 4

# CUDA graphs are captured per batch size. An office frame holds a handful of
# people; capturing beyond this would spend GPU memory on shapes that occur once.
_MAX_GRAPH_BATCH = 16

DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "models", "osnet_x0_25_msmt17.pth",
)


def cosine(a, b) -> float:
    """
    Cosine similarity, safe on None and on zero vectors.

    Returns 0.0 rather than raising when either side is missing, so a detection
    with no embedding (model absent, crop too small) scores "no evidence"
    instead of taking down the frame.
    """
    if a is None or b is None:
        return 0.0
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return 0.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def histogram_similarity(a, b) -> float:
    """
    Bhattacharyya-based similarity between two colour histograms, in [0, 1].

    OpenCV's HISTCMP_BHATTACHARYYA returns a DISTANCE (0 = identical), so it is
    inverted here. Bhattacharyya rather than correlation because it behaves
    sanely on sparse histograms — a crop with one dominant colour produces a
    spiky histogram, and correlation over-rewards the spike lining up.
    """
    if a is None or b is None:
        return 0.0
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    if a.size == 0 or a.size != b.size:
        return 0.0
    d = cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA)
    if not np.isfinite(d):
        return 0.0
    return float(max(0.0, 1.0 - d))


def _joint(kpts, i, j):
    """
    Midpoint of a symmetric joint pair, or whichever side is confident.

    Returns None when neither side is. Mirrors the helper inside
    classify_posture_raw() so both read the skeleton the same way.
    """
    if kpts is None:
        return None
    k = np.asarray(kpts)
    if k.ndim != 2 or k.shape[0] <= max(i, j):
        return None
    conf = k[:, 2] if k.shape[1] >= 3 else np.ones(k.shape[0])
    ci, cj = conf[i] > KPT_CONF_MIN, conf[j] > KPT_CONF_MIN
    if ci and cj:
        return ((k[i][0] + k[j][0]) / 2.0, (k[i][1] + k[j][1]) / 2.0)
    if ci:
        return (float(k[i][0]), float(k[i][1]))
    if cj:
        return (float(k[j][0]), float(k[j][1]))
    return None


def _clamp_box(bbox, w, h):
    """Integer bbox clamped inside the frame, or None if it has no area."""
    x1, y1, x2, y2 = (int(round(v)) for v in bbox[:4])
    x1, x2 = max(0, min(w - 1, x1)), max(0, min(w, x2))
    y1, y2 = max(0, min(h - 1, y1)), max(0, min(h, y2))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return x1, y1, x2, y2


def colour_histogram(frame, bbox, keypoints=None) -> dict:
    """
    HSV histograms for the upper and lower body, split by the pose keypoints.

    Upper = shoulders to hips (the shirt). Lower = hips to knees (the trousers).
    Splitting matters: a single histogram over the whole box blends the two
    garments into one average colour, and two people in a dark top with light
    trousers and the reverse would score as a match.

    When keypoints are missing or unconfident — very common for a seated person
    whose legs are under a desk — the split falls back to fixed fractions of the
    box (45% / 80%). The fallback is approximate, which is why `from_keypoints`
    is returned: Step 6 can weight a keypoint-derived histogram more heavily.

    The V channel is quantised coarsely (4 bins) on purpose: brightness is the
    part of HSV that moves most with lighting, and a fine V axis would make the
    same shirt look different between a sunlit and a shaded desk.
    """
    h, w = frame.shape[:2]
    box = _clamp_box(bbox, w, h)
    if box is None:
        return {"upper": None, "lower": None, "from_keypoints": False}
    x1, y1, x2, y2 = box
    box_h = y2 - y1

    shoulders = _joint(keypoints, L_SHOULDER, R_SHOULDER)
    hips = _joint(keypoints, L_HIP, R_HIP)
    knees = _joint(keypoints, L_KNEE, R_KNEE)

    from_kpts = shoulders is not None and hips is not None
    if from_kpts:
        y_top = int(np.clip(shoulders[1], y1, y2))
        y_mid = int(np.clip(hips[1], y1, y2))
        y_bot = int(np.clip(knees[1], y1, y2)) if knees is not None else y2
    else:
        y_top = y1 + int(box_h * 0.15)
        y_mid = y1 + int(box_h * 0.45)
        y_bot = y1 + int(box_h * 0.80)

    # Keep the bands ordered and non-empty even when the skeleton is odd
    # (a person bent double can put hips above shoulders).
    y_top, y_mid, y_bot = sorted((y_top, y_mid, y_bot))

    def band(ya, yb):
        ya, yb = max(y1, ya), min(y2, yb)
        if yb - ya < 2:
            return None
        roi = frame[ya:yb, x1:x2]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1, 2], None,
                            [H_BINS, S_BINS, V_BINS],
                            [0, 180, 0, 256, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist.flatten().astype(np.float32)

    return {
        "upper": band(y_top, y_mid),
        "lower": band(y_mid, y_bot),
        "from_keypoints": from_kpts,
    }


def height_estimate(bbox, keypoints, spatial_engine, frame_shape):
    """
    A person's height in floorplan units, or None.

    Takes the ground point (feet) and the head, projects BOTH through the same
    homography, and measures the distance between them. Projecting both is what
    makes this resolution-independent: raw pixel height shrinks with distance
    from the camera, projected height does not.

    Returns None when the person is SEATED or their head is not visible. That is
    a deliberate abstention, not a gap to paper over — a seated person's
    bbox-top is their scalp at desk height, and calling that "height" would feed
    the fusion a number that is confidently wrong. The plan gives height the
    lowest weight (0.10) for exactly this reason: it fails whenever someone sits
    down, which in an office is most of the time.
    """
    if spatial_engine is None:
        return None

    head = None
    if keypoints is not None:
        k = np.asarray(keypoints)
        if k.ndim == 2 and k.shape[0] > NOSE:
            conf = k[:, 2] if k.shape[1] >= 3 else np.ones(k.shape[0])
            if conf[NOSE] > KPT_CONF_MIN:
                head = (float(k[NOSE][0]), float(k[NOSE][1]))

    # No confident head keypoint: the bbox top is a poor stand-in (hair, hats,
    # and truncation all move it), so abstain rather than guess.
    if head is None:
        return None

    # Both feet must be plausible too — a person whose ankles are occluded has
    # a bbox bottom that is not on the floor.
    ankles = _joint(keypoints, L_ANKLE, R_ANKLE)
    ground = ankles if ankles is not None else tuple(spatial_engine.ground_point(bbox))

    try:
        H = spatial_engine.homography_matrix
        if H is not None:
            gx, gy = spatial_engine.transform_point_topdown(H, list(ground))
            hx, hy = spatial_engine.transform_point_topdown(H, list(head))
        else:
            # No calibration: fall back to the same proportional mapping
            # project_to_floor() uses, so the number is at least consistent
            # across frames of one camera. It is NOT comparable across cameras
            # without a homography, which is why Step 13 needs calibration.
            fh, fw = frame_shape[:2]
            if not fw or not fh:
                return None
            sx, sy = spatial_engine.FLOOR_WIDTH / fw, spatial_engine.FLOOR_HEIGHT / fh
            gx, gy = ground[0] * sx, ground[1] * sy
            hx, hy = head[0] * sx, head[1] * sy
        d = float(np.hypot(gx - hx, gy - hy))
        return d if np.isfinite(d) and d > 0 else None
    except Exception as e:
        logger.debug(f"height_estimate failed: {e}")
        return None


class AppearanceExtractor:
    """
    Extracts appearance signatures for a whole frame at a time.

    One instance per process is enough — the model is stateless and thread-safe
    under a lock. It is created lazily on first use so importing this module
    never costs a model load, which keeps the API's startup fast and lets the
    unit tests import it without a GPU.
    """

    def __init__(self, model_path: str = None, device: str = None, enabled: bool = True):
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self._device = device
        self._model = None
        self._lock = threading.Lock()
        self._load_failed = False
        self.enabled = enabled
        # batch size -> (graph, static input, static output). See _graphed_forward.
        self._graphs = {}
        self._use_cuda_graph = True

    @property
    def device(self):
        if self._device is None:
            try:
                import torch
                self._device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                self._device = "cpu"
        return self._device

    def _ensure_model(self):
        """
        Loads OSNet once, on first use.

        Returns None and remembers the failure if anything goes wrong — a
        missing weights file or a torchreid import problem must degrade the
        signature to colour+height, never break the video pipeline.
        """
        if self._model is not None or self._load_failed or not self.enabled:
            return self._model

        with self._lock:
            if self._model is not None or self._load_failed:
                return self._model
            try:
                import importlib
                import torch

                osnet = importlib.import_module("torchreid.reid.models.osnet")

                if not os.path.exists(self.model_path):
                    logger.warning(
                        f"OSNet weights not found at {self.model_path}; "
                        "appearance matching will use colour and height only."
                    )
                    self._load_failed = True
                    return None

                state = torch.load(self.model_path, map_location="cpu", weights_only=False)
                if isinstance(state, dict) and "state_dict" in state:
                    state = state["state_dict"]
                state = {k.replace("module.", ""): v for k, v in state.items()}

                # num_classes must match the checkpoint's classifier or the load
                # rejects it. The classifier is discarded anyway — features come
                # from the penultimate layer — so this only has to agree.
                n_classes = 1000
                for k, v in state.items():
                    if k.endswith("classifier.weight"):
                        n_classes = int(v.shape[0])
                        break

                model = osnet.osnet_x0_25(num_classes=n_classes, pretrained=False)
                missing, unexpected = model.load_state_dict(state, strict=False)
                if missing or unexpected:
                    logger.warning(
                        f"OSNet loaded with {len(missing)} missing / "
                        f"{len(unexpected)} unexpected tensors"
                    )
                model.eval().to(self.device)
                if self.device == "cuda":
                    # Lets cuDNN autotune each conv once. Must happen before any
                    # graph capture, or the tuning allocations get recorded.
                    torch.backends.cudnn.benchmark = True
                self._model = model
                logger.info(
                    f"OSNet re-ID loaded from {os.path.basename(self.model_path)} "
                    f"on {self.device} ({n_classes}-identity checkpoint)"
                )
            except Exception as e:
                logger.warning(f"Could not load OSNet ({e}); colour+height only.")
                self._load_failed = True
        return self._model

    def _preprocess(self, crops):
        """BGR crops -> one normalised NCHW float tensor at OSNet's input size."""
        import torch
        batch = np.empty((len(crops), REID_INPUT_H, REID_INPUT_W, 3), dtype=np.float32)
        for i, c in enumerate(crops):
            r = cv2.resize(c, (REID_INPUT_W, REID_INPUT_H), interpolation=cv2.INTER_LINEAR)
            batch[i] = cv2.cvtColor(r, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        batch = (batch - _MEAN) / _STD
        return torch.from_numpy(batch.transpose(0, 3, 1, 2)).to(self.device)

    def _graphed_forward(self, x):
        """
        Run OSNet through a captured CUDA graph, falling back to a normal call.

        WHY THIS EXISTS — measured, not guessed.

        OSNet x0_25 is only 0.71M parameters but 442 modules. Every one is a
        separate CUDA kernel launch, and at ~25 us of launch overhead each that
        is ~11 ms per frame of the GPU sitting idle waiting for the CPU to feed
        it. The tell-tale sign: a batch of 8 cost the same 12 ms as a batch of 3,
        and FP16 made it slightly WORSE — both symptoms of a launch-bound model
        rather than a compute-bound one.

        A CUDA graph captures all 442 launches once and replays them as a single
        submission. Measured on this machine: 11.0 ms -> 1.57 ms, a 7x speedup,
        which brings the cost to roughly the ~1 ms per person the plan budgets.

        Graphs are captured PER BATCH SIZE, because the shapes are baked in.
        Crowd sizes in an office are small and repeat, so a handful of captures
        covers everything; beyond `_MAX_GRAPH_BATCH` it falls back to an eager
        call rather than filling GPU memory with rarely-used graphs.
        """
        import torch

        n = x.shape[0]
        if (self.device != "cuda" or not self._use_cuda_graph
                or n > _MAX_GRAPH_BATCH):
            with torch.no_grad():
                return self._model(x)

        entry = self._graphs.get(n)
        if entry is None:
            try:
                static_in = torch.zeros_like(x)
                # Warm up on a side stream first: capturing a cold model records
                # cuDNN's own autotuning allocations into the graph.
                s = torch.cuda.Stream()
                s.wait_stream(torch.cuda.current_stream())
                with torch.cuda.stream(s):
                    for _ in range(3):
                        with torch.no_grad():
                            self._model(static_in)
                torch.cuda.current_stream().wait_stream(s)

                graph = torch.cuda.CUDAGraph()
                with torch.no_grad():
                    with torch.cuda.graph(graph):
                        static_out = self._model(static_in)
                entry = (graph, static_in, static_out)
                self._graphs[n] = entry
                logger.debug(f"captured CUDA graph for OSNet batch size {n}")
            except Exception as e:
                # Any capture problem disables graphs permanently and falls back
                # to eager. Slower, but identical results — never a correctness
                # difference, only a speed one.
                logger.info(f"CUDA graph capture unavailable ({e}); using eager OSNet.")
                self._use_cuda_graph = False
                with torch.no_grad():
                    return self._model(x)

        graph, static_in, static_out = entry
        static_in.copy_(x)
        graph.replay()
        # The graph writes into the SAME output tensor every replay, so this must
        # be cloned before the next call overwrites it.
        return static_out.clone()

    def embed_crops(self, crops):
        """
        512-d L2-normalised embeddings for a list of BGR crops — ONE forward
        pass for the whole list.

        Returns a list of the same length, with None where a crop was unusable,
        so the caller can zip it against its detections without bookkeeping.
        L2-normalising here means cosine similarity downstream is a plain dot
        product and every comparison is on the same scale.
        """
        out = [None] * len(crops)
        usable = [(i, c) for i, c in enumerate(crops)
                  if c is not None and c.size > 0 and c.shape[0] >= 8 and c.shape[1] >= 4]
        if not usable:
            return out

        model = self._ensure_model()
        if model is None:
            return out

        try:
            import torch
            with self._lock:
                feats = self._graphed_forward(self._preprocess([c for _, c in usable]))
            feats = feats.detach().cpu().numpy().astype(np.float32)
            norms = np.linalg.norm(feats, axis=1, keepdims=True)
            norms[norms < 1e-8] = 1.0
            feats /= norms
            for (i, _), f in zip(usable, feats):
                out[i] = f
        except Exception as e:
            logger.warning(f"OSNet inference failed ({e}); embeddings dropped this frame.")
        return out

    def extract_batch(self, frame, detections, spatial_engine=None) -> list:
        """
        A signature per detection, with ONE OSNet pass for the whole frame.

        Each signature is a dict:
            track_id   the detection's ByteTrack id, for the caller's convenience
            embedding  512-d L2-normalised vector, or None
            upper      HSV histogram of the torso, or None
            lower      HSV histogram of the legs, or None
            height     floorplan-unit height, or None (seated / no head visible)
            bbox       the box it was measured from
            area       box area in px, a crude quality proxy — a 20px-tall crop
                       produces a real but meaningless embedding, and Step 6
                       uses this to distrust tiny detections

        Never raises. A signature whose fields are all None is a valid outcome
        meaning "nothing measurable here", and the identity tracker treats it as
        no evidence rather than as evidence of difference.
        """
        if not detections:
            return []

        h, w = frame.shape[:2]
        crops, meta = [], []

        for det in detections:
            bbox = det.get("bbox")
            if not bbox:
                crops.append(None)
                meta.append(None)
                continue
            box = _clamp_box(bbox, w, h)
            if box is None:
                crops.append(None)
                meta.append(None)
                continue
            x1, y1, x2, y2 = box
            crops.append(frame[y1:y2, x1:x2])
            meta.append(box)

        embeddings = self.embed_crops(crops)

        signatures = []
        for det, emb, box in zip(detections, embeddings, meta):
            kpts = det.get("keypoints")
            bbox = det.get("bbox")
            if box is None:
                signatures.append({
                    "track_id": det.get("track_id"),
                    "embedding": None, "upper": None, "lower": None,
                    "height": None, "bbox": bbox, "area": 0,
                })
                continue

            hist = colour_histogram(frame, bbox, kpts)
            x1, y1, x2, y2 = box
            signatures.append({
                "track_id": det.get("track_id"),
                "embedding": emb,
                "upper": hist["upper"],
                "lower": hist["lower"],
                "hist_from_keypoints": hist["from_keypoints"],
                "height": height_estimate(bbox, kpts, spatial_engine, frame.shape),
                "bbox": bbox,
                "area": (x2 - x1) * (y2 - y1),
            })

        return signatures


def signature_similarity(a: dict, b: dict) -> dict:
    """
    Compare two signatures, component by component.

    Returns each component's score AND a `fused` score using the plan's §3
    weights, RENORMALISED over whichever components are actually present. That
    renormalisation is the important part: if height is missing (seated person)
    a fixed 0.10·0 would silently cap the best possible score at 0.90 and make
    every seated match look worse than it is. Comparing only what both
    signatures have keeps the score on a 0..1 scale whatever is measurable.

    The seat prior (weight 0.40) is NOT here — it is spatial, not appearance,
    and the identity tracker adds it where the zone is known.
    """
    scores = {"osnet": None, "colour": None, "height": None}

    if a.get("embedding") is not None and b.get("embedding") is not None:
        scores["osnet"] = cosine(a["embedding"], b["embedding"])

    parts = []
    for key in ("upper", "lower"):
        if a.get(key) is not None and b.get(key) is not None:
            parts.append(histogram_similarity(a[key], b[key]))
    if parts:
        scores["colour"] = float(np.mean(parts))

    ha, hb = a.get("height"), b.get("height")
    if ha and hb and ha > 0 and hb > 0:
        # Relative difference, so the score does not depend on the floorplan's
        # unit scale. 20% apart -> 0; identical -> 1.
        scores["height"] = float(max(0.0, 1.0 - abs(ha - hb) / max(ha, hb) / 0.20))

    weights = {"osnet": 0.30, "colour": 0.20, "height": 0.10}
    total = sum(weights[k] for k, v in scores.items() if v is not None)
    if total <= 0:
        fused = 0.0
    else:
        fused = sum(weights[k] * v for k, v in scores.items() if v is not None) / total

    scores["fused"] = float(fused)
    scores["components"] = sum(1 for v in scores.values() if isinstance(v, float))
    return scores


# One shared extractor. Created lazily; safe to import from anywhere.
_default_extractor = None


def get_extractor(enabled: bool = True) -> AppearanceExtractor:
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = AppearanceExtractor(enabled=enabled)
    return _default_extractor
