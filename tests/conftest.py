import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database  # noqa: E402
from app import create_app  # noqa: E402

SAMPLES = ROOT / "sample_uploads"


@pytest.fixture()
def app(tmp_path):
    return create_app(tmp_path / "test.db")


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def conn(tmp_path):
    c = database.connect(tmp_path / "unit.db")
    database.init_db(c)
    yield c
    c.close()


def sample(rel):
    p = SAMPLES / rel
    return p.name, p.read_bytes()


@pytest.fixture()
def upload(client):
    """upload('01_baseline/hdfc_returns_20260910.csv', ...) -> list of result dicts"""
    def _upload(*rels):
        files = [(__import__("io").BytesIO(sample(r)[1]), sample(r)[0]) for r in rels]
        resp = client.post("/api/uploads", data={"files": files}, content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return resp.get_json()["results"]
    return _upload
