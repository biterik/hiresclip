#!/usr/bin/env python3
"""hiresclip - replace the PDF snippet on the macOS clipboard with a high-dpi PNG.

Workflow: select a region in Preview, Cmd-C, run this script (via a hotkey),
Cmd-V in PowerPoint. Office for Mac would otherwise paste the low-resolution
TIFF flavour of the selection; the PDF flavour is rendered here at a high dpi
and put back on the clipboard as a PNG, which also survives the trip to
PowerPoint on Windows.

As a side output a vector SVG of the same snippet is written next to a copy of
the PDF in ~/Desktop/hiresclip/ (needs pdftocairo from poppler; skipped
silently if it is not available).

Configuration:
  --dpi N / HIRESCLIP_DPI / PDFCLIP_DPI   render resolution (default 600)
  --no-svg                                do not write the PDF/SVG side files
  --svg-dir DIR                           where to write them (default ~/Desktop/hiresclip)
  --check                                 verify the installation, touch nothing

The render step (render_png) is a pure function without any macOS dependency so
that it can be unit-tested off a Mac; everything that talks to the pasteboard
lives in main().
"""
from __future__ import annotations

import argparse
import datetime
import io
import os
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

__version__ = "1.0.0"

DEFAULT_DPI = 600
DEFAULT_SVG_DIR = pathlib.Path.home() / "Desktop" / "hiresclip"
PDF_PASTEBOARD_TYPE = "com.adobe.pdf"
NOTIFICATION_TITLE = "hiresclip"


# --------------------------------------------------------------------------- #
# Pure part: PDF bytes -> PNG bytes                                            #
# --------------------------------------------------------------------------- #

@dataclass
class Rendered:
    """Result of rendering one page of a PDF."""

    png: bytes
    width: int   # pixels
    height: int  # pixels
    pages: int   # number of pages in the source PDF
    dpi: int


def render_png(pdf_bytes: bytes, dpi: int = DEFAULT_DPI, page: int = 0) -> Rendered:
    """Rasterise one page of *pdf_bytes* at *dpi* and return it as PNG bytes.

    Raises ValueError for empty input, for a document without pages, or for an
    out-of-range page index. pypdfium2 raises its own error for data that is
    not a PDF.
    """
    if not pdf_bytes:
        raise ValueError("empty PDF data")
    if dpi <= 0:
        raise ValueError(f"dpi must be positive, got {dpi}")

    import pypdfium2 as pdfium  # imported here so --help works without it

    doc = pdfium.PdfDocument(pdf_bytes)
    n_pages = len(doc)
    if n_pages == 0:
        raise ValueError("PDF has no pages")
    if not 0 <= page < n_pages:
        raise ValueError(f"page {page} out of range (document has {n_pages} pages)")

    img = doc[page].render(scale=dpi / 72).to_pil()
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(dpi, dpi))
    return Rendered(png=buf.getvalue(), width=img.width, height=img.height,
                    pages=n_pages, dpi=dpi)


# --------------------------------------------------------------------------- #
# Side output: PDF copy + SVG via pdftocairo                                   #
# --------------------------------------------------------------------------- #

def find_pdftocairo() -> Optional[pathlib.Path]:
    """Locate pdftocairo: next to the running interpreter (conda env), else on PATH."""
    candidate = pathlib.Path(sys.executable).parent / "pdftocairo"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("pdftocairo")
    return pathlib.Path(found) if found else None


