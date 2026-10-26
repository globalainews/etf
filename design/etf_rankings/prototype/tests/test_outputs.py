from __future__ import annotations

import unittest
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]


class StillOutputTests(unittest.TestCase):
    def test_formal_stills_are_vertical_full_hd(self) -> None:
        for name in (
            "daily_ranking.png",
            "weekly_ranking.png",
            "top_etf_analysis.png",
            "price_share_divergence.png",
        ):
            with self.subTest(name=name), Image.open(ROOT / name) as image:
                self.assertEqual(image.size, (1080, 1920))

    def test_overview_is_four_uncropped_panels(self) -> None:
        with Image.open(ROOT / "design_overview.png") as image:
            self.assertEqual(image.size, (4320, 1920))

    def test_direction_comparison_has_three_panels(self) -> None:
        with Image.open(ROOT / "direction_comparison.png") as image:
            self.assertEqual(image.size, (3240, 1920))

    def test_video_contract(self) -> None:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            self.skipTest("ffprobe unavailable")
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_name,codec_type,width,height,r_frame_rate,pix_fmt,sample_rate,channels", "-of", "json", str(ROOT / "etf_ranking_demo.mp4")],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(result.stdout)
        video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
        audio = next(stream for stream in payload["streams"] if stream["codec_type"] == "audio")
        self.assertEqual((video["width"], video["height"]), (1080, 1920))
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual(video["pix_fmt"], "yuv420p")
        self.assertEqual(video["r_frame_rate"], "30/1")
        self.assertEqual(audio["codec_name"], "aac")
        self.assertEqual(audio["sample_rate"], "48000")
        self.assertAlmostEqual(float(payload["format"]["duration"]), 25.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
