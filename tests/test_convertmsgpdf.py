import csv
from pathlib import Path

import convertmsgpdf


class _FakeMsg:
    def __init__(self):
        self.sender = "sender@example.com"
        self.sender_email = ""
        self.to = "recipient@example.com"
        self.cc = "cc@example.com"
        self.date = "2024-01-02 03:04"
        self.subject = "Sample Subject"
        self.body = "Hello from the email body."
        self.htmlBody = ""
        self.closed = False

    def close(self):
        self.closed = True


def read_csv_rows(csv_path: Path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_convert_msg_to_pdf_appends_inventory_row(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    output_root = tmp_path / "output" / "emails"
    inventory_csv = tmp_path / "output" / "pdf_inventory.csv"
    msg_path = input_dir / "nested" / "sample.msg"
    pdf_path = output_root / "nested" / "sample.pdf"

    msg_path.parent.mkdir(parents=True)
    msg_path.write_text("fake msg content", encoding="utf-8")

    fake_msg = _FakeMsg()
    monkeypatch.setattr(convertmsgpdf.extract_msg, "Message", lambda _path: fake_msg)

    def fake_build_pdf(path, meta, body_text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\nFake PDF content\n")

    monkeypatch.setattr(convertmsgpdf, "build_pdf", fake_build_pdf)

    ok = convertmsgpdf.convert_msg_to_pdf(
        msg_path,
        pdf_path,
        inventory_csv=inventory_csv,
        output_root=output_root,
    )

    assert ok is True
    assert fake_msg.closed is True
    assert pdf_path.exists()
    assert inventory_csv.exists()

    rows = read_csv_rows(inventory_csv)
    assert len(rows) == 1

    row = rows[0]
    assert row["filename"] == "sample.pdf"
    assert Path(row["full_path"]) == pdf_path.resolve()
    assert row["relative_path"] == "output/emails/nested/sample.pdf"
    assert row["group_key"] == "sample"
    assert row["revision"] == ""
    assert row["revision_number"] == "-1"
    assert row["is_latest"] == "True"
    assert row["md5_hash"]


def test_convert_msg_to_pdf_does_not_duplicate_existing_inventory_rows(tmp_path, monkeypatch):
    output_root = tmp_path / "output" / "emails"
    inventory_csv = tmp_path / "output" / "pdf_inventory.csv"
    msg_path = tmp_path / "input" / "sample.msg"
    pdf_path = output_root / "sample.pdf"

    msg_path.parent.mkdir(parents=True)
    msg_path.write_text("fake msg content", encoding="utf-8")

    fake_msg = _FakeMsg()
    monkeypatch.setattr(convertmsgpdf.extract_msg, "Message", lambda _path: fake_msg)
    monkeypatch.setattr(
        convertmsgpdf,
        "build_pdf",
        lambda path, meta, body_text: path.write_bytes(b"%PDF-1.4\nFake PDF content\n"),
    )

    assert convertmsgpdf.convert_msg_to_pdf(
        msg_path,
        pdf_path,
        inventory_csv=inventory_csv,
        output_root=output_root,
    )
    assert convertmsgpdf.convert_msg_to_pdf(
        msg_path,
        pdf_path,
        inventory_csv=inventory_csv,
        output_root=output_root,
    )

    rows = read_csv_rows(inventory_csv)
    assert len(rows) == 1
