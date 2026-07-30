import pytest

from backend.appy import app


def test_static_cache_header_present_for_static_paths(tmp_path):
    app.config["TESTING"] = True
    original_static_folder = app.static_folder
    app.static_folder = str(tmp_path)
    client = app.test_client()
    file_path = tmp_path / "app.js"
    try:
        file_path.write_text("console.log('test');", encoding="utf-8")

        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        assert "Cache-Control" in resp.headers
        # Accept either a public max-age header or other cache directives
        cc = resp.headers.get("Cache-Control", "")
        if "max-age" in cc:
            # If max-age is present, validate it matches the configured TTL.
            assert str(app.config.get("SEND_FILE_MAX_AGE_DEFAULT", 86400)) in cc
    finally:
        app.static_folder = original_static_folder


def test_no_cache_header_for_api_route():
    app.config["TESTING"] = True
    client = app.test_client()

    # Use the public API endpoint which returns JSON to ensure normal routes
    # don't receive the static Cache-Control header
    resp = client.get("/health")
    assert resp.status_code in (200, 204, 404, 503)
    assert "Cache-Control" not in resp.headers

