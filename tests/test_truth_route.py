from starlette.testclient import TestClient

from utah import proof_skeleton
from utah.interface.web import app


def test_api_truth_returns_skeleton_grouped():
    proof_skeleton.seed_skeleton()
    c = TestClient(app)
    r = c.get("/api/truth")
    assert r.status_code == 200
    data = r.json()
    assert data["scoreboard"]["total"] >= 26
    assert "growth" in data["by_system"]
    row = data["by_system"]["growth"][0]
    assert {"id", "claim", "tier", "artifact"} <= set(row)


def test_api_truth_detail_pulls_real_source():
    proof_skeleton.seed_skeleton()
    c = TestClient(app)
    r = c.get("/api/truth/infra.deck.honest")
    assert r.status_code == 200
    assert "web.py" in r.json()["artifact"]
    assert r.json()["source"]            # the real file content, not an md
