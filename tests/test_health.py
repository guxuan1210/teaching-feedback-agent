from fastapi.testclient import TestClient
from app.main import create_app


def test_health_page_reports_ready():
    client = TestClient(create_app(database_url="sqlite+pysqlite:///:memory:"))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
