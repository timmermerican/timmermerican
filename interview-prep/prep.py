#!/usr/bin/env python3
"""
Interview prep research tool.

Usage:
  python prep.py "Sarah Chen" "Notion" "Head of PMM"
  python prep.py "Sarah Chen" "Notion" "Head of PMM" --podcast https://youtube.com/watch?v=...
  python prep.py "Sarah Chen" "Notion" "Head of PMM" --out ./briefs/
"""

import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import click
import httpx
from anthropic import Anthropic
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

load_dotenv()

console = Console()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# ── Search ────────────────────────────────────────────────────────────────────

def search(query: str, max_results: int = 5) -> list[dict]:
    """Search via Tavily if TAVILY_API_KEY is set, else fall back to DuckDuckGo."""
    if os.getenv("TAVILY_API_KEY"):
        return _tavily_search(query, max_results)
    return _ddg_search(query, max_results)


def _tavily_search(query: str, max_results: int) -> list[dict]:
    from tavily import TavilyClient
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    r = client.search(query, max_results=max_results)
    return [
        {"url": x["url"], "title": x["title"], "snippet": x.get("content", "")}
        for x in r.get("results", [])
    ]


def _ddg_search(query: str, max_results: int) -> list[dict]:
    from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    return [{"url": r["href"], "title": r["title"], "snippet": r["body"]} for r in results]


# ── Content fetching ──────────────────────────────────────────────────────────

def fetch_page_text(url: str, timeout: int = 10) -> Optional[str]:
    """Fetch a URL and return cleaned body text (up to 8000 chars)."""
    try:
        r = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"},
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)[:8000]
    except Exception:
        return None


# ── Podcast transcript ────────────────────────────────────────────────────────

def load_transcript_file(path: str) -> tuple[Optional[str], str]:
    """Read a pre-extracted .txt transcript from disk (e.g. from Apple Podcasts extractor)."""
    try:
        text = Path(path).read_text(encoding="utf-8")[:12000]
        filename = Path(path).name
        return text, f"Local transcript file ({filename})"
    except Exception as e:
        return None, f"Could not read transcript file {path}: {e}"


def extract_podcast_url(url: str) -> tuple[Optional[str], str]:
    """Extract transcript from a URL. Handles YouTube directly; fetches page text otherwise."""
    youtube_id = _extract_youtube_id(url)
    if youtube_id:
        return _youtube_transcript(youtube_id)
    return _fetch_page_transcript(url)


def _extract_youtube_id(url: str) -> Optional[str]:
    for pattern in [
        r"youtube\.com/watch\?v=([^&]+)",
        r"youtu\.be/([^?/]+)",
        r"youtube\.com/embed/([^?]+)",
    ]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _youtube_transcript(video_id: str) -> tuple[Optional[str], str]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        # 1.x uses instance method + .text attribute (not class method + dict["text"])
        transcript = YouTubeTranscriptApi().fetch(video_id)
        text = " ".join(snippet.text for snippet in transcript)[:12000]
        return text, f"YouTube transcript (id={video_id})"
    except Exception as e:
        return None, f"YouTube transcript unavailable: {e}"


def _fetch_page_transcript(url: str) -> tuple[Optional[str], str]:
    text = fetch_page_text(url)
    if text and len(text) > 500:
        return text, f"Page content from {url}"
    return None, f"Could not extract transcript from {url}"


# ── Source gathering ──────────────────────────────────────────────────────────

class SourceBundle:
    def __init__(self):
        self.sections: list[tuple[str, str]] = []
        self.unavailable: list[str] = []

    def add(self, label: str, text: str):
        self.sections.append((label, text))

    def flag(self, source: str, reason: str):
        self.unavailable.append(f"{source}: {reason}")

    def as_text(self) -> str:
        return "\n\n".join(f"=== {label} ===\n{text}" for label, text in self.sections)


