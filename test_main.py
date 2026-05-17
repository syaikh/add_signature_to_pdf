import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from PIL import Image

sys.modules.setdefault("fitz", MagicMock())
import main


class SmokeTestMain(unittest.TestCase):
    def test_get_image_aspect_ratio_and_transparency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / "sig.png"
            output_path = Path(tmpdir) / "sig_transparent.png"

            image = Image.new("RGB", (120, 80), color=(255, 255, 255))
            for x in range(20, 100):
                for y in range(20, 60):
                    image.putpixel((x, y), (0, 0, 0))
            image.save(source_path, format="PNG")

            aspect_ratio = main._get_image_aspect_ratio(str(source_path))
            self.assertAlmostEqual(aspect_ratio, 120 / 80)

            main.make_signature_transparent(
                str(source_path), str(output_path), white_threshold=250, alpha_softness=15
            )
            self.assertTrue(output_path.exists())

            with Image.open(output_path) as converted:
                self.assertEqual(converted.mode, "RGBA")
                alpha_channel = np.array(converted.getchannel("A"))
                self.assertEqual(alpha_channel.shape, (80, 120))
                self.assertTrue((alpha_channel == 0).any())

    def test_cli_no_pdfs_exits_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = Path(tmpdir) / "input"
            output_dir = Path(tmpdir) / "output"
            input_dir.mkdir()
            output_dir.mkdir()

            signature_path = Path(tmpdir) / "sig.png"
            Image.new("RGB", (10, 10), color=(0, 0, 0)).save(signature_path)

            argv_backup = sys.argv
            try:
                sys.argv = [
                    "main.py",
                    "--input",
                    str(input_dir),
                    "--output",
                    str(output_dir),
                    "--name",
                    "Jane Doe",
                    "--signature",
                    str(signature_path),
                    "--workers",
                    "1",
                ]
                main.main()
            finally:
                sys.argv = argv_backup

            self.assertTrue(output_dir.exists())
            self.assertFalse(any(output_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
