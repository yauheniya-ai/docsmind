"""Generates a small but structurally realistic PDF for testing the parser:
a title, numbered sections/subsections at different heading sizes, body
paragraphs, and one embedded image — without needing a real document on hand.
"""

import io

import pymupdf
from PIL import Image, ImageDraw

OUT_PATH = "examples/sample.pdf"


def make_test_image() -> bytes:
    import random

    random.seed(42)
    img = Image.new("RGB", (400, 260), color=(235, 235, 245))
    draw = ImageDraw.Draw(img)
    # A few "network nodes" and connecting lines with varied colors, so the
    # PNG doesn't compress down to a couple hundred bytes like a flat shape
    # would (real screenshots/diagrams are comfortably above the size floor;
    # this just makes the synthetic test representative of that).
    nodes = [(random.randint(30, 370), random.randint(30, 230)) for _ in range(10)]
    for i, (x, y) in enumerate(nodes):
        color = (random.randint(20, 200), random.randint(20, 200), random.randint(20, 200))
        draw.ellipse([x - 12, y - 12, x + 12, y + 12], fill=color, outline=(0, 0, 0))
        if i > 0:
            draw.line([nodes[i - 1], (x, y)], fill=(120, 120, 120), width=2)
    draw.rectangle([5, 5, 395, 255], outline=(30, 30, 90), width=3)
    draw.text((20, 15), "Network Segmentation Diagram", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build() -> None:
    doc = pymupdf.open()
    page = doc.new_page()

    y = 60
    def write(text: str, size: float, bold: bool = False, gap: int = 14):
        nonlocal y
        fontname = "helv" if not bold else "hebo"
        page.insert_text((50, y), text, fontsize=size, fontname=fontname)
        y += size + gap

    write("Information Security Handbook", 20, bold=True, gap=28)

    write("1. Incident Response", 15, bold=True, gap=18)
    write("All suspected security incidents must be reported to the Security", 10)
    write("Operations team immediately through the incident channel. Employees", 10)
    write("should not investigate independently.", 10, gap=22)

    write("1.1 Triage Timelines", 13, bold=True, gap=16)
    write("Critical incidents require initial triage within 15 minutes of", 10)
    write("being reported. High severity incidents require triage within one", 10)
    write("hour, and all other incidents within one business day.", 10, gap=22)

    write("2. Device Security", 15, bold=True, gap=18)
    write("Company-managed devices must use full-disk encryption, automatic", 10)
    write("screen locking after five minutes, and multi-factor authentication", 10)
    write("for all cloud service logins.", 10, gap=22)

    # Place the image below the current text, then continue with a new page.
    img_bytes = make_test_image()
    rect = pymupdf.Rect(50, y, 350, y + 150)
    page.insert_image(rect, stream=img_bytes)
    y += 170

    write("Figure 1 shows the reference network segmentation diagram.", 10, gap=20)

    page2 = doc.new_page()
    y = 60
    def write2(text: str, size: float, bold: bool = False, gap: int = 14):
        nonlocal y
        fontname = "helv" if not bold else "hebo"
        page2.insert_text((50, y), text, fontsize=size, fontname=fontname)
        y += size + gap

    write2("2.1 Encryption Standards", 13, bold=True, gap=16)
    write2("Disk encryption must use AES-256 or equivalent. Key material must", 10)
    write2("be stored in a hardware-backed keystore where the device supports", 10)
    write2("one, and rotated on a 12 month cycle.", 10, gap=22)

    write2("3. Access Control", 15, bold=True, gap=18)
    write2("Access to production systems requires multi-factor authentication", 10)
    write2("and is reviewed quarterly by the security team.", 10, gap=20)

    doc.save(OUT_PATH)
    doc.close()

    # Attach a PDF bookmark/outline (TOC) matching the heading structure
    # above, so the parser's TOC-first path can be tested against this file
    # too (re-open since PyMuPDF sets TOC on an already-saved doc's xref
    # table more reliably than pre-save).
    doc2 = pymupdf.open(OUT_PATH)
    doc2.set_toc(
        [
            [1, "1. Incident Response", 1],
            [2, "1.1 Triage Timelines", 1],
            [1, "2. Device Security", 1],
            [2, "2.1 Encryption Standards", 2],
            [1, "3. Access Control", 2],
        ]
    )
    tmp_path = OUT_PATH + ".tmp"
    doc2.save(tmp_path)
    doc2.close()
    import os

    os.replace(tmp_path, OUT_PATH)

    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    build()