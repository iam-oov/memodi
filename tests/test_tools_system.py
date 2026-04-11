from memodi.tools.system import ping


def test_ping():
    assert ping() == "pong"