def gather_sources(name: str, company: str, role: str, podcast_url: Optional[str], transcript_file: Optional[str] = None) -> SourceBundle:
    bundle = SourceBundle()

    # 1. Sharebird — highest signal for PMM/GTM practitioners
    console.print("[dim]  Searching Sharebird...[/dim]")
    sb_results = search(f'site:sharebird.com "{name}"', max_results=5)
    if not sb_results:
        sb_results = search(f'site:sharebird.com "{company}" product marketing', max_results=4)
    if sb_results:
        parts = []
        for r in sb_results[:3]:
            page = fetch_page_text(r["url"])
            parts.append(f"[{r['title']}]\n{page or r['snippet']}")
        bundle.add("Sharebird", "\n\n".join(parts))
    else:
        bundle.flag("Sharebird", "no results found")

    # 2. LinkedIn via Google — indexed posts and profile snippets
    console.print("[dim]  Searching LinkedIn (via Google)...[/dim]")
    li_results = search(f'"{name}" "{company}" site:linkedin.com', max_results=4)
    if not li_results:
        li_results = search(f'"{name}" site:linkedin.com', max_results=3)
    if li_results:
        li_text = "\n\n".join(f"[{r['title']}]\n{r['snippet']}" for r in li_results)
        bundle.add("LinkedIn (indexed)", li_text)
    else:
        bundle.flag("LinkedIn", "no indexed results — profile may be private or name ambiguous")

    # 3. Web: interviews, bylines, talks
    console.print("[dim]  Searching for interviews and bylines...[/dim]")
    web_results = search(
        f'"{name}" "{company}" interview OR podcast OR keynote OR byline OR "wrote" OR "authored"',
        max_results=8,
    )
    if web_results:
        parts = []
        for r in web_results[:5]:
            if "linkedin.com" in r["url"] or "sharebird.com" in r["url"]:
                continue
            page = fetch_page_text(r["url"])
            parts.append(f"[{r['title']}] ({r['url']})\n{page or r['snippet']}")
        if parts:
            bundle.add("Web (interviews/bylines)", "\n\n".join(parts))
    if not web_results:
        bundle.flag("Web interviews", "no results found")

    # 4. Podcast transcript
    if transcript_file:
        # Pre-extracted .txt file from Apple Podcasts extractor skill
        console.print(f"[dim]  Loading transcript file...[/dim]")
        transcript, note = load_transcript_file(transcript_file)
        if transcript:
            bundle.add(f"Podcast transcript ({note})", transcript)
        else:
            bundle.flag("Transcript file", note)
    elif podcast_url:
        console.print(f"[dim]  Extracting transcript from podcast URL...[/dim]")
        transcript, note = extract_podcast_url(podcast_url)
        if transcript:
            bundle.add(f"Podcast transcript ({note})", transcript)
        else:
            bundle.flag("Podcast URL", note)
    else:
        # Try to discover a podcast episode
        console.print("[dim]  Searching for podcast appearances...[/dim]")
        pod_results = search(f'"{name}" podcast episode interview', max_results=3)
        if pod_results:
            snippets = "\n\n".join(f"[{r['title']}] {r['url']}\n{r['snippet']}" for r in pod_results)
            bundle.add("Podcast appearances (discovered, no transcript)", snippets)

    return bundle


# ── Synthesis ─────────────────────────────────────────────────────────────────

SYNTHESIS_PROMPT = """\
You are preparing a pre-conversation brief for an interview with {name}, {role} at {company}.

Here is all the raw research gathered from public sources:

{sources}

---

Synthesize this into a structured brief following EXACTLY the template below.
The brief body must be under 500 words total.

INTERVIEW PREP BRIEF
Person: {name} | {role} @ {company}
Sources checked: {sources_checked}
{unavailable_line}
Confidence: [High / Medium / Low — based on depth of primary source material found]

─────────────────────────────────────────

1. THEIR VOCABULARY
5–8 exact words or phrases they use for GTM and PMM concepts. Prioritize direct quotes.
Note any term that diverges from industry defaults — those signal their mental model.

2. PROBLEMS THEY'RE WORKING ON
What are they publicly trying to solve? Separate their personal/career problem from their company's stated problem.

3. CONTRARIAN OR DISTINCTIVE TAKES
Opinions that push against conventional PMM/GTM wisdom.
Mark each with:
★ = genuine differentiator (personal view, specific and arguable)
○ = company talking point (sounds differentiated but is likely polished messaging)

4. 3 CONVERSATION HOOKS
Specific, concrete observations to open with. Format each as:
"[What you noticed] — which suggests [why it matters to them]"

5. EXPERIENCE ANCHOR PROMPTS
Questions to help the reader identify which of their own stories maps to this person's context.
Do NOT fill these in — generate the right questions based on what you found.

─────────────────────────────────────────

RULES:
- No filler or boilerplate. Every sentence earns its place.
- If a source was thin, say so rather than inventing signal.
- If you cannot find genuine differentiating signal, say "Signal was thin — treat as a starting point."
- Do not exceed 500 words in the brief body.\
"""


