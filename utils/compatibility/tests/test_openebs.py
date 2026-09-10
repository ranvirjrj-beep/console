"""Offline regressions for the OpenEBS compatibility scraper."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

COMPATIBILITY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPATIBILITY))
spec = importlib.util.spec_from_file_location(
    "openebs_scraper", COMPATIBILITY / "scrapers/openebs.py"
)
scraper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scraper)


INDEX = b"""
apiVersion: v1
entries:
  openebs:
    - appVersion: 4.7.0-develop
      version: 4.7.0-develop
    - appVersion: 4.6.1-prerelease
      version: 4.6.1-prerelease
    - appVersion: 4.6.0
      version: 4.6.0
    - appVersion: 4.5.1
      version: 4.5.1
    - appVersion: 4.5.0
      version: 4.5.0
    - appVersion: 4.4.0
      version: 4.4.0
    - appVersion: 3.10.0
      version: 3.10.0
"""

DOCS = {
    "https://openebs.io/docs/4.6.x/quickstart-guide/installation": (
        b"<html>No version marker</html>"
    ),
    "https://openebs.io/docs/main/quickstart-guide/prerequisites": (
        b"<li>Kubernetes 1.23 or higher is required</li>"
    ),
    "https://openebs.io/docs/4.5.x/quickstart-guide/installation": (
        b"<li>Kubernetes 1.23 or higher is required</li>"
    ),
    "https://openebs.io/docs/4.4.x/quickstart-guide/installation": (
        b"<li>Kubernetes 1.23 or higher is required</li>"
    ),
}


class OpenEBSTests(unittest.TestCase):
    def test_parses_published_kubernetes_requirement(self):
        examples = (
            (b"Kubernetes 1.23 or higher is required", "1.23"),
            (b"Kubernetes version v1.24+", "1.24"),
        )
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(scraper.parse_min_kube_version(text), expected)

    def test_chart_index_selects_latest_stable_patch_per_minor(self):
        self.assertEqual(
            scraper.parse_chart_index(INDEX),
            [
                {"app_version": "4.6.0", "chart_version": "4.6.0"},
                {"app_version": "4.5.1", "chart_version": "4.5.1"},
                {"app_version": "4.4.0", "chart_version": "4.4.0"},
            ],
        )

    def test_prerelease_develop_and_v3_entries_are_ignored(self):
        versions = [row["app_version"] for row in scraper.parse_chart_index(INDEX)]
        self.assertNotIn("4.7.0", versions)
        self.assertNotIn("4.6.1", versions)
        self.assertNotIn("3.10.0", versions)

    def test_latest_minor_can_fall_back_to_current_docs(self):
        def fetch(url):
            if url == f"{scraper.HELM_REPO_URL}/index.yaml":
                return INDEX
            return DOCS.get(url)

        with patch.object(scraper, "fetch_page", side_effect=fetch), patch.object(
            scraper, "current_kube_version", return_value="1.36"
        ), patch.object(scraper, "update_compatibility_info") as update:
            scraper.scrape()

        update.assert_called_once()
        rows = update.call_args.args[1]
        self.assertEqual(
            [row["version"] for row in rows], ["4.6.0", "4.5.1", "4.4.0"]
        )
        self.assertEqual(rows[0]["kube"][0], "1.23")
        self.assertEqual(rows[0]["kube"][-1], "1.36")
        self.assertEqual(rows[0]["chart_version"], "4.6.0")

    def test_missing_historical_docs_do_not_inherit_current_requirement(self):
        docs = dict(DOCS)
        docs["https://openebs.io/docs/4.5.x/quickstart-guide/installation"] = b"missing"

        def fetch(url):
            if url == f"{scraper.HELM_REPO_URL}/index.yaml":
                return INDEX
            return docs.get(url)

        with patch.object(scraper, "fetch_page", side_effect=fetch), patch.object(
            scraper, "current_kube_version", return_value="1.36"
        ), patch.object(scraper, "print_error"), patch.object(
            scraper, "update_compatibility_info"
        ) as update:
            scraper.scrape()

        rows = update.call_args.args[1]
        self.assertEqual([row["version"] for row in rows], ["4.6.0", "4.4.0"])

    def test_malformed_or_missing_chart_index_never_writes(self):
        for index in (None, b"not: [valid", b"entries: {}"):
            with self.subTest(index=index), patch.object(
                scraper, "fetch_page", return_value=index
            ), patch.object(scraper, "print_error"), patch.object(
                scraper, "update_compatibility_info"
            ) as update:
                scraper.scrape()
                update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
