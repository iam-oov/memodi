import os

# huggingface-hub 1.x reads HF_HUB_DISABLE_XET into a module-level constant at
# IMPORT time, and its default xet CAS backend returns 401 on anonymous
# downloads of public models. This must be set before fastembed imports
# huggingface_hub, so the imports below are deliberately not at the top.
# Override with HF_HUB_DISABLE_XET=0 (e.g. with an authenticated HF_TOKEN).
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from fastembed import TextEmbedding
from numpy import isfinite
from numpy.linalg import norm

_model = None
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384


def get_model() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding(MODEL_NAME)
    return _model


def generate_embedding(text: str) -> list[float]:
    model = get_model()
    embedding = next(model.embed([text]))
    magnitude = norm(embedding)
    if not (isfinite(magnitude) and magnitude > 0):
        raise ValueError(f"cannot normalize embedding with L2 norm {magnitude}")
    normalized = embedding / magnitude
    return normalized.tolist()
