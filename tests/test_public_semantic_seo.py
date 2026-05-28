from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse


PUBLIC_PAGES = (
    "/",
    "/offre",
    "/deploiement",
    "/comment-ca-marche",
    "/securite",
    "/professionnels",
    "/pour-les-structures",
)


def _extract_json_ld_blocks(html: str) -> list[dict]:
    blocks = re.findall(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [json.loads(block) for block in blocks]


def _extract_meta_content(html: str, attr_name: str, attr_value: str) -> str:
    pattern = (
        rf'<meta[^>]+{attr_name}="{re.escape(attr_value)}"[^>]+content="([^"]+)"'
    )
    match = re.search(pattern, html, flags=re.IGNORECASE)
    assert match, f"Missing meta {attr_name}={attr_value}"
    return match.group(1)


def test_public_pages_have_semantic_seo_basics(client, app):
    for path in PUBLIC_PAGES:
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)

        assert html.count('rel="canonical"') == 1, path
        assert html.count("<h1") == 1, path
        assert 'property="og:url"' in html, path
        assert 'name="twitter:card"' in html, path

        canonical_match = re.search(
            r'<link rel="canonical" href="([^"]+)"',
            html,
            flags=re.IGNORECASE,
        )
        assert canonical_match, path
        canonical_href = canonical_match.group(1)
        assert canonical_href.startswith("http"), path

        og_image = _extract_meta_content(html, "property", "og:image")
        og_path = urlparse(og_image).path
        assert og_path.startswith("/static/"), path
        assert app.static_folder is not None
        local_og_path = Path(app.static_folder) / og_path.replace("/static/", "", 1)
        assert local_og_path.is_file(), path

        json_ld_blocks = _extract_json_ld_blocks(html)
        json_ld_types = {block.get("@type") for block in json_ld_blocks}
        assert {
            "Organization",
            "WebSite",
            "SoftwareApplication",
            "BreadcrumbList",
        }.issubset(json_ld_types), path

        for block in json_ld_blocks:
            assert "@context" in block


def test_inner_public_pages_expose_breadcrumb_semantics(client):
    for path in PUBLIC_PAGES[1:]:
        response = client.get(path)
        html = response.get_data(as_text=True)
        assert 'aria-label="Fil d' in html, path

        breadcrumb = next(
            block
            for block in _extract_json_ld_blocks(html)
            if block.get("@type") == "BreadcrumbList"
        )
        items = breadcrumb.get("itemListElement") or []
        assert len(items) >= 2, path


def test_homepage_reinforces_operational_keywords(client):
    response = client.get("/")
    html = response.get_data(as_text=True).lower()

    for keyword in (
        "coordination opérationnelle",
        "continuité inter-structures",
        "pilotage",
        "orientation",
        "réseaux partenaires",
    ):
        assert keyword in html


def test_robots_and_sitemap_public_routes(client):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Sitemap: https://helpchain.live/sitemap.xml" in robots.get_data(as_text=True)

    sitemap = client.get("/sitemap.xml")
    sitemap_text = sitemap.get_data(as_text=True)
    assert sitemap.status_code == 200
    assert "<urlset" in sitemap_text
    for path in (
        "https://helpchain.live/",
        "https://helpchain.live/offre",
        "https://helpchain.live/deploiement",
        "https://helpchain.live/comment-ca-marche",
        "https://helpchain.live/securite",
        "https://helpchain.live/professionnels",
    ):
        assert path in sitemap_text