def synthesize(name: str, company: str, role: str, bundle: SourceBundle) -> str:
    client = Anthropic()

    sources_checked = ", ".join(label for label, _ in bundle.sections) or "none"
    unavailable_line = ""
    if bundle.unavailable:
        unavailable_line = "Sources unavailable: " + "; ".join(bundle.unavailable)

    prompt = SYNTHESIS_PROMPT.format(
        name=name,
        company=company,
        role=role,
        sources=bundle.as_text() or "(no sources retrieved)",
        sources_checked=sources_checked,
        unavailable_line=unavailable_line,
    )

    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ── Output ────────────────────────────────────────────────────────────────────

def save_brief(name: str, brief: str, out_dir: str) -> Path:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\s-]", "", name).strip().lower().replace(" ", "-")
    filename = f"{date.today().isoformat()}-{slug}.md"
    path = Path(out_dir) / filename
    path.write_text(brief, encoding="utf-8")
    return path


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("name")
@click.argument("company")
@click.argument("role")
@click.option("--podcast", default=None, metavar="URL",
              help="YouTube URL to extract transcript from directly")
@click.option("--transcript", default=None, metavar="FILE",
              help="Path to a pre-extracted .txt transcript (from the Apple Podcasts extractor skill)")
@click.option("--out", default="./briefs", show_default=True, metavar="DIR",
              help="Directory to write the markdown brief")
def main(name: str, company: str, role: str, podcast: Optional[str], transcript: Optional[str], out: str):
    """Research a person and generate a pre-conversation interview brief.

    \b
    Examples:
      python prep.py "Sarah Chen" "Notion" "Head of PMM"
      python prep.py "Sarah Chen" "Notion" "Head of PMM" --podcast https://youtu.be/xyz
      python prep.py "Sarah Chen" "Notion" "Head of PMM" --transcript ~/Downloads/episode.txt
      python prep.py "Sarah Chen" "Notion" "Head of PMM" --out ~/notes/briefs
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]Error: ANTHROPIC_API_KEY not set. Add it to .env or export it.[/red]")
        sys.exit(1)

    console.print(Panel.fit(
        f"[bold]{name}[/bold]  ·  {role}  @  [cyan]{company}[/cyan]",
        title="[bold blue]Interview Prep[/bold blue]",
        border_style="blue",
    ))

    console.print("\n[bold]Gathering sources[/bold]")
    bundle = gather_sources(name, company, role, podcast, transcript_file=transcript)

    console.print()
    if bundle.sections:
        console.print(f"[green]✓[/green] {len(bundle.sections)} source(s) retrieved: "
                      + ", ".join(f"[dim]{label}[/dim]" for label, _ in bundle.sections))
    else:
        console.print("[red]No sources retrieved. Check your search API key and internet connection.[/red]")
        sys.exit(1)

    for note in bundle.unavailable:
        console.print(f"[yellow]⚠[/yellow]  {note}")

    console.print("\n[bold]Synthesizing brief...[/bold]")
    brief = synthesize(name, company, role, bundle)

    path = save_brief(name, brief, out)
    console.print(f"[green]✓[/green] Brief saved → [bold]{path}[/bold]\n")

    console.print(Markdown(brief))


if __name__ == "__main__":
    main()
