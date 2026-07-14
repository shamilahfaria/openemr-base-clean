"""Generate a synthetic lab-report PDF for the demo and integration tests.

Reproducible from the repo (FR-8.7) — no real PHI. Run:
    python fixtures/make_sample_lab.py
"""
from __future__ import annotations

from pathlib import Path

import fitz  # pymupdf

OUT = Path(__file__).resolve().parent / "sample_lab_report.pdf"

ROWS = [
    ("Test", "Result", "Units", "Reference Range", "Flag"),
    ("Hemoglobin A1c", "6.7", "%", "4.0 - 5.6", "HIGH"),
    ("LDL Cholesterol", "160", "mg/dL", "< 100", "HIGH"),
    ("HDL Cholesterol", "38", "mg/dL", "> 40", "LOW"),
    ("Triglycerides", "210", "mg/dL", "< 150", "HIGH"),
    ("Fasting Glucose", "128", "mg/dL", "70 - 99", "HIGH"),
    ("Creatinine", "0.9", "mg/dL", "0.6 - 1.3", "NORMAL"),
]
COLS = [56, 210, 300, 380, 500]


def build() -> None:
    doc = fitz.open()
    page = doc.new_page()  # Letter
    page.insert_text((56, 70), "METRO HEALTH LABORATORIES", fontsize=16, fontname="helv")
    page.insert_text((56, 90), "Comprehensive Metabolic + Lipid Panel", fontsize=11)
    page.insert_text((56, 120), "Patient: DOE, JANE  (SYNTHETIC DEMO)", fontsize=10)
    page.insert_text((56, 135), "MRN: DEMO-000123     DOB: 1958-03-14", fontsize=10)
    page.insert_text((56, 150), "Collected: 2026-07-01     Reported: 2026-07-02", fontsize=10)
    page.draw_line(fitz.Point(56, 165), fitz.Point(556, 165))

    y = 190
    for r, row in enumerate(ROWS):
        font = "hebo" if r == 0 else "helv"
        for x, cell in zip(COLS, row):
            page.insert_text((x, y), cell, fontsize=10, fontname=font)
        y += 22
        if r == 0:
            page.draw_line(fitz.Point(56, y - 16), fitz.Point(556, y - 16))

    page.insert_text((56, y + 24), "Synthetic demo data — not a real patient.", fontsize=8)
    doc.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
