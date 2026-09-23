from pathlib import Path


def test_vercel_entrypoint_exports_fastapi_application():
    source = Path("api/index.py").read_text(encoding="utf-8")
    assert "from admin.main import app" in source
    assert '__all__ = ["app"]' in source


def test_vercel_config_routes_all_requests_to_fastapi():
    source = Path("vercel.json").read_text(encoding="utf-8")
    assert '"api/index.py"' in source
    assert '"source": "/(.*)"' in source
    assert '"destination": "/api/index.py"' in source
    assert '"maxDuration": 300' in source
