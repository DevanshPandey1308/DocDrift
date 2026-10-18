from __future__ import annotations

import unittest
from textwrap import dedent

from docdrift_worker.checkout import parse_added_markdown_lines


class UnifiedDiffPositionTests(unittest.TestCase):
    def test_maps_only_added_lines_across_context_deletions_and_multiple_hunks(self) -> None:
        diff = dedent(
            """\
            diff --git a/docs/guide.md b/docs/guide.md
            index 1111111..2222222 100644
            --- a/docs/guide.md
            +++ b/docs/guide.md
            @@ -1,2 +1,3 @@
             context line
            -old claim
            +updated claim
            +additional claim
            @@ -10 +11,2 @@
            -deleted text
            +new section
            +new detail
            """
        )

        self.assertEqual(
            parse_added_markdown_lines(diff),
            {"docs/guide.md": frozenset({2, 3, 11, 12})},
        )

    def test_deleted_document_and_context_only_lines_do_not_produce_anchors(self) -> None:
        diff = dedent(
            """\
            diff --git a/docs/removed.md b/docs/removed.md
            deleted file mode 100644
            --- a/docs/removed.md
            +++ /dev/null
            @@ -1 +0,0 @@
            -old claim
            """
        )
        self.assertEqual(parse_added_markdown_lines(diff), {})

    def test_malformed_or_unsafe_diff_is_unavailable(self) -> None:
        malformed = dedent(
            """\
            diff --git a/docs/guide.md b/docs/guide.md
            --- a/docs/guide.md
            +++ b/docs/guide.md
            @@ -1,2 +1,2 @@
            +not enough lines
            """
        )
        unsafe = dedent(
            """\
            diff --git a/../outside.md b/../outside.md
            --- a/../outside.md
            +++ b/../outside.md
            @@ -1 +1 @@
            -old
            +new
            """
        )
        self.assertIsNone(parse_added_markdown_lines(malformed))
        self.assertIsNone(parse_added_markdown_lines(unsafe))
        self.assertIsNone(parse_added_markdown_lines("+not a patch"))

    def test_patch_content_that_looks_like_a_file_header_stays_an_added_line(self) -> None:
        diff = """\
        diff --git a/docs/guide.md b/docs/guide.md
        --- a/docs/guide.md
        +++ b/docs/guide.md
        @@ -1 +1 @@
        -old
        +++ b/this-is-document-content
        """
        self.assertEqual(
            parse_added_markdown_lines(dedent(diff)),
            {"docs/guide.md": frozenset({1})},
        )

    def test_renamed_markdown_file_maps_new_path_using_no_rename_patch(self) -> None:
        no_rename_patch = dedent(
            """\
            diff --git a/docs/new.md b/docs/new.md
            new file mode 100644
            --- /dev/null
            +++ b/docs/new.md
            @@ -0,0 +1,2 @@
            +# Heading
            +Updated claim.
            diff --git a/docs/old.md b/docs/old.md
            deleted file mode 100644
            --- a/docs/old.md
            +++ /dev/null
            @@ -1,2 +0,0 @@
            -# Heading
            -The claim is here.
            """
        )

        self.assertEqual(
            parse_added_markdown_lines(no_rename_patch),
            {"docs/new.md": frozenset({1, 2})},
        )


if __name__ == "__main__":
    unittest.main()
