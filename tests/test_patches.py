from __future__ import annotations

import unittest

from docdrift_core import Claim, ClaimType
from docdrift_core.patches import (
    DocumentationPatch,
    PatchLimits,
    PatchValidationError,
    create_claim_patch,
    create_span_patch,
    create_unique_text_patch,
)


class DocumentationPatchTests(unittest.TestCase):
    def test_exact_replacement_preserves_everything_outside_the_span(self) -> None:
        original = "Before `wrong` after.\nSecond line.\n"
        start = original.index("wrong")
        patch = create_span_patch(
            original,
            start_offset=start,
            end_offset=start + len("wrong"),
            expected_original="wrong",
            replacement_text="right",
        )

        self.assertIsInstance(patch, DocumentationPatch)
        self.assertEqual(patch.original_text, original)
        self.assertEqual(patch.proposed_text, "Before `right` after.\nSecond line.\n")
        self.assertEqual(patch.start_offset, start)
        self.assertEqual(patch.end_offset, start + len("wrong"))
        self.assertEqual(patch.changed_characters, len("wrong"))
        self.assertEqual(patch.changed_lines, 1)

    def test_replacements_at_document_boundaries_and_empty_text(self) -> None:
        original = "middle"
        beginning = create_span_patch(
            original,
            start_offset=0,
            end_offset=0,
            expected_original="",
            replacement_text="start ",
        )
        ending = create_span_patch(
            original,
            start_offset=len(original),
            end_offset=len(original),
            expected_original="",
            replacement_text=" end",
        )
        deletion = create_span_patch(
            original,
            start_offset=0,
            end_offset=len(original),
            expected_original=original,
            replacement_text="",
        )

        self.assertEqual(beginning.proposed_text, "start middle")
        self.assertEqual(ending.proposed_text, "middle end")
        self.assertEqual(deletion.proposed_text, "")
        self.assertEqual(deletion.changed_characters, len(original))

    def test_invalid_offsets_and_expected_text_mismatch_are_rejected(self) -> None:
        original = "unchanged text"
        invalid_targets = (
            (-1, 2, "un"),
            (4, 2, ""),
            (0, len(original) + 1, "unchanged text"),
            (True, 1, "u"),
            (0, 0, "not empty"),
        )
        for start, end, expected in invalid_targets:
            with self.subTest(start=start, end=end, expected=expected):
                with self.assertRaises(PatchValidationError):
                    create_span_patch(
                        original,
                        start_offset=start,
                        end_offset=end,
                        expected_original=expected,
                        replacement_text="replacement",
                    )
                self.assertEqual(original, "unchanged text")

    def test_text_lookup_rejects_stale_or_ambiguous_targets(self) -> None:
        original = "same phrase and same phrase"
        with self.assertRaisesRegex(PatchValidationError, "ambiguous"):
            create_unique_text_patch(
                original, expected_original="same phrase", replacement_text="new phrase"
            )
        with self.assertRaisesRegex(PatchValidationError, "stale"):
            create_unique_text_patch(
                original, expected_original="missing", replacement_text="new"
            )
        with self.assertRaisesRegex(PatchValidationError, "ambiguous"):
            create_unique_text_patch("aaa", expected_original="aa", replacement_text="x")
        self.assertEqual(original, "same phrase and same phrase")

    def test_character_and_line_limits_are_enforced(self) -> None:
        with self.assertRaisesRegex(PatchValidationError, "character"):
            create_span_patch(
                "abc",
                start_offset=0,
                end_offset=3,
                expected_original="abc",
                replacement_text="1234",
                limits=PatchLimits(max_changed_characters=3),
            )
        with self.assertRaisesRegex(PatchValidationError, "line"):
            create_span_patch(
                "one\ntwo",
                start_offset=0,
                end_offset=len("one\ntwo"),
                expected_original="one\ntwo",
                replacement_text="one\ntwo\nthree",
                limits=PatchLimits(max_changed_lines=2),
            )
        for maximum_characters, maximum_lines in ((0, 1), (1, 0)):
            with (
                self.subTest(characters=maximum_characters, lines=maximum_lines),
                self.assertRaises(ValueError),
            ):
                PatchLimits(maximum_characters, maximum_lines)

    def test_claim_line_and_line_local_span_are_translated_without_newline_changes(self) -> None:
        original = "heading\r\nDocs BAD here\r\nfooter\r\n"
        claim = Claim(
            "claim-1",
            ClaimType.DEFAULT,
            "setting",
            {},
            "Docs BAD here",
            "docs/guide.md",
            2,
            5,
            8,
        )
        patch = create_claim_patch(
            original,
            claim,
            expected_original="BAD",
            replacement_text="good",
        )

        self.assertEqual(patch.proposed_text, "heading\r\nDocs good here\r\nfooter\r\n")
        self.assertEqual(patch.original_text[: patch.start_offset], "heading\r\nDocs ")
        self.assertTrue(patch.proposed_text.endswith("\r\n"))

    def test_claim_patch_rejects_stale_line_or_span(self) -> None:
        claim = Claim(
            "claim-1", ClaimType.DEFAULT, "setting", {}, "Docs BAD here", "docs/guide.md", 1, 5, 8
        )
        for document in ("Docs OLD here", "Docs BAD changed"):
            with (
                self.subTest(document=document),
                self.assertRaises(PatchValidationError),
            ):
                create_claim_patch(
                    document,
                    claim,
                    expected_original="BAD",
                    replacement_text="good",
                )


if __name__ == "__main__":
    unittest.main()
