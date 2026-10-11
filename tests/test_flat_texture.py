"""
Synthetic Unit Tests for Flat-Area Detection and Micro-Texture Injection.
All images are created programmatically. No external test images or dependencies.
Covers all 22 required properties from specification.
"""
from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw

from processor import analyze, prepare
from requirements_config import DEFAULT_PROFILE
from status_model import CheckStatus, OverallStatus
from texture_engine import (
    apply_luminance_texture,
    compute_image_fingerprint,
    detect_flat_areas,
    generate_heatmap_preview,
    generate_multiscale_noise,
    seed_from_fingerprint,
)


class TestFlatTextureEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_flat_texture_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Large flat background detected
    def test_01_large_flat_background(self):
        # 3000x4200 solid light-gray canvas (meets Displate min short side >= 2900)
        img = Image.new("RGB", (3000, 4200), color=(210, 215, 220))
        ratio, full_m, an_m, stats = detect_flat_areas(img)
        self.assertGreater(ratio, 0.15)
        self.assertTrue(stats["exceeds_threshold"])
        self.assertGreater(np.sum(full_m > 0.5), 0)

        # In analyze(): flat_area_check is WARNING
        path = self.temp_dir / "flat_bg.png"
        img.save(path, dpi=(300, 300))
        rep = analyze(path)
        heuristic_checks = {c["id"]: c for c in rep["checks"]["internal_heuristics"]}
        self.assertIn("flat_area_check", heuristic_checks)
        self.assertEqual(heuristic_checks["flat_area_check"]["status"], "WARNING")
        # Ensure heuristic warning does NOT block preparation
        self.assertNotEqual(rep["final_status"], "BLOCKED")

    # 2. Small flat isolated regions ignored
    def test_02_small_flat_isolated_regions(self):
        rng = np.random.default_rng(42)
        # Random noise canvas
        arr = rng.integers(0, 255, size=(600, 600, 3), dtype=np.uint8)
        # Small isolated flat box (12x12 = 144 pixels, well below min_region_pixels)
        arr[50:62, 50:62] = [200, 200, 200]
        img = Image.fromarray(arr)
        ratio, full_m, an_m, stats = detect_flat_areas(img)
        self.assertEqual(ratio, 0.0)
        self.assertEqual(np.sum(full_m > 0), 0)

    # 3. Detailed artwork rejected as flat
    def test_03_detailed_artwork(self):
        rng = np.random.default_rng(99)
        # High-frequency detailed texture
        arr = rng.integers(20, 230, size=(800, 1120, 3), dtype=np.uint8)
        img = Image.fromarray(arr)
        ratio, full_m, an_m, stats = detect_flat_areas(img)
        self.assertEqual(ratio, 0.0)
        self.assertFalse(stats["exceeds_threshold"])

    # 4. Large sky-like smooth region detected
    def test_04_large_sky_like_smooth_region(self):
        # Subtle horizontal sky gradient
        w, h = 800, 1120
        gradient = np.tile(np.linspace(180, 220, w, dtype=np.uint8), (h, 1))
        rgb = np.stack([gradient, gradient, (gradient * 1.1).clip(0, 255).astype(np.uint8)], axis=2)
        img = Image.fromarray(rgb)
        ratio, full_m, an_m, stats = detect_flat_areas(img)
        self.assertGreater(ratio, 0.15)
        self.assertTrue(stats["exceeds_threshold"])

    # 5. Already textured background has low or zero flat ratio
    def test_05_already_textured_background(self):
        w, h = 600, 840
        rng = np.random.default_rng(55)
        # Base color + noticeable grain
        base = np.full((h, w, 3), 150, dtype=np.float32)
        grain = rng.normal(0, 25, (h, w, 1))
        arr = np.clip(base + grain, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
        ratio, full_m, an_m, stats = detect_flat_areas(img)
        self.assertLess(ratio, 0.15)
        self.assertFalse(stats["exceeds_threshold"])

    # 6. Edge protection fades out texture near edges
    def test_06_edge_protection(self):
        # Flat canvas with a sharp contrasting vertical divider at x=300
        arr = np.full((600, 600, 3), 220, dtype=np.uint8)
        arr[:, 300:] = 20
        img = Image.fromarray(arr)
        ratio, full_m, an_m, stats = detect_flat_areas(img)
        # Value deep inside flat area (x=100) vs right at the boundary (x=299-301)
        deep_flat_mask = full_m[:, 100].max()
        edge_mask = full_m[:, 298:303].max()
        self.assertGreater(deep_flat_mask, 0.5)
        self.assertLess(edge_mask, 0.25)

    # 7. Texture OFF preserves existing behavior
    def test_07_texture_off(self):
        img = Image.new("RGB", (3000, 4200), (200, 200, 200))
        src = self.temp_dir / "src_off.png"
        img.save(src, dpi=(300, 300))
        out_dir = self.temp_dir / "out_off"
        res = prepare(src, out_dir, texture_enabled=False)
        self.assertTrue(res["prepared"])
        tex_rec = res["report"]["processing"]["texture"]
        self.assertFalse(tex_rec["applied"])
        self.assertFalse(tex_rec["enabled"])

    # 8. Texture ON applies texture when flat areas are detected
    def test_08_texture_on(self):
        img = Image.new("RGB", (3000, 4200), (200, 200, 200))
        src = self.temp_dir / "src_on.png"
        img.save(src, dpi=(300, 300))
        out_dir = self.temp_dir / "out_on"
        res = prepare(src, out_dir, texture_enabled=True, texture_strength="subtle")
        self.assertTrue(res["prepared"])
        tex_rec = res["report"]["processing"]["texture"]
        self.assertTrue(tex_rec["applied"])
        self.assertTrue(tex_rec["enabled"])
        self.assertEqual(tex_rec["strength"], "subtle")

    # 9. Fine Paper procedural noise properties
    def test_09_texture_fine_paper(self):
        noise = generate_multiscale_noise(500, 700, seed=123, texture_type="fine_paper")
        self.assertEqual(noise.shape, (700, 500))
        self.assertAlmostEqual(float(noise.mean()), 0.0, places=2)
        self.assertAlmostEqual(float(noise.std()), 1.0, places=1)

    # 10. Fine Canvas procedural noise properties
    def test_10_texture_fine_canvas(self):
        noise = generate_multiscale_noise(500, 700, seed=123, texture_type="fine_canvas")
        self.assertEqual(noise.shape, (700, 500))
        self.assertAlmostEqual(float(noise.mean()), 0.0, places=2)
        self.assertAlmostEqual(float(noise.std()), 1.0, places=1)

    # 11. Amplitude Very Subtle (~0.8%)
    def test_11_strength_very_subtle(self):
        img = Image.new("RGB", (400, 400), (128, 128, 128))
        mask = np.ones((400, 400), dtype=np.float32)
        amp = DEFAULT_PROFILE.texture_amplitudes["very_subtle"]
        out_img, stats = apply_luminance_texture(img, mask, amplitude=amp, seed=42)
        self.assertTrue(stats["applied"])
        # Expected max luminance delta ~ 0.008 * 255 * 3 (with noise std ~1) <= 10
        self.assertLessEqual(stats["max_y_delta"], 10.0)
        self.assertGreater(stats["mean_y_delta"], 0.5)

    # 12. Amplitude Subtle (~1.5%)
    def test_12_strength_subtle(self):
        img = Image.new("RGB", (400, 400), (128, 128, 128))
        mask = np.ones((400, 400), dtype=np.float32)
        amp = DEFAULT_PROFILE.texture_amplitudes["subtle"]
        out_img, stats = apply_luminance_texture(img, mask, amplitude=amp, seed=42)
        self.assertTrue(stats["applied"])
        self.assertLessEqual(stats["max_y_delta"], 18.0)
        self.assertGreater(stats["mean_y_delta"], 1.0)

    # 13. Amplitude Medium (~3.0%)
    def test_13_strength_medium(self):
        img = Image.new("RGB", (400, 400), (128, 128, 128))
        mask = np.ones((400, 400), dtype=np.float32)
        amp = DEFAULT_PROFILE.texture_amplitudes["medium"]
        out_img, stats = apply_luminance_texture(img, mask, amplitude=amp, seed=42)
        self.assertTrue(stats["applied"])
        self.assertLessEqual(stats["max_y_delta"], 35.0)
        self.assertGreater(stats["mean_y_delta"], 2.5)

    # 14. Deterministic output with same input
    def test_14_deterministic_output(self):
        img = Image.new("RGB", (300, 300), (150, 160, 170))
        mask = np.ones((300, 300), dtype=np.float32)
        out1, _ = apply_luminance_texture(img, mask, amplitude=0.015, seed=777)
        out2, _ = apply_luminance_texture(img, mask, amplitude=0.015, seed=777)
        np.testing.assert_array_equal(np.asarray(out1), np.asarray(out2))

    # 15. Same content with different path/filename produces same texture
    def test_15_same_content_different_filename_or_path(self):
        img = Image.new("RGB", (500, 700), (180, 185, 190))
        p1 = self.temp_dir / "art_one.png"
        p2 = self.temp_dir / "subdir" / "renamed_copy.png"
        p2.parent.mkdir()
        img.save(p1)
        img.save(p2)

        fp1 = compute_image_fingerprint(p1)
        fp2 = compute_image_fingerprint(p2)
        self.assertEqual(fp1, fp2)
        seed1 = seed_from_fingerprint(fp1)
        seed2 = seed_from_fingerprint(fp2)
        self.assertEqual(seed1, seed2)

    # 16. Pixels outside mask remain byte-identical in memory
    def test_16_pixels_outside_mask_unchanged_in_memory(self):
        rng = np.random.default_rng(88)
        orig_arr = rng.integers(20, 230, size=(250, 250, 3), dtype=np.uint8)
        orig_img = Image.fromarray(orig_arr)

        # Mask only active in left half
        mask = np.zeros((250, 250), dtype=np.float32)
        mask[:, :125] = 1.0

        tex_img, _ = apply_luminance_texture(orig_img, mask, amplitude=0.03, seed=123)
        tex_arr = np.asarray(tex_img)

        # Right half (mask == 0) must be 100% bit-identical
        zero_mask = (mask == 0.0)
        np.testing.assert_array_equal(tex_arr[zero_mask], orig_arr[zero_mask])

    # 17. Chroma remains within small tolerance
    def test_17_chroma_remains_within_tolerance(self):
        img = Image.new("RGB", (200, 200), (120, 160, 200))
        mask = np.ones((200, 200), dtype=np.float32)
        tex_img, _ = apply_luminance_texture(img, mask, amplitude=0.015, seed=123)

        orig_ycbcr = img.convert("YCbCr")
        tex_ycbcr = tex_img.convert("YCbCr")

        cb_delta = np.abs(np.asarray(tex_ycbcr.getchannel("Cb"), dtype=int) - np.asarray(orig_ycbcr.getchannel("Cb"), dtype=int))
        cr_delta = np.abs(np.asarray(tex_ycbcr.getchannel("Cr"), dtype=int) - np.asarray(orig_ycbcr.getchannel("Cr"), dtype=int))
        self.assertLessEqual(cb_delta.max(), 2)
        self.assertLessEqual(cr_delta.max(), 2)

    # 18. Dimensions preserved
    def test_18_dimensions_preserved(self):
        img = Image.new("RGB", (512, 768), (140, 140, 140))
        mask = np.ones((768, 512), dtype=np.float32)
        tex_img, _ = apply_luminance_texture(img, mask, amplitude=0.015)
        self.assertEqual(tex_img.size, (512, 768))

    # 19. Alpha behavior preserved
    def test_19_alpha_behavior_preserved(self):
        arr = np.full((120, 120, 4), 180, dtype=np.uint8)
        arr[:, :, 3] = np.linspace(30, 240, 120, dtype=np.uint8)
        rgba = Image.fromarray(arr)
        mask = np.ones((120, 120), dtype=np.float32)

        tex_rgba, _ = apply_luminance_texture(rgba, mask, amplitude=0.015)
        self.assertEqual(tex_rgba.mode, "RGBA")
        np.testing.assert_array_equal(np.asarray(tex_rgba.getchannel("A")), arr[:, :, 3])

    # 20. ICC profile preserved
    def test_20_icc_profile_preserved(self):
        img = Image.new("RGB", (200, 200), (150, 150, 150))
        srgb_profile = ImageCms.createProfile("sRGB")
        img.info["icc_profile"] = ImageCms.ImageCmsProfile(srgb_profile).tobytes()

        mask = np.ones((200, 200), dtype=np.float32)
        tex_img, _ = apply_luminance_texture(img, mask, amplitude=0.015)
        self.assertIn("icc_profile", tex_img.info)
        self.assertEqual(tex_img.info["icc_profile"], img.info["icc_profile"])

    # 21. Final JPEG size handling / fallback
    def test_21_final_jpeg_size_handling(self):
        # Prepare image with texture enabled
        img = Image.new("RGB", (2900, 4060), (210, 210, 210))
        src = self.temp_dir / "src_limit.png"
        img.save(src, dpi=(300, 300))
        out_dir = self.temp_dir / "out_limit"
        res = prepare(src, out_dir, texture_enabled=True, texture_strength="medium")
        self.assertTrue(res["prepared"])
        out_file = Path(res["final_path"])
        self.assertTrue(out_file.is_file())
        self.assertLessEqual(out_file.stat().st_size, DEFAULT_PROFILE.maximum_file_size_bytes)

    # 22. Existing validation still works and produces expected status
    def test_22_existing_validation_still_works(self):
        img = Image.new("RGB", (3000, 4200), (220, 220, 220))
        src = self.temp_dir / "src_valid.png"
        img.save(src, dpi=(300, 300))
        rep = analyze(src)
        self.assertIn("checks", rep)
        self.assertIn("official_requirements", rep["checks"])
        self.assertIn("internal_heuristics", rep["checks"])
        # Should not be blocked
        self.assertFalse(rep["blocked"])


if __name__ == "__main__":
    unittest.main()
