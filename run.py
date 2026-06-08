"""
Entry point: runs all three scraper phases in sequence.

Usage:
    python run.py

Requires .env with LINKEDIN_EMAIL and LINKEDIN_PASSWORD set.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def run_phase(label: str, module: str):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scraper" / module)],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print(f"Phase failed with exit code {result.returncode}. Stopping.")
        sys.exit(result.returncode)


if __name__ == "__main__":
    run_phase("Phase 1: Discovering Adobe contributors on Sharebird", "discover.py")
    run_phase("Phase 2: Scraping AMA transcripts", "scrape_amas.py")
    run_phase("Phase 3: Annotating and generating output", "annotate.py")

    print("\nAll done!")
    print("  Markdown: output/adobe_pmm_transcripts.md")
    print("  JSON:     data/annotated_transcripts.json")