def save_side_files(pdf_bytes: bytes, svg_dir: pathlib.Path,
                    pdftocairo: Optional[pathlib.Path] = None,
                    stem: Optional[str] = None) -> tuple[pathlib.Path, Optional[pathlib.Path]]:
    """Write <stem>.pdf into *svg_dir* and, if pdftocairo is available, <stem>.svg.

    Returns (pdf_path, svg_path_or_None). Never raises because of a missing or
    failing pdftocairo; the SVG is simply not produced.
    """
    svg_dir = pathlib.Path(svg_dir).expanduser()
    svg_dir.mkdir(parents=True, exist_ok=True)
    if stem is None:
        stem = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = svg_dir / f"{stem}.pdf"
    pdf_path.write_bytes(pdf_bytes)

    if pdftocairo is None:
        pdftocairo = find_pdftocairo()
    if pdftocairo is None:
        return pdf_path, None

    svg_path = svg_dir / f"{stem}.svg"
    try:
        result = subprocess.run([str(pdftocairo), "-svg", str(pdf_path), str(svg_path)],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return pdf_path, None
    if result.returncode != 0 or not svg_path.is_file():
        return pdf_path, None
    return pdf_path, svg_path


# --------------------------------------------------------------------------- #
# macOS glue: notifications and pasteboard                                     #
# --------------------------------------------------------------------------- #

def _applescript_string(text: str) -> str:
    """Escape *text* for use inside a double-quoted AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify(message: str, title: str = NOTIFICATION_TITLE) -> None:
    """Show a macOS user notification via osascript (the sender shows as 'Script Editor')."""
    script = (f'display notification "{_applescript_string(message)}" '
              f'with title "{_applescript_string(title)}"')
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
    print(f"{title}: {message}", file=sys.stderr)


def read_clipboard_pdf() -> Optional[bytes]:
    """Return the com.adobe.pdf flavour of the general pasteboard, or None."""
    from AppKit import NSPasteboard

    data = NSPasteboard.generalPasteboard().dataForType_(PDF_PASTEBOARD_TYPE)
    return bytes(data) if data is not None else None


def write_clipboard_png(png: bytes) -> None:
    """Replace the general pasteboard contents with a single PNG item."""
    from AppKit import NSPasteboard, NSPasteboardTypePNG
    from Foundation import NSData

    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setData_forType_(NSData.dataWithBytes_length_(png, len(png)), NSPasteboardTypePNG)


# --------------------------------------------------------------------------- #
# Command line                                                                 #
# --------------------------------------------------------------------------- #

def _default_dpi() -> int:
    for var in ("HIRESCLIP_DPI", "PDFCLIP_DPI"):
        value = os.environ.get(var)
        if value:
            try:
                return int(value)
            except ValueError:
                print(f"hiresclip: ignoring non-integer {var}={value!r}", file=sys.stderr)
    return DEFAULT_DPI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hiresclip",
        description="Replace the PDF snippet on the macOS clipboard with a high-dpi PNG.")
    p.add_argument("--dpi", type=int, default=_default_dpi(),
                   help="render resolution in dots per inch "
                        "(default: $HIRESCLIP_DPI, $PDFCLIP_DPI or %(default)s)")
    p.add_argument("--no-svg", action="store_true",
                   help="do not write the PDF/SVG side files")
    p.add_argument("--svg-dir", type=pathlib.Path, default=DEFAULT_SVG_DIR,
                   help="directory for the PDF/SVG side files (default: %(default)s)")
    p.add_argument("--check", action="store_true",
                   help="verify the installation (imports, pdftocairo) and exit; "
                        "does not touch the clipboard")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def check_installation() -> int:
    """Print what hiresclip would use and return 0 if the core pieces import."""
    ok = True
    print(f"hiresclip {__version__}")
    print(f"python:     {sys.executable}")
    for module in ("pypdfium2", "PIL", "AppKit"):
        try:
            __import__(module)
            print(f"{module + ':':<12}ok")
        except Exception as exc:  # noqa: BLE001 - report anything, this is a diagnostic
            print(f"{module + ':':<12}MISSING ({exc})")
            ok = False
    pdftocairo = find_pdftocairo()
    print(f"pdftocairo: {pdftocairo or 'not found (SVG side output will be skipped)'}")
    print(f"dpi:        {_default_dpi()}")
    print(f"svg dir:    {DEFAULT_SVG_DIR}")
    print("status:     " + ("ok" if ok else "BROKEN"))
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        return check_installation()
    if args.dpi <= 0:
        print("hiresclip: --dpi must be positive", file=sys.stderr)
        return 2

    if sys.platform != "darwin":
        print("hiresclip: the clipboard part only works on macOS", file=sys.stderr)
        return 2

    pdf_bytes = read_clipboard_pdf()
    if pdf_bytes is None:
        notify("No PDF on clipboard - copy a region in Preview first")
        return 1

    try:
        rendered = render_png(pdf_bytes, dpi=args.dpi)
    except Exception as exc:  # noqa: BLE001 - surface any render failure as a notification
        notify(f"Could not render the PDF: {exc}")
        return 1

    svg_path = None
    if not args.no_svg:
        try:
            _, svg_path = save_side_files(pdf_bytes, args.svg_dir)
        except OSError as exc:
            print(f"hiresclip: could not write side files: {exc}", file=sys.stderr)

    write_clipboard_png(rendered.png)

    msg = f"Clipboard now holds a {rendered.dpi} dpi PNG ({rendered.width}x{rendered.height} px)"
    if rendered.pages > 1:
        msg += f" - page 1 of {rendered.pages} rendered"
    if svg_path is not None:
        msg += f"; SVG saved as {svg_path.name}"
    notify(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
