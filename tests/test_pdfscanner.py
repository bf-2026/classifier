import csv
from pathlib import Path

from pdfscanner import PDFScanner


def read_csv_rows(csv_path: Path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_scan_and_export_marks_latest_revision_across_folders(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    folder_a = docs_dir / "folder_a"
    folder_b = docs_dir / "folder_b"
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)

    older = folder_a / "260220 Spezifikation Slickline-Wireline Service rev00a.pdf"
    newer = folder_b / "260220 Spezifikation Slickline-Wireline Service rev00b.pdf"
    other = folder_b / "Standalone Document rev01.pdf"

    older.write_bytes(b"older version content")
    newer.write_bytes(b"newer version content")
    other.write_bytes(b"standalone content")

    creation_times = {
        older.name: 10.0,
        newer.name: 20.0,
        other.name: 15.0,
    }

    output_csv = tmp_path / "pdf_inventory.csv"
    scanner = PDFScanner(dir=str(docs_dir), output=str(output_csv))
    monkeypatch.setattr(
        scanner,
        "_file_creation_time",
        lambda file_path: creation_times[file_path.name],
    )
    scanner.scan_and_export()

    rows = read_csv_rows(output_csv)

    assert len(rows) == 3
    assert {"filename", "relative_path", "group_key", "revision", "is_latest"}.issubset(
        rows[0].keys()
    )

    grouped = {row["filename"]: row for row in rows}

    assert grouped["260220 Spezifikation Slickline-Wireline Service rev00a.pdf"][
        "relative_path"
    ] == "docs/folder_a/260220 Spezifikation Slickline-Wireline Service rev00a.pdf"
    assert grouped["260220 Spezifikation Slickline-Wireline Service rev00a.pdf"][
        "group_key"
    ] == "260220 Spezifikation Slickline-Wireline Service"
    assert grouped["260220 Spezifikation Slickline-Wireline Service rev00a.pdf"][
        "revision"
    ] == "rev00a"
    assert grouped["260220 Spezifikation Slickline-Wireline Service rev00a.pdf"][
        "is_latest"
    ] == "False"

    assert grouped["260220 Spezifikation Slickline-Wireline Service rev00b.pdf"][
        "relative_path"
    ] == "docs/folder_b/260220 Spezifikation Slickline-Wireline Service rev00b.pdf"
    assert grouped["260220 Spezifikation Slickline-Wireline Service rev00b.pdf"][
        "group_key"
    ] == "260220 Spezifikation Slickline-Wireline Service"
    assert grouped["260220 Spezifikation Slickline-Wireline Service rev00b.pdf"][
        "revision"
    ] == "rev00b"
    assert grouped["260220 Spezifikation Slickline-Wireline Service rev00b.pdf"][
        "is_latest"
    ] == "True"

    assert grouped["Standalone Document rev01.pdf"]["relative_path"] == "docs/folder_b/Standalone Document rev01.pdf"
    assert grouped["Standalone Document rev01.pdf"]["is_latest"] == "True"


def test_scan_and_export_uses_creation_date_when_revision_number_matches(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    folder_a = docs_dir / "folder_a"
    folder_b = docs_dir / "folder_b"
    folder_c = docs_dir / "folder_c"
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)
    folder_c.mkdir(parents=True)

    rev01 = folder_a / "250512 Spezifikation Schweißarbeiten_rev01.pdf"
    rev02 = folder_b / "250512 Spezifikation Schweißarbeiten_rev02.pdf"
    rev02a = folder_c / "250512 Spezifikation Schweißarbeiten_rev02a.pdf"

    rev01.write_bytes(b"rev01 content")
    rev02.write_bytes(b"rev02 content")
    rev02a.write_bytes(b"rev02a content")

    creation_times = {
        rev01.name: 30.0,
        rev02.name: 10.0,
        rev02a.name: 20.0,
    }

    output_csv = tmp_path / "pdf_inventory.csv"
    scanner = PDFScanner(dir=str(docs_dir), output=str(output_csv))
    monkeypatch.setattr(
        scanner,
        "_file_creation_time",
        lambda file_path: creation_times[file_path.name],
    )

    scanner.scan_and_export()

    rows = read_csv_rows(output_csv)
    grouped = {row["filename"]: row for row in rows}

    assert len(rows) == 3
    assert grouped["250512 Spezifikation Schweißarbeiten_rev01.pdf"]["group_key"] == "250512 Spezifikation Schweißarbeiten"
    assert grouped["250512 Spezifikation Schweißarbeiten_rev01.pdf"]["revision"] == "rev01"
    assert grouped["250512 Spezifikation Schweißarbeiten_rev01.pdf"]["is_latest"] == "False"

    assert grouped["250512 Spezifikation Schweißarbeiten_rev02.pdf"]["revision"] == "rev02"
    assert grouped["250512 Spezifikation Schweißarbeiten_rev02.pdf"]["is_latest"] == "False"

    assert grouped["250512 Spezifikation Schweißarbeiten_rev02a.pdf"]["revision"] == "rev02a"
    assert grouped["250512 Spezifikation Schweißarbeiten_rev02a.pdf"]["is_latest"] == "True"
