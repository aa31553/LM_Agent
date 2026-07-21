from fastapi.testclient import TestClient

from app.main import create_app


def test_document_formats_endpoint_is_the_frontend_contract() -> None:
    client = TestClient(create_app())
    response = client.get(
        "/api/v1/documents/formats",
        headers={"Authorization": "Bearer admin"},
    )

    assert response.status_code == 200
    body = response.json()
    extensions = {item["extension"] for item in body["items"]}
    expected = {".pdf", ".docx", ".webp", ".md", ".csv", ".json", ".yaml", ".html", ".xml"}
    assert expected.issubset(extensions)
    assert set(body["accept"].split(",")) == extensions
