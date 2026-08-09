"""Create a tiny deterministic PDF for the release-readiness CLI run."""

from __future__ import annotations

import sys
from pathlib import Path


def _pdf_bytes() -> bytes:
    content = (
        b"BT\n/F1 12 Tf\n72 720 Td\n"
        b"(ABSTRACT) Tj\n0 -24 Td\n"
        b"(This synthetic paper studies local trade policy.) Tj\n"
        b"0 -24 Td\n(1. Introduction) Tj\n0 -24 Td\n"
        b"(The introduction describes a reproducible economic question.) Tj\n"
        b"ET\n"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(output)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: create_release_fixture_pdf.py PATH")
    path = Path(sys.argv[1])
    path.write_bytes(_pdf_bytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
