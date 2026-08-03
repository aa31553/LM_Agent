from fastapi.routing import Mount
from fastapi.testclient import TestClient

from app.main import create_app


def test_console_static_application_is_mounted() -> None:
    app = create_app()
    console = next(
        route for route in app.routes if isinstance(route, Mount) and route.path == "/console"
    )
    assert console.name == "console"
    client = TestClient(app)
    redirect = client.get("/", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/console/"
    assert client.get("/console/").status_code == 200
    assert client.get("/console/dist/chart-runtime.js").status_code == 200
    assert client.get("/console/vendor/echarts.min.js").status_code == 200
