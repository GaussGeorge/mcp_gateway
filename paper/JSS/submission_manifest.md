# JSS Submission Manifest

This manifest defines the final manuscript-side upload set for the JSS submission and the supplementary files that accompany the artifact statement.

## 1. Main manuscript upload

- `paper/JSS/main.pdf`
- `paper/JSS/main.tex`
- `paper/JSS/figures/*.png`
- `paper/JSS/highlights.txt`

## 2. Supplementary / reviewer-facing files

- `paper/JSS/JSS_SUPPLEMENTARY_ARTIFACT_GUIDE.md`
- `paper/JSS/JSS_FIGURE_TABLE_ALIGNMENT.md`
- `paper/JSS/artifact_availability_statement.txt`
- `paper/JSS/cover_letter_draft.txt`
- `paper/JSS/references_audit.md`
- `paper/JSS/submission_checklist.md`

## 3. Canonical evidence package

- `artifact_results/statistical_summary_v4/`
- `artifact_results/request_level_baseline_v1/`
- `artifact_results/idempotency_only_retry_baseline_v1/`

The canonical numerical source for the manuscript is `artifact_results/statistical_summary_v4/`.

## 4. Script-side replay entry points

- `scripts/validate_jss_artifacts.py`
- `scripts/build_jss_statistical_summary.py`
- `scripts/build_jss_figures.py`

## 5. Final checked properties

- Double-column PDF
- Page count: 18
- Abstract word count: 249
- Highlights: 5 lines, each under 85 characters
- All JSS figures referenced from PNG files
- `validate_jss_artifacts.py`: `errors=[]`
- `statistical_summary_v4/validation.json`: `errors=[]`
- `request_level_baseline_v1/validation.json`: `errors=[]`
- `idempotency_only_retry_baseline_v1/validation.json`: `errors=[]`
- No undefined references/citations or fatal LaTeX errors

## 6. PDF metadata note

The generated PDF currently has no embedded `/Title` or `/Author` metadata fields. This does not affect the manuscript body, which contains the correct author, email, and institution text. If the submission workflow requires embedded PDF metadata, set it during the final camera-ready export rather than changing the JSS source at this stage.
