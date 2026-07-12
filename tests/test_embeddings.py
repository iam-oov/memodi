import pytest
from numpy import dot, zeros
from numpy.linalg import norm

from memodi import embeddings
from memodi.embeddings import EMBEDDING_DIM, generate_embedding


def test_generate_embedding():
    embedding = generate_embedding("test sentence")
    assert len(embedding) == EMBEDDING_DIM
    assert all(isinstance(x, float) for x in embedding)
    assert norm(embedding) == pytest.approx(1.0, abs=1e-3)


def test_embedding_similarity():
    e1 = generate_embedding("authentication with JWT tokens")
    e2 = generate_embedding("user login and session management")
    e3 = generate_embedding("database indexing strategies")
    # e1 and e2 should be more similar than e1 and e3
    sim_12 = dot(e1, e2)
    sim_13 = dot(e1, e3)
    assert sim_12 > sim_13


def test_embedding_cross_lingual_similarity():
    e1 = generate_embedding("arreglé el bug de conexión")
    e2 = generate_embedding("fixed the connection bug")
    e3 = generate_embedding("database indexing strategies")
    sim_es_en = dot(e1, e2)
    sim_unrelated = dot(e1, e3)
    assert sim_es_en > sim_unrelated


def test_generate_embedding_rejects_zero_vector(monkeypatch):
    class ZeroModel:
        def embed(self, texts):
            yield zeros(EMBEDDING_DIM)

    monkeypatch.setattr(embeddings, "get_model", lambda: ZeroModel())
    with pytest.raises(ValueError):
        generate_embedding("anything")
