import csv
from pathlib import Path

import llm_classifier
from llm_classifier import LLMClassificationResult, LLMClassifier


class _FakeOpenAI:
    def __init__(self, base_url=None, api_key=None):
        self.base_url = base_url
        self.api_key = api_key
        self.responses = None


def read_csv_rows(csv_path: Path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_classify_latest_documents_updates_only_latest_rows(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.test")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm_classifier, "fitz", object())
    monkeypatch.setattr(llm_classifier, "OpenAI", _FakeOpenAI)

    csv_path = tmp_path / "pdf_inventory.csv"
    pdf_root = tmp_path / "docs"
    pdf_root.mkdir()

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["filename", "relative_path", "is_latest"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {"filename": "old.pdf", "relative_path": "docs/old.pdf", "is_latest": "False"},
                {"filename": "latest.pdf", "relative_path": "docs/latest.pdf", "is_latest": "True"},
            ]
        )

    classifier = LLMClassifier(csv_path=csv_path, pdf_root=pdf_root)
    monkeypatch.setattr(
        classifier,
        "_classify_row",
        lambda row: LLMClassificationResult(
            document_type="image_based",
            confidence=0.93,
            reason="Mostly visual pages.",
            raw_json='{"document_type": "image_based", "confidence": 0.93, "reason": "Mostly visual pages."}',
        ),
    )

    result_path = classifier.classify_latest_documents(show_progress=False)

    captured = capsys.readouterr()
    assert "old.pdf: skipped (not latest)" in captured.err
    assert "latest.pdf: image_based (0.9300)" in captured.err

    assert result_path == csv_path
    assert csv_path.exists()

    rows = read_csv_rows(csv_path)
    rows_by_name = {row["filename"]: row for row in rows}

    assert rows_by_name["old.pdf"]["llm_document_type"] == ""
    assert rows_by_name["old.pdf"]["llm_confidence"] == ""
    assert rows_by_name["latest.pdf"]["llm_document_type"] == "image_based"
    assert rows_by_name["latest.pdf"]["llm_confidence"] == "0.9300"
    assert rows_by_name["latest.pdf"]["llm_reason"] == "Mostly visual pages."


def test_classify_latest_documents_skips_already_classified_rows(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.test")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm_classifier, "fitz", object())
    monkeypatch.setattr(llm_classifier, "OpenAI", _FakeOpenAI)

    csv_path = tmp_path / "pdf_inventory.csv"
    pdf_root = tmp_path / "docs"
    pdf_root.mkdir()

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "filename",
                "relative_path",
                "is_latest",
                "llm_document_type",
                "llm_confidence",
                "llm_reason",
                "llm_raw_json",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "filename": "already.pdf",
                "relative_path": "docs/already.pdf",
                "is_latest": "True",
                "llm_document_type": "text_based",
                "llm_confidence": "0.8500",
                "llm_reason": "Previously classified.",
                "llm_raw_json": '{"document_type":"text_based"}',
            }
        )

    classifier = LLMClassifier(csv_path=csv_path, pdf_root=pdf_root)

    def fail_if_called(_row):
        raise AssertionError("_classify_row should not be called for already classified files")

    monkeypatch.setattr(classifier, "_classify_row", fail_if_called)

    result_path = classifier.classify_latest_documents(show_progress=False)

    captured = capsys.readouterr()
    assert "already.pdf: skipped (already text_based)" in captured.err
    assert result_path == csv_path

    rows = read_csv_rows(csv_path)
    assert rows[0]["llm_document_type"] == "text_based"
    assert rows[0]["llm_confidence"] == "0.8500"


