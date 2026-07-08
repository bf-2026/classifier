import csv
from pathlib import Path

from inventory_csv import PdfInventoryCsv


def read_csv_rows(csv_path: Path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_append_row_if_missing_writes_header_and_skips_duplicates(tmp_path):
    csv_path = tmp_path / "output" / "pdf_inventory.csv"
    store = PdfInventoryCsv(csv_path)

    row = {
        "filename": "sample.pdf",
        "full_path": str(tmp_path / "output" / "emails" / "sample.pdf"),
        "relative_path": "output/emails/sample.pdf",
        "group_key": "sample",
        "revision": "",
        "revision_number": "-1",
        "creation_time": "2024-01-01 00:00:00",
        "is_latest": "True",
        "md5_hash": "abc123",
    }

    assert store.append_row_if_missing(row) is True
    assert store.append_row_if_missing(row) is False

    rows = read_csv_rows(csv_path)
    assert len(rows) == 1
    assert rows[0]["filename"] == "sample.pdf"
    assert rows[0]["relative_path"] == "output/emails/sample.pdf"


def test_load_existing_relative_paths_normalizes_separators(tmp_path):
    csv_path = tmp_path / "pdf_inventory.csv"
    csv_path.write_text(
        "filename,full_path,relative_path,group_key,revision,revision_number,creation_time,is_latest,md5_hash\n"
        "sample.pdf,C:\\tmp\\sample.pdf,docs\\nested\\sample.pdf,sample,,-1,2024-01-01 00:00:00,True,hash\n",
        encoding="utf-8",
    )

    store = PdfInventoryCsv(csv_path)
    assert store.load_existing_relative_paths() == {"docs/nested/sample.pdf"}
