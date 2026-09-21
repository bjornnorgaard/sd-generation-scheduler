from __future__ import annotations

import unittest

from tests.helpers import EXTENSION_ROOT  # noqa: F401

from lib_generation_scheduler import summary

T2I_IDS = [
    None, "txt2img_prompt", "txt2img_neg_prompt", None, "txt2img_batch_count",
    "txt2img_batch_size", "txt2img_cfg_scale", None, "txt2img_height", "txt2img_width",
    "txt2img_hr", "txt2img_sampling", "txt2img_scheduler", "txt2img_steps", "txt2img_seed",
]


class ResolveRolesTests(unittest.TestCase):
    def test_finds_positions_by_elem_id(self):
        roles = summary.resolve_roles("txt2img", T2I_IDS)
        self.assertEqual(roles["prompt"], 1)
        self.assertEqual(roles["width"], 9)
        self.assertEqual(roles["seed"], 14)
        self.assertEqual(roles["hires"], 10)
        self.assertNotIn("denoising_strength", roles)

    def test_tab_scoped_ids_do_not_leak(self):
        roles = summary.resolve_roles("img2img", T2I_IDS)
        self.assertEqual(roles, {})

    def test_img2img_denoising_only_on_img2img(self):
        ids = ["img2img_denoising_strength", "img2img_hr"]
        self.assertEqual(summary.resolve_roles("img2img", ids), {"denoising_strength": 0})

    def test_first_match_wins(self):
        self.assertEqual(summary.resolve_roles("txt2img", ["txt2img_prompt", "txt2img_prompt"]),
                         {"prompt": 0})


class BuildSummaryTests(unittest.TestCase):
    def setUp(self):
        self.roles = summary.resolve_roles("txt2img", T2I_IDS)
        self.args = ["", "a cat", "blurry", [], 2, 1, 6.5, 3.0, 1216, 832, False,
                     "Euler", "Simple", 24, -1]

    def test_extracts_fields(self):
        result = summary.build_summary("txt2img", self.args, self.roles)
        self.assertEqual(result["tab"], "txt2img")
        self.assertEqual(result["prompt"], "a cat")
        self.assertEqual(result["negative_prompt"], "blurry")
        self.assertEqual((result["width"], result["height"]), (832, 1216))
        self.assertEqual(result["steps"], 24)
        self.assertEqual(result["sampler"], "Euler")
        self.assertEqual(result["seed"], -1)
        self.assertEqual(result["batch_count"], 2)
        self.assertEqual(result["hires"], False)

    def test_long_prompt_clipped(self):
        args = list(self.args)
        args[1] = "x" * 1000
        result = summary.build_summary("txt2img", args, self.roles)
        self.assertEqual(len(result["prompt"]), summary.PROMPT_LIMIT)
        self.assertTrue(result["prompt"].endswith("…"))

    def test_missing_roles_and_short_args_are_tolerated(self):
        self.assertEqual(summary.build_summary("txt2img", ["", "p"], self.roles),
                         {"tab": "txt2img", "prompt": "p"})
        self.assertEqual(summary.build_summary("txt2img", self.args, {}), {"tab": "txt2img"})

    def test_non_scalar_values_stringified(self):
        args = list(self.args)
        args[13] = ["weird"]
        result = summary.build_summary("txt2img", args, self.roles)
        self.assertEqual(result["steps"], "['weird']")

    def test_empty_values_omitted(self):
        args = list(self.args)
        args[12] = ""
        self.assertNotIn("scheduler", summary.build_summary("txt2img", args, self.roles))


if __name__ == "__main__":
    unittest.main()
