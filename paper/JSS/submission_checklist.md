# JSS Submission Checklist

## Manuscript package

- [x] `paper/JSS/main.pdf`
- [x] `paper/JSS/main.tex`
- [x] `paper/JSS/highlights.txt`
- [x] `paper/JSS/figures/*.png`
- [x] Author, email, and institution placeholders removed

## Evidence package

- [x] `artifact_results/statistical_summary_v4/validation.json` reports `errors=[]`
- [x] `artifact_results/request_level_baseline_v1/validation.json` reports `errors=[]`
- [x] `artifact_results/idempotency_only_retry_baseline_v1/validation.json` reports `errors=[]`
- [x] `python scripts/validate_jss_artifacts.py` reports `errors=[]`
- [x] `JSS-C17` present in `statistical_summary_v4/claim_summary.csv`
- [x] `JSS-C18` present in `statistical_summary_v4/claim_summary.csv`

## Layout

- [x] Double-column PDF
- [x] PDF page count = 17
- [x] All JSS figures use PNG files

## Submission follow-up

- [ ] Create GitHub release from `jss-submission-artifacts`
- [ ] Create Zenodo DOI for release
- [ ] Replace branch URL with release/DOI in artifact statement
- [ ] Paste final cover letter into submission system
