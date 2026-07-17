import csv
from pathlib import Path

from pdfreducer import PDFReducer


def test_reduce_inventory_replaces_original_when_reduced_file_is_smaller(tmp_path):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"original PDF content" * 10)
    inventory = tmp_path / "inventory.csv"
    inventory.write_text(
        "filename,full_path,relative_path,file_size\n"
        f"document.pdf,{source},document.pdf,{source.stat().st_size}\n",
        encoding="utf-8",
    )

    reducer = PDFReducer()

    def fake_reduce(input_path, output_path=None, **_kwargs):
        destination = Path(output_path)
        destination.write_bytes(b"reduced")
        return destination

    reducer.reduce = fake_reduce

    result = reducer.reduce_inventory(inventory)

    assert result == [source]
    assert source.read_bytes() == b"reduced"
    with inventory.open(newline="", encoding="utf-8") as csv_file:
        row = next(csv.DictReader(csv_file))
    assert Path(row["full_path"]) == source
    assert row["filename"] == source.name
    assert row["file_size"] == str(source.stat().st_size)


def test_reduce_inventory_replaces_original_even_when_reduced_file_is_larger(tmp_path):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"original")
    inventory = tmp_path / "inventory.csv"
    inventory.write_text(
        "filename,full_path,relative_path,file_size\n"
        f"document.pdf,{source},document.pdf,{source.stat().st_size}\n",
        encoding="utf-8",
    )

    reducer = PDFReducer()

    def fake_reduce(input_path, output_path=None, **_kwargs):
        destination = Path(output_path)
        destination.write_bytes(b"larger reduced PDF")
        return destination

    reducer.reduce = fake_reduce

    result = reducer.reduce_inventory(inventory, replace_original=True)

    assert result == [source]
    assert source.read_bytes() == b"larger reduced PDF"
    assert not source.with_name(f".{source.name}.reduced-temp").exists()


def test_reduce_inventory_skips_rows_marked_reduced(tmp_path):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"already reduced")
    inventory = tmp_path / "inventory.csv"
    inventory.write_text(
        "filename,full_path,relative_path,file_size,reduced\n"
        f"document.pdf,{source},document.pdf,{source.stat().st_size},True\n",
        encoding="utf-8",
    )

    reducer = PDFReducer()
    reducer.reduce = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must skip"))

    result = reducer.reduce_inventory(inventory)

    assert result == [source]
    with inventory.open(newline="", encoding="utf-8") as csv_file:
        row = next(csv.DictReader(csv_file))
    assert row["reduced"] == "True"


def test_reduce_inventory_skips_reduced_filename(tmp_path):
    source = tmp_path / "document-reduced.pdf"
    source.write_bytes(b"already reduced")
    inventory = tmp_path / "inventory.csv"
    inventory.write_text(
        "filename,full_path,relative_path,file_size\n"
        f"{source.name},{source},document.pdf,{source.stat().st_size}\n",
        encoding="utf-8",
    )

    reducer = PDFReducer()
    reducer.reduce = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must skip"))

    result = reducer.reduce_inventory(inventory)

    assert result == [source]
    with inventory.open(newline="", encoding="utf-8") as csv_file:
        row = next(csv.DictReader(csv_file))
    assert row["reduced"] == "True"
