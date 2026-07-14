import os

from fastembed import TextEmbedding
from numpy import isfinite
from numpy.linalg import norm

# huggingface-hub 1.x defaults to the xet CAS backend, which returns 401 on
# anonymous downloads of public models. Force the classic HF CDN so fresh model
# pulls work in CI and on clean installs. Override with HF_HUB_DISABLE_XET=0
# (e.g. alongside an authenticated HF_TOKEN) if xet is ever wanted back.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

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
