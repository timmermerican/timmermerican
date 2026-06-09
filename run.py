"""
Smart orchestrator for the Adobe Sharebird scraper.

Usage:
    python3 run.py                # full run; skips Phase 1 if already discovered
    python3 run.py --refresh      # delete existing transcripts and re-scrape everything
    python3 run.py --annotate-only  # skip scraping; re-annotate existing transcripts only

Phases:
    1. Discovery    — find Adobe contributor AMA URLs (skipped if already done)
    2. Scraping     — pull full Q&A transcripts (with Read More expansion)
    3. Validation   — check for truncation, missing contributors, low counts
    4. Annotation   — flag Firefly/Director+ content, generate output files
    5. Export       — convert to .docx for Google Docs (requires pandoc)
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
MANUAL_DIR = DATA_DIR / "manual"
OUTPUT_DIR = ROOT / "output"
DISCOVERED_PATH = DATA_DIR / "discovered_urls.json"

EXPECTED_CONTRIBUTORS = [
    "Mary Sheehan",
    "Mike Polner",
    "Gagan Mand",
    "Katharine Gregorio",
    "Jeremy Wood",
]


def banner(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def run_module(module_path: str, extra_args: list = None) -> bool:
    cmd = [sys.executable, str(module_path)] + (extra_args or [])
    result = subprocess.run(cmd, cwd=str(ROOT))
    return result.returncode == 0


# ── Phase 1 ──────────────────────────────────────────────────────────────────

def phase1_discover(force: bool) -> bool:
    banner("Phase 1: Discovering Adobe contributors on Sharebird")

    if not force and DISCOVERED_PATH.exists():
        with open(DISCOVERED_PATH) as f:
            existing = json.load(f)
        print(f"  Skipping — {len(existing)} contributors already in discovered_urls.json")
        print(f"  Tip: run with --refresh to re-discover from scratch")
        return True

    return run_module(ROOT / "scraper" / "discover.py")


# ── Phase 2 ──────────────────────────────────────────────────────────────────

def phase2_scrape(refresh: bool) -> bool:
    banner("Phase 2: Scraping AMA transcripts")

    if refresh and TRANSCRIPTS_DIR.exists():
        existing = list(TRANSCRIPTS_DIR.glob("*.json"))
        for f in existing:
            f.unlink()
        print(f"  --refresh: deleted {len(existing)} existing transcript(s)")

    return run_module(ROOT / "scraper" / "scrape_amas.py")


# ── Validation ────────────────────────────────────────────────────────────────

def validate() -> bool:
    banner("Validation")
    warnings = []
    names_found = set()
    total_qas = 0

    # ── QA log (written by scraper, per-answer quality data) ──────────────────
    qa_log_path = DATA_DIR / "scrape_qa.json"
    if qa_log_path.exists():
        with open(qa_log_path) as f:
            qa_log = json.load(f)
        for entry in qa_log:
            if entry.get("read_more_remaining", 0) > 0:
                warnings.append(
                    f"  WARN {entry['name']}: {entry['read_more_remaining']} "
                    f"'Read More' button(s) still unexpanded after scrape"
                )
            for issue in entry.get("issues", []):
                warnings.append(
                    f"  WARN {entry['name']}: {issue['issue']} — {issue['question']}"
                )

    # ── Coverage and count checks (reads transcript files) ───────────────────
    sources = sorted(TRANSCRIPTS_DIR.glob("*.json")) if TRANSCRIPTS_DIR.exists() else []
    if MANUAL_DIR.exists():
        sources += sorted(MANUAL_DIR.glob("*.json"))

    for path in sources:
        try:
            with open(path) as f:
                t = json.load(f)
        except Exception:
            continue
        if t.get("qa_count", 0) == 0:
            continue
        names_found.add(t.get("name", ""))
        total_qas += t.get("qa_count", 0)

    for expected in EXPECTED_CONTRIBUTORS:
        if not any(expected.lower() in n.lower() for n in names_found):
            warnings.append(f"  WARN missing contributor: {expected}")

    if total_qas < 100:
        warnings.append(f"  WARN only {total_qas} Q&As found — expected ≥100")

    if warnings:
        print(f"  {len(warnings)} issue(s) — review before using output:")
        for w in warnings:
            print(w)
        return False
    else:
        print(f"  All checks passed")
        print(f"  {total_qas} Q&As across {len(names_found)} contributors")
        return True


# ── Phase 3 ──────────────────────────────────────────────────────────────────

def phase3_annotate(no_age_filter: bool = False) -> bool:
    banner("Phase 3: Annotating and generating output")
    extra = ["--no-age-filter"] if no_age_filter else []
    return run_module(ROOT / "scraper" / "annotate.py", extra)


# ── Phase 4 ──────────────────────────────────────────────────────────────────

def phase4_export():
    banner("Phase 4: Exporting to Word (.docx)")

    md_path = OUTPUT_DIR / "adobe_pmm_transcripts.md"
    docx_path = OUTPUT_DIR / "adobe_pmm_transcripts.docx"

    if not md_path.exists():
        print(f"  Markdown file not found — skipping export")
        return

    if not shutil.which("pandoc"):
        print(f"  pandoc not installed")
        print(f"  Install it with: brew install pandoc")
        print(f"  Then re-run: python3 run.py --annotate-only")
        return

    result = subprocess.run(["pandoc", str(md_path), "-o", str(docx_path)])
    if result.returncode == 0:
        print(f"  Saved: {docx_path}")
        print()
        print(f"  To open in Google Docs:")
        print(f"    1. Go to drive.google.com")
        print(f"    2. New → File upload → pick adobe_pmm_transcripts.docx")
        print(f"       (it's in your timmermerican/output/ folder)")
        print(f"    3. Right-click the file → Open with Google Docs")
    else:
        print(f"  pandoc failed — try running manually:")
        print(f"  pandoc {md_path} -o {docx_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    refresh = "--refresh" in sys.argv
    annotate_only = "--annotate-only" in sys.argv
    no_age_filter = "--no-age-filter" in sys.argv

    if refresh:
        print("\n  --refresh: will delete existing transcripts and re-scrape everything")
    if annotate_only:
        print("\n  --annotate-only: skipping discovery and scraping")
    if no_age_filter:
        print("\n  --no-age-filter: including all Q&As regardless of age")
    else:
        print("\n  Age filter: excluding Q&As older than 2 years (use --no-age-filter to disable)")

    if not annotate_only:
        ok = phase1_discover(force=refresh)
        if not ok:
            print("\nPhase 1 failed. Fix the error above and re-run.")
            sys.exit(1)

        ok = phase2_scrape(refresh=refresh)
        if not ok:
            print("\nPhase 2 failed. Fix the error above and re-run.")
            sys.exit(1)

    validate()

    ok = phase3_annotate(no_age_filter=no_age_filter)
    if not ok:
        print("\nPhase 3 failed. Fix the error above and re-run.")
        sys.exit(1)

    phase4_export()

    print()
    print("=" * 60)
    print("  Done!")
    print(f"  Word doc: output/adobe_pmm_transcripts.docx")
    print(f"  Markdown: output/adobe_pmm_transcripts.md")
    print(f"  JSON:     data/annotated_transcripts.json")
    print("=" * 60)
