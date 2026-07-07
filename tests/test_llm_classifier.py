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


def test_classify_latest_documents_updates_only_latest_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.test")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm_classifier, "fitz", object())
    monkeypatch.setattr(llm_classifier, "OpenAI", _FakeOpenAI)

    csv_path = tmp_path / "pdf_inventory.csv"
    output_csv = tmp_path / "classified.csv"
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

    classifier = LLMClassifier(csv_path=csv_path, pdf_root=pdf_root, output_csv=output_csv)
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

    result_path = classifier.classify_latest_documents()

    assert result_path == output_csv
    assert output_csv.exists()

    rows = read_csv_rows(output_csv)
    rows_by_name = {row["filename"]: row for row in rows}

    assert rows_by_name["old.pdf"]["llm_document_type"] == ""
    assert rows_by_name["old.pdf"]["llm_confidence"] == ""
    assert rows_by_name["latest.pdf"]["llm_document_type"] == "image_based"
    assert rows_by_name["latest.pdf"]["llm_confidence"] == "0.9300"
    assert rows_by_name["latest.pdf"]["llm_reason"] == "Mostly visual pages."
