from scripts.probe_kap_bulk_download_route import (
    extract_candidate_routes,
    extract_interesting_snippets,
    extract_script_urls,
)


def test_extract_script_urls_resolves_relative_and_deduplicates() -> None:
    html = """
    <html><head>
      <script src="/_next/static/chunks/a.js"></script>
      <script src="https://kap.org.tr/_next/static/chunks/b.js"></script>
      <script src="/_next/static/chunks/a.js"></script>
    </head></html>
    """
    assert extract_script_urls(html, base_url="https://kap.org.tr/tr") == (
        "https://kap.org.tr/_next/static/chunks/a.js",
        "https://kap.org.tr/_next/static/chunks/b.js",
    )


def test_extract_candidate_routes_finds_relative_and_escaped_kap_download_paths() -> None:
    script = r'''
      const a="/tr/api/financial-report/download?year=2025&period=Y";
      const b="https:\/\/kap.org.tr\/tr\/api\/file\/download\/4028abc";
      const ignored="/tr/api/company/list";
    '''
    assert extract_candidate_routes(script) == (
        "/tr/api/financial-report/download?year=2025&period=Y",
        "https://kap.org.tr/tr/api/file/download/4028abc",
    )


def test_extract_interesting_snippets_is_bounded_and_token_scoped() -> None:
    text = "x" * 250 + "download financial period year report" + "y" * 250
    snippets = extract_interesting_snippets(text, max_per_token=1, radius=20)
    assert set(snippets) >= {"financial", "download", "period", "year", "report"}
    assert all(len(rows) == 1 for rows in snippets.values())
    assert all(len(rows[0]) < 100 for rows in snippets.values())
