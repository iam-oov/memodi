from memodi.embeddings import EMBEDDING_DIM, generate_embedding


def test_generate_embedding():
    embedding = generate_embedding("test sentence")
    assert len(embedding) == EMBEDDING_DIM
    assert all(isinstance(x, float) for x in embedding)


def test_embedding_similarity():
    e1 = generate_embedding("authentication with JWT tokens")
    e2 = generate_embedding("user login and session management")
    e3 = generate_embedding("database indexing strategies")
    # e1 and e2 should be more similar than e1 and e3
    from numpy import dot

    sim_12 = dot(e1, e2)
    sim_13 = dot(e1, e3)
    assert sim_12 > sim_13
