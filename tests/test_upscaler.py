import shutil
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from upscaler import (
    AVAILABLE_MODELS,
    calculate_required_scale,
    get_upscaler_info,
    is_upscaler_available,
    upscale_image,
)
from processor import analyze_and_prepare, prepare


class TestUpscalerModule(unittest.TestCase):
    def test_upscaler_available(self):
        self.assertTrue(is_upscaler_available())
        info = get_upscaler_info()
        self.assertTrue(info["available"])
        self.assertEqual(info["engine"], "Real-ESRGAN NCNN Vulkan")
        self.assertIn("realesrgan-x4plus", info["available_models"])

    def test_scale_calculation(self):
        # 3000 >= 2900 -> 1 (no upscale needed)
        self.assertEqual(calculate_required_scale(3000, 4200), 1)
        # 2900 -> 1
        self.assertEqual(calculate_required_scale(2900, 4000), 1)
        # 1600 -> 2 (1600 * 2 = 3200 >= 2900)
        self.assertEqual(calculate_required_scale(1600, 2200), 2)
        # 1000 -> 3 (1000 * 3 = 3000 >= 2900)
        self.assertEqual(calculate_required_scale(1000, 1400), 3)
        # 700 -> 4 (700 * 4 = 2800, max scale is 4)
        self.assertEqual(calculate_required_scale(700, 1000), 4)

    def test_direct_upscale_execution(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="test_upscale_"))
        try:
            # Create a small 100x140 RGB image
            src_path = temp_dir / "small.jpg"
            img = Image.new("RGB", (100, 140), color=(120, 180, 220))
            img.save(src_path, "JPEG")

            out_path = temp_dir / "upscaled.png"
            res = upscale_image(src_path, out_path, scale=2, model_name="realesrgan-x4plus")

            self.assertTrue(res["success"])
            self.assertEqual(res["scale"], 2)
            self.assertEqual(res["original_size"], [100, 140])
            self.assertEqual(res["upscaled_size"], [200, 280])
            self.assertTrue(out_path.is_file())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_prepare_without_upscale_blocks_low_res(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="test_prep_no_up_"))
        try:
            src_path = temp_dir / "small_source.jpg"
            img = Image.new("RGB", (800, 1120), color=(50, 100, 150))
            img.save(src_path, "JPEG")

            out_dir = temp_dir / "out"
            res = prepare(src_path, out_dir, auto_upscale=False)

            self.assertFalse(res["prepared"])
            self.assertTrue(res["report"]["blocked"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_prepare_with_upscale_succeeds(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="test_prep_with_up_"))
        try:
            # Create a 750x1050 image (shortest side 750 < 2900, requires 4x upscale -> 3000x4200 >= 2900)
            src_path = temp_dir / "source_750.jpg"
            img = Image.new("RGB", (750, 1050), color=(80, 140, 200))
            img.save(src_path, "JPEG")

            out_dir = temp_dir / "out"
            res = analyze_and_prepare(src_path, out_dir, auto_upscale=True, upscale_model="realesrgan-x4plus")

            self.assertTrue(res["prepared"], f"Expected prepared=True, got {res.get('error')}")
            rep = res["report"]
            self.assertFalse(rep["blocked"])

            # Verify shortest side was upscaled to >= 2900 px
            final = rep["final"]
            self.assertGreaterEqual(min(final["width"], final["height"]), 2900)
            self.assertTrue(final["upscaled"])
            self.assertIsNotNone(final["upscale_info"])
            self.assertEqual(final["upscale_info"]["scale"], 4)

            # Check that final jpg and preview exist on disk
            self.assertTrue((out_dir / "displate_ready.jpg").is_file())
            self.assertTrue((out_dir / "safe_area_preview.jpg").is_file())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
