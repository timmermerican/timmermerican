"""
Phase 3: Annotate all transcripts with flags and generate output files.

Reads all data/transcripts/*.json, adds:
  - firefly_related: bool
  - director_level: bool
  - topic_tags: list[str]

Writes:
  - data/annotated_transcripts.json  (full JSON archive)
  - output/adobe_pmm_transcripts.md  (Google Docs-friendly Markdown)
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scraper.utils import DATA_DIR, OUTPUT_DIR

TRANSCRIPTS_DIR = DATA_DIR / "transcripts"

FIREFLY_PATTERNS = re.compile(
    r"\b(firefly|generative[- ]?ai|gen[- ]?ai|genai|text[- ]to[- ]image|"
    r"ai[- ]art|creative[- ]cloud[- ]ai|image[- ]generation|diffusion|"
    r"midjourney|dall[- ]?e|stable[- ]diffusion|ai[- ]content|"
    r"ai[- ]generated|foundation model)\b",
    re.IGNORECASE,
)

DIRECTOR_PATTERNS = re.compile(
    r"\b(director|vp|vice president|senior director|sr\.? director|"
    r"head of|gm|general manager|chief marketing|cmo|svp|evp)\b",
    re.IGNORECASE,
)

TOPIC_RULES = {
    "positioning": r"\b(position|messaging|narrative|value prop|differentia)\b",
    "product_launch": r"\b(launch|gtm|go-to-market|announce|release)\b",
    "roadmap": r"\b(roadmap|strategy|priorit|planning|OKR|vision)\b",
    "team_building": r"\b(team|hire|hiring|headcount|org|structure|PMM team)\b",
    "sales_enablement": r"\b(sales|enablement|battlecard|pitch|prospect|revenue)\b",
    "research": r"\b(research|customer|interview|insight|survey|win.loss)\b",
    "metrics": r"\b(metric|kpi|measure|success|attribution|pipeline|ARR|MRR)\b",
    "competitive": r"\b(competitor|competitive|differentiat|versus|vs\.)\b",
    "pricing": r"\b(price|pricing|package|tier|freemium|monetiz)\b",
    "career": r"\b(career|interview|resume|job|transition|promot|salary|hire me)\b",
}

TOPIC_COMPILED = {tag: re.compile(pattern, re.IGNORECASE) for tag, pattern in TOPIC_RULES.items()}


def parse_age_years(timestamp: str) -> float | None:
    """Return approximate age in years from a timestamp string, or None if unparseable."""
    if not timestamp:
        return None
    m = re.search(r'(\d+)\s+year', timestamp, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r'(\d+)\s+month', timestamp, re.I)
    if m:
        return float(m.group(1)) / 12
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            dt = datetime.strptime(timestamp[:len(fmt)], fmt).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).days / 365.25
        except ValueError:
            continue
    return None


def is_too_old(timestamp: str, max_years: float) -> bool:
    age = parse_age_years(timestamp)
    return age is not None and age > max_years


def is_firefly_related(text: str) -> bool:
    return bool(FIREFLY_PATTERNS.search(text))


def is_director_level(title: str) -> bool:
    return bool(DIRECTOR_PATTERNS.search(title))


def get_topic_tags(text: str) -> list[str]:
    return [tag for tag, pat in TOPIC_COMPILED.items() if pat.search(text)]


def annotate_transcript(transcript: dict, max_age_years: float = 2.0) -> dict:
    t = transcript.copy()
    t["director_level"] = is_director_level(t.get("title", ""))

    annotated_qas = []
    skipped_old = 0
    for qa in t.get("qas", []):
        if max_age_years and is_too_old(qa.get("timestamp", ""), max_age_years):
            skipped_old += 1
            continue
        combined = f"{qa.get('question', '')} {qa.get('answer', '')}"
        qa = qa.copy()
        qa["firefly_related"] = is_firefly_related(combined)
        qa["topic_tags"] = get_topic_tags(combined)
        annotated_qas.append(qa)

    if skipped_old:
        print(f"    Filtered {skipped_old} Q&A(s) older than {max_age_years:.0f} years")
    t["qas"] = annotated_qas
    t["qa_count"] = len(annotated_qas)
    t["firefly_qa_count"] = sum(1 for qa in annotated_qas if qa["firefly_related"])
    return t


def render_markdown(transcripts: list[dict]) -> str:
    lines = [
        "# Adobe PMM/PM Transcripts — Sharebird",
        "",
        "> Scraped from Sharebird. All content included; "
        "`[Firefly]` marks Firefly/GenAI-related answers, "
        "`[Director+]` marks Director-or-above contributors.",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
    ]

    for t in transcripts:
        name = t.get("name", "Unknown")
        slug = t.get("slug", "")
        badge = " `[Director+]`" if t.get("director_level") else ""
        lines.append(f"- [{name}{badge}](#{slug})")

    lines += ["", "---", ""]

    for t in transcripts:
        name = t.get("name", "Unknown")
        title = t.get("title", "")
        slug = t.get("slug", "")
        url = t.get("ama_url", "")
        note = t.get("note", "")
        director_badge = " `[Director+]`" if t.get("director_level") else ""
        firefly_count = t.get("firefly_qa_count", 0)
        qa_count = t.get("qa_count", 0)
        paywalled = t.get("paywalled", False)

        lines += [
            f'<a name="{slug}"></a>',
            f"## {name}{director_badge}",
            f"**{title}**  ",
            f"[View on Sharebird]({url})",
            "",
        ]

        if note:
            lines += [f"> {note}", ""]

        meta_parts = [f"{qa_count} Q&As"]
        if firefly_count:
            meta_parts.append(f"{firefly_count} Firefly-related")
        if paywalled:
            meta_parts.append("partial — paywalled")
        lines += [f"*{' · '.join(meta_parts)}*", "", "---", ""]

        if not t.get("qas"):
            lines += ["*(No Q&As extracted — page may be fully paywalled or empty)*", "", ""]
            continue

        for i, qa in enumerate(t["qas"], 1):
            q = qa.get("question", "").strip()
            a = qa.get("answer", "").strip()
            tags = qa.get("topic_tags", [])
            firefly = qa.get("firefly_related", False)
            pw = qa.get("paywalled", False)

            if not q and not a:
                continue

            tag_str = ""
            if firefly:
                tag_str += " `[Firefly]`"
            if tags:
                tag_str += " " + " ".join(f"`{t}`" for t in tags)

            lines.append(f"### Q{i}{tag_str}")
            if q:
                lines += [f"**Q:** {q}", ""]
            if a:
                lines += [f"**A:** {a}", ""]
            elif pw:
                lines += ["**A:** *(paywalled — upgrade to view)*", ""]
            lines.append("")

        lines += ["---", ""]

    return "\n".join(lines)


def main():
    import sys as _sys
    no_age_filter = "--no-age-filter" in _sys.argv
    max_age_years = None if no_age_filter else 2.0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    transcript_files = sorted(TRANSCRIPTS_DIR.glob("*.json"))
    manual_dir = DATA_DIR / "manual"
    if manual_dir.exists():
        transcript_files = list(transcript_files) + sorted(manual_dir.glob("*.json"))

    if not transcript_files:
        print("No transcript files found. Run scrape_amas.py first.")
        return

    age_note = "no age filter" if no_age_filter else "≤2 years old"
    print(f"Annotating {len(transcript_files)} transcripts ({age_note})...")
    annotated = []
    for path in transcript_files:
        with open(path) as f:
            t = json.load(f)
        if t.get("qa_count", 0) == 0:
            print(f"  Skipping {path.name} (0 Q&As)")
            continue
        annotated.append(annotate_transcript(t, max_age_years=max_age_years))

    # Write JSON archive
    json_out = DATA_DIR / "annotated_transcripts.json"
    with open(json_out, "w") as f:
        json.dump(annotated, f, indent=2)
    print(f"JSON saved: {json_out}")

    # Write Markdown
    md_out = OUTPUT_DIR / "adobe_pmm_transcripts.md"
    md = render_markdown(annotated)
    with open(md_out, "w") as f:
        f.write(md)
    print(f"Markdown saved: {md_out}")

    # Print summary
    total_qas = sum(t.get("qa_count", 0) for t in annotated)
    total_firefly = sum(t.get("firefly_qa_count", 0) for t in annotated)
    director_count = sum(1 for t in annotated if t.get("director_level"))
    print(f"\nSummary:")
    print(f"  Contributors: {len(annotated)}")
    print(f"  Director+: {director_count}")
    print(f"  Total Q&As: {total_qas}")
    print(f"  Firefly-related Q&As: {total_firefly}")


if __name__ == "__main__":
    main()
