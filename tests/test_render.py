"""Tests for the pure parts of hiresclip (no macOS pasteboard involved).

Run with:  python -m unittest discover -s tests -v
Needs pypdfium2 and Pillow (both in environment.yml); pdftocairo is optional.

The clipboard path (read com.adobe.pdf, write PNG) is deliberately not tested
here: it needs a real macOS session. It was tested manually on macOS Tahoe with
PowerPoint for Mac 365.
"""
from __future__ import annotations

import io
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import hiresclip  # noqa: E402

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def make_pdf(pages: list[tuple[float, float, bytes]]) -> bytes:
    """Build a minimal, well-formed PDF from (width_pt, height_pt, content_stream) tuples."""
    objects: list[bytes] = []          # object bodies, index i -> object number i+1

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")                 # placeholder, filled in below
    pages_obj = add(b"")
    page_ids = []
    for width, height, content in pages:
        stream = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        page = add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %g %g] /Contents %d 0 R >>"
                   % (pages_obj, width, height, stream))
        page_ids.append(page)
    kids = b" ".join(b"%d 0 R" % p for p in page_ids)
    objects[pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objects) + 1))
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objects) + 1, catalog, xref))
    return out.getvalue()


# 100 x 100 pt page, red square covering the lower-left quadrant
RED_SQUARE = b"1 0 0 rg 0 0 50 50 re f"
ONE_PAGE = make_pdf([(100, 100, RED_SQUARE)])
# second page has a blue square instead, so we can tell which page was rendered
TWO_PAGES = make_pdf([(100, 100, RED_SQUARE), (100, 100, b"0 0 1 rg 0 0 50 50 re f")])


def decode(png: bytes):
    from PIL import Image
    return Image.open(io.BytesIO(png))


class RenderPngTests(unittest.TestCase):
    def test_returns_png_with_expected_size_at_72_dpi(self):
        r = hiresclip.render_png(ONE_PAGE, dpi=72)
        self.assertTrue(r.png.startswith(PNG_SIGNATURE))
        self.assertEqual((r.width, r.height), (100, 100))
        self.assertEqual(r.pages, 1)
        self.assertEqual(r.dpi, 72)

    def test_size_scales_with_dpi(self):
        r = hiresclip.render_png(ONE_PAGE, dpi=300)
        # 100 pt * 300/72 = 416.67 px; pypdfium2 rounds up
        self.assertIn(r.width, (416, 417))
        self.assertIn(r.height, (416, 417))
        img = decode(r.png)
        self.assertEqual(img.size, (r.width, r.height))

    def test_default_dpi_is_600(self):
        r = hiresclip.render_png(ONE_PAGE)
        self.assertEqual(r.dpi, hiresclip.DEFAULT_DPI)
        self.assertEqual(hiresclip.DEFAULT_DPI, 600)
        self.assertIn(r.width, (833, 834))

    def test_png_carries_dpi_metadata(self):
        r = hiresclip.render_png(ONE_PAGE, dpi=300)
        dpi = decode(r.png).info.get("dpi")
        self.assertIsNotNone(dpi)
        self.assertAlmostEqual(dpi[0], 300, delta=1)
        self.assertAlmostEqual(dpi[1], 300, delta=1)

    def test_content_is_rendered(self):
        img = decode(hiresclip.render_png(ONE_PAGE, dpi=72).png)
        # PDF origin is bottom-left; the square is in the lower-left quadrant of the image
        self.assertEqual(img.getpixel((25, 75)), (255, 0, 0, 255))  # inside the square
        self.assertEqual(img.getpixel((75, 25))[3], 0)              # outside: transparent

    def test_default_background_is_transparent(self):
        r = hiresclip.render_png(ONE_PAGE, dpi=72)
        self.assertTrue(r.alpha)
        img = decode(r.png)
        self.assertEqual(img.mode, "RGBA")
        self.assertEqual(img.getpixel((75, 25))[3], 0)            # background transparent
        self.assertEqual(img.getpixel((25, 75)), (255, 0, 0, 255))  # square still opaque red

    def test_white_background_on_request(self):
        r = hiresclip.render_png(ONE_PAGE, dpi=72, alpha=False)
        self.assertFalse(r.alpha)
        img = decode(r.png).convert("RGBA")
        self.assertEqual(img.getpixel((75, 25)), (255, 255, 255, 255))

    def test_multipage_renders_first_page_and_reports_count(self):
        r = hiresclip.render_png(TWO_PAGES, dpi=72)
        self.assertEqual(r.pages, 2)
        img = decode(r.png).convert("RGB")
        self.assertEqual(img.getpixel((25, 75)), (255, 0, 0))    # red = page 1, not blue

    def test_explicit_page_index(self):
        r = hiresclip.render_png(TWO_PAGES, dpi=72, page=1)
        img = decode(r.png).convert("RGB")
        self.assertEqual(img.getpixel((25, 75)), (0, 0, 255))

    def test_page_out_of_range(self):
        with self.assertRaises(ValueError):
            hiresclip.render_png(ONE_PAGE, dpi=72, page=1)

    def test_empty_input_rejected(self):
        with self.assertRaises(ValueError):
            hiresclip.render_png(b"", dpi=72)

    def test_bad_dpi_rejected(self):
        with self.assertRaises(ValueError):
            hiresclip.render_png(ONE_PAGE, dpi=0)

    def test_non_pdf_rejected(self):
        with self.assertRaises(Exception):
            hiresclip.render_png(b"this is not a pdf", dpi=72)


class SideFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="hiresclip-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pdf_copy_is_written_without_pdftocairo(self):
        # a non-existent converter path must degrade to "PDF only", not raise
        pdf_path, svg_path = hiresclip.save_side_files(
            ONE_PAGE, self.tmp / "sub", pdftocairo=self.tmp / "no-such-binary", stem="x")
        self.assertEqual(pdf_path, self.tmp / "sub" / "x.pdf")
        self.assertEqual(pdf_path.read_bytes(), ONE_PAGE)
        self.assertIsNone(svg_path)

    def test_svg_is_written_when_pdftocairo_available(self):
        tool = hiresclip.find_pdftocairo()
        if tool is None:
            self.skipTest("pdftocairo not available")
        _, svg_path = hiresclip.save_side_files(ONE_PAGE, self.tmp, pdftocairo=tool, stem="y")
        self.assertIsNotNone(svg_path)
        self.assertTrue(svg_path.is_file())
        self.assertIn(b"<svg", svg_path.read_bytes())

    def test_find_pdftocairo_returns_path_or_none(self):
        tool = hiresclip.find_pdftocairo()
        self.assertTrue(tool is None or (isinstance(tool, pathlib.Path) and tool.is_file()))


class CliTests(unittest.TestCase):
    def test_env_var_sets_default_dpi(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"HIRESCLIP_DPI": "300"}):
            self.assertEqual(hiresclip.build_parser().parse_args([]).dpi, 300)
        with mock.patch.dict("os.environ", {"PDFCLIP_DPI": "150"}, clear=False):
            import os
            os.environ.pop("HIRESCLIP_DPI", None)
            self.assertEqual(hiresclip.build_parser().parse_args([]).dpi, 150)

    def test_flag_overrides_env(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"HIRESCLIP_DPI": "300"}):
            self.assertEqual(hiresclip.build_parser().parse_args(["--dpi", "72"]).dpi, 72)

    def test_no_svg_and_svg_dir(self):
        ns = hiresclip.build_parser().parse_args(["--no-svg", "--svg-dir", "/tmp/x"])
        self.assertTrue(ns.no_svg)
        self.assertEqual(ns.svg_dir, pathlib.Path("/tmp/x"))

    def test_white_flag(self):
        self.assertFalse(hiresclip.build_parser().parse_args([]).white)
        self.assertTrue(hiresclip.build_parser().parse_args(["--white"]).white)

    def test_applescript_escaping(self):
        self.assertEqual(hiresclip._applescript_string('a "b" \\ c'), 'a \\"b\\" \\\\ c')


if __name__ == "__main__":
    unittest.main()
