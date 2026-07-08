from pathlib import Path
from llm_classifier import LLMClassifier


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent
    csv_path = base_dir / "./output/pdf_inventory.csv"

    # Adjust this if your PDFs live in a different folder.
    pdf_root = base_dir

    classifier = LLMClassifier(
        csv_path=csv_path,
        pdf_root=pdf_root,
    )
    output_path = classifier.classify_latest_documents()
