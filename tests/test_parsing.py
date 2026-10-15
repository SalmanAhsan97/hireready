from app.parsing import extract_resume

FILLER = b"".join(b"BT /F1 11 Tf 72 %d Td (Led paid social campaigns for SaaS brands and grew signups forty percent) Tj ET\n" % (500 - 14 * i) for i in range(4))


def make_pdf(stream: bytes) -> bytes:
    """A one-page PDF drawing the given content stream with Helvetica as /F1."""
    stream = FILLER + stream
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def test_hidden_keywords_are_removed_and_reported():
    pdf = make_pdf(
        b"BT /F1 11 Tf 72 700 Td (Marketing Manager) Tj ET\n"
        b"q 1 1 1 rg BT /F1 11 Tf 72 680 Td (kubernetes python terraform) Tj ET Q\n"
        b"BT 3 Tr /F1 11 Tf 72 660 Td (golang rust) Tj ET\n"
        b"BT 0 Tr /F1 0.5 Tf 72 640 Td (snowflake dbt) Tj ET\n"
    )
    text, warnings = extract_resume("cv.pdf", pdf)
    assert "Marketing Manager" in text
    for word in ("kubernetes", "golang", "snowflake"):
        assert word not in text
    assert len(warnings) == 1 and "7 words of hidden text" in warnings[0]


def test_white_text_on_a_dark_panel_is_kept():
    pdf = make_pdf(b"0.1 0.1 0.3 rg 0 600 200 192 re f\nq 1 1 1 rg BT /F1 11 Tf 20 700 Td (Jane Doe Marketing Manager) Tj ET Q\n")
    text, warnings = extract_resume("cv.pdf", pdf)
    assert "Jane Doe" in text and warnings == []


def test_scanned_pages_ocr_layer_is_kept():
    ocr = b"".join(b"BT 3 Tr /F1 11 Tf 72 %d Td (Scanned resume line with enough words to read clearly) Tj ET\n" % (760 - 14 * i) for i in range(12))
    text, warnings = extract_resume("cv.pdf", make_pdf(ocr))
    assert "Scanned resume line" in text and warnings == []
