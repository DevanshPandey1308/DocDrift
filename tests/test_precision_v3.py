from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docdrift_core import analyze, extract_markdown
from docdrift_core.models import Verdict


class PrecisionV3PathAnchoringTests(unittest.TestCase):
    def test_missing_paths_require_repository_anchoring_and_moves_are_specific(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            root = Path(temporary)
            (root / "docs/tutorials").mkdir(parents=True)
            (root / "src/legacy").mkdir(parents=True)
            (root / "docs_src").mkdir()
            (root / "src/version.py").write_text("VERSION = '1'\n", encoding="utf-8")
            (root / "src/legacy/io.py").write_text("VALUE = 1\n", encoding="utf-8")

            documents = {
                "README.md": """The example is implemented in `src/old_module.py`.
The example is implemented in `src/core/io.py`.
The example is implemented in `hello.py`.
The configuration is in `gunicorn.conf.py`.
The module is named `version.py`.
The version module is `docs_src/x/version.py`.
A CLI application has `main.py`.
The package has `absent_root/missing.py`.
The rendered page is `site/index.html`.
Use image `ghcr.io/acme/service:1.2.3`.
The old implementation is `src/old_module.py`.
To begin, create a `foo/bar.py`.
The implementation lives in `src/another_missing.py`; run the generator before release.
""",
                "docs/tutorials/setup.md": "The example is implemented in `src/io.py`.\n",
                "CHANGELOG.md": "The example is implemented in `src/absent.py`.\n",
                "docs/tutorial/main.md": "The example is implemented in `main.py`.\n",
            }
            for name, text in documents.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")

            findings, _, claims = analyze(root, (".",), (".",))
            by_claim = {(item.claim.document_path, item.claim.subject): item for item in findings}
            expected = {
                ("README.md", "src/old_module.py"): Verdict.FALSE,
                ("README.md", "src/core/io.py"): Verdict.FALSE,
                ("README.md", "hello.py"): Verdict.UNVERIFIABLE,
                ("README.md", "gunicorn.conf.py"): Verdict.UNVERIFIABLE,
                ("README.md", "version.py"): Verdict.UNVERIFIABLE,
                ("README.md", "docs_src/x/version.py"): Verdict.UNVERIFIABLE,
                ("README.md", "main.py"): Verdict.UNVERIFIABLE,
                ("README.md", "absent_root/missing.py"): Verdict.UNVERIFIABLE,
                ("README.md", "site/index.html"): Verdict.UNVERIFIABLE,
                ("README.md", "ghcr.io/acme/service:1.2.3"): Verdict.UNVERIFIABLE,
                ("README.md", "src/another_missing.py"): Verdict.FALSE,
                ("README.md", "foo/bar.py"): Verdict.UNVERIFIABLE,
                ("docs/tutorials/setup.md", "src/io.py"): Verdict.UNVERIFIABLE,
                ("CHANGELOG.md", "src/absent.py"): Verdict.UNVERIFIABLE,
                ("docs/tutorial/main.md", "main.py"): Verdict.UNVERIFIABLE,
            }
            for key, verdict in expected.items():
                with self.subTest(claim=key):
                    self.assertEqual(by_claim[key].verdict, verdict)
            moved = by_claim[("README.md", "src/core/io.py")]
            self.assertEqual(moved.evidence[0].path, "src/legacy/io.py")

            extracted_ids = {
                claim.id
                for relative, text in documents.items()
                for claim in extract_markdown(text, relative, root)
            }
            self.assertEqual({claim.id for claim in claims}, extracted_ids)


if __name__ == "__main__":
    unittest.main()
