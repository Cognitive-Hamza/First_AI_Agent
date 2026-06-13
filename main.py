import os
import re
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()  # Must run before tools import — TavilySearch reads env vars at instantiation

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from tools import tavily_search, wiki_tool, jina_tool, date_tool

# ── LLM ────────────────────────────────────────────────────────────────────────
llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0.3)


# ══════════════════════════════════════════════════════════════════════════════
#  STAGE 1 — RESEARCH
#  Manual tool loop using bind_tools — no LangChain agent needed.
# ══════════════════════════════════════════════════════════════════════════════

RESEARCH_SYSTEM = """You are a senior research analyst. Your job is to gather deep,
specific, and publication-ready intelligence on a topic so a writer can produce
content that stands out from everything else on the internet.

WORKFLOW (execute in this order):
1. Search Tavily at least TWICE — use different query angles each time
   (e.g. first: overview/definition angle, second: statistics/data angle,
    third if needed: controversy/debate/comparison angle)
2. Use Wikipedia for foundational background, historical context, or definitions
3. Fetch 1–2 of the most information-rich URLs from Tavily using fetch_webpage
4. Call get_current_date once for time-aware content

Then produce a research brief using EXACTLY these section headers in this order:

## KEY FACTS
The most important, specific, verifiable facts. No vague generalisations.
Each bullet must be a concrete claim, not a category label.

## STATISTICS & DATA
Hard numbers only. For each stat: the figure, the source, and the year.
Format: "- [stat] — [Source], [Year]"

## HOOKS & ANGLES
3 specific content angles that most existing articles on this topic miss or
handle poorly. These should be non-obvious, specific, and genuinely interesting.
These become the writer's competitive edge.

## AUDIENCE QUESTIONS
The real questions this audience types into Google, asks on Reddit/Quora/Stack Overflow.
5–8 specific questions in the audience's own language.

## SPECIFIC EXAMPLES
Real companies, tools, case studies, or scenarios found in research.
Not generic ("many companies use this") — specific ("Netflix uses X to do Y").

## CONTENT GAPS
What is missing or handled poorly in the existing content on this topic?
What would make this piece 10x better than what already exists?

## EXPERT INSIGHTS
Direct quotes or specific positions from named experts, official documentation,
or authoritative reports. Always include the source name.

## SEO KEYWORDS FOUND
Primary keyword + 8–12 semantic/LSI variations that appear naturally in sources.

## SOURCES
Title and URL for every source consulted.

Quality standard: every section must have real content from research — no filler,
no invented facts. If a section has nothing, write "Nothing found — skip this section."
The writer will produce content that is ONLY as good as this brief."""

RESEARCH_TOOLS = [tavily_search, wiki_tool, jina_tool, date_tool]
_tool_map = {t.name: t for t in RESEARCH_TOOLS}


def run_research(query: str) -> str:
    """
    Run the research loop: bind tools to LLM, let Claude decide what to call,
    execute tool calls, feed results back, repeat until Claude stops calling tools.
    """
    llm_with_tools = llm.bind_tools(RESEARCH_TOOLS)

    messages = [
        SystemMessage(content=RESEARCH_SYSTEM),
        HumanMessage(content=query),
    ]

    for step in range(8):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if not getattr(response, "tool_calls", None):
            content = response.content
            if isinstance(content, list):
                return "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            return str(content)

        called = [tc["name"] for tc in response.tool_calls]
        print(f"  Step {step + 1}: {called}")

        for tc in response.tool_calls:
            name = tc["name"]
            args = tc["args"]

            if name in _tool_map:
                try:
                    result = _tool_map[name].run(args)
                except Exception as e:
                    result = f"Tool error ({name}): {e}"
            else:
                result = f"Unknown tool: {name}"

            messages.append(ToolMessage(
                content=str(result),
                tool_call_id=tc["id"],
            ))

    final = llm_with_tools.invoke(messages)
    content = getattr(final, "content", str(final))
    return content if isinstance(content, str) else str(content)


# ══════════════════════════════════════════════════════════════════════════════
#  STAGE 2 — WRITER
#  Prompt | LLM chain. No tools — writer works purely from the research brief.
# ══════════════════════════════════════════════════════════════════════════════

writer_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are an expert content writer for a top-tier digital publication.
You write for humans first. You write content that is specific, useful, and impossible to skim past.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BANNED — NEVER USE THESE UNDER ANY CIRCUMSTANCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Banned openers (rewrite completely if tempted):
  ✗ "If you're a [role], you've probably..."
  ✗ "In today's fast-paced world..."
  ✗ "In this article, we will explore..."
  ✗ "Whether you're a X or a Y..."
  ✗ "Have you ever wondered..."
  ✗ "It's no secret that..."

Banned phrases anywhere in the body:
  ✗ "It's worth noting that..."
  ✗ "Needless to say..."
  ✗ "In conclusion" (use the ## Conclusion heading instead)
  ✗ "In summary" (use ## Key Takeaways if needed)
  ✗ "As we can see..."
  ✗ "This is important because..."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WRITING RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Opener rule:
  The first 2 sentences must earn the reader's attention with ONE of:
  (a) A surprising or counterintuitive statistic + its real-world meaning
  (b) A specific scenario the audience recognises from their own life
  (c) A direct provocation or challenge to a common assumption

Stat interpretation rule (mandatory):
  Every statistic must be followed immediately by a "so what" sentence.
  BAD:  "CI/CD reduces deployment cycles by 50%."
  GOOD: "CI/CD reduces deployment cycles by 50% — a team that ships weekly
         starts shipping twice a week. That compounds to 52 extra releases a year."

Bold usage rule:
  Maximum 2–3 bold terms per H2 section.
  Bold only: genuine technical terms, proper nouns of key tools/products.
  Never bold: adjectives, general nouns, mid-sentence emphasis, obvious words.

H2 title rule:
  Every H2 must pass this test: "Does this title tell the reader something
  specific they couldn't have assumed before reading?"
  BANNED H2 patterns: "Why X Matters", "The Importance of X",
  "Benefits of X", "Introduction to X", "What is X?" (move to intro instead)
  GOOD H2 pattern: specific outcome + how/why/when

Data & facts rule:
  Use only facts from the research brief. Never invent statistics.
  When citing data, always name the source inline: "According to Gartner (2024)..."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTENT TYPE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Blog Post (800–1200 words):
  - Hook lands within first 2 sentences — no warm-up
  - 5–6 H2 sections maximum
  - Conversational but credible — like a smart friend explaining something
  - End each H2 section with a "so what" for the reader
  - CTA must be one specific, concrete action (not "learn more" or "get started")

Article (1200–2000 words):
  - Every major claim cites a source from the research brief
  - Add ## Key Takeaways section (5–7 bullets) before ## Conclusion
  - Use > blockquote for the single most important statistic or quote
  - More formal tone — closer to journalism than blogging

Pillar Page (2500–3500 words):
  - ## Table of Contents is mandatory, placed after the intro paragraph
  - Each H2 section must be self-contained — a reader should understand it
    without reading the rest
  - Add ## Quick Reference or ## Cheat Sheet section near the end
  - Add ## Frequently Asked Questions with 4–6 questions from the research brief
  - Most comprehensive treatment of the topic possible

Social Snippet (platform-specific):
  LinkedIn:   150–200 words, line break every 1–2 sentences,
              strong standalone first line, end with a question or CTA
  Twitter/X:  240–280 chars max, punchy, 1–2 hashtags only
  Instagram:  Short caption (50–100 words) + 5 hashtags on a new line

Technical topics (any type):
  If the topic involves code, configuration, or CLI commands:
  - Include at least one working code/config example (10–20 lines)
  - Use correct language identifier in code blocks: ```yaml, ```python, ```bash
  - Follow every code block with 2–3 sentences explaining what it does

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Return clean Markdown only — no preamble, no meta-commentary
- First line: <!-- meta: [compelling 150–160 char meta description] -->
- Then the H1 title, then the content
- Heading hierarchy: H1 (title only) → H2 (main sections) → H3 (subsections)
- Paragraphs: 2–4 sentences max, never longer
- Vary sentence length deliberately for rhythm""",
    ),
    (
        "human",
        """RESEARCH BRIEF:
{research}

---

CONTENT SPECIFICATIONS:
- Topic:           {topic}
- Content Type:    {content_type}
- Target Keywords: {keywords}
- Target Audience: {audience}
- Tone:            {tone}
- Word Count:      {word_count}

Use the HOOKS & ANGLES and CONTENT GAPS from the research brief to make this
piece distinctly better than what already exists on this topic.

Write the complete content in Markdown now.""",
    ),
])

writer_chain = writer_prompt | llm


# ══════════════════════════════════════════════════════════════════════════════
#  STAGE 3 — REFINEMENT
#  Senior editor pass. Audits the draft against an 8-point checklist and
#  rewrites the full article. This is what pushes 8.5/10 to 9.5/10.
# ══════════════════════════════════════════════════════════════════════════════

refinement_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are the senior editor at a top digital publication.
A writer has submitted a draft. Your job is to audit it against 8 quality criteria
and rewrite the COMPLETE article fixing every issue found.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8-POINT EDITORIAL CHECKLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. OPENER QUALITY
   Does the opening line hook the reader within 2 sentences?
   If it uses any of these patterns — rewrite it completely:
   "If you're a [role]..." / "In today's world..." / "Have you ever wondered..."
   / "Whether you're X or Y..." / "It's no secret..."
   Replace with: surprising stat + interpretation, specific scenario,
   or direct challenge to a common assumption.

2. STAT INTERPRETATION
   Find every bare statistic in the draft.
   Each one must be followed immediately by a practical "so what" sentence.
   If any stat stands alone without interpretation — add it.

3. BOLD OVERUSE
   Count bold usage per H2 section. If more than 3 bolded terms exist in any
   section, strip the least essential ones. Bold is for key technical terms only —
   never for adjectives, general nouns, or mid-sentence emphasis.

4. H2 TITLE QUALITY
   Read every H2. Replace any that matches these banned patterns:
   "Why X Matters", "The Importance of X", "Benefits of X", "Introduction to X"
   Rewrite to be specific and benefit-led — tell the reader an outcome, not a topic.

5. CODE / CONCRETE EXAMPLES (applies to technical topics)
   If the topic involves tools, code, or configuration AND no code block exists —
   add one working example (10–20 lines, correct language identifier).
   Add 2–3 explanatory sentences after it.

6. COMPARISON CALLOUT (add if missing and relevant)
   If the topic has widely-known alternatives, add a brief comparison section.
   Keep it tight: a small table or 3–4 bullet points, under 150 words total.

7. LEARNING PATH (applies when audience is students or learners)
   If the audience is learning-oriented and no "where to start" section exists,
   add: ## Your First 30 Days: A Learning Path
   4–6 concrete steps with specific free resources (not generic "read the docs").

8. CTA SPECIFICITY
   The call-to-action must name ONE specific first action.
   Replace generic CTAs ("get started", "learn more", "explore today") with
   something concrete: "Run your first Azure Pipeline in 20 minutes using
   Microsoft Learn's free sandbox — search 'Azure Pipelines quickstart'."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Return ONLY the complete rewritten article in Markdown
- Keep the <!-- meta: ... --> line at the top, improve it if weak
- No commentary before or after — just the article
- Do not summarise what you changed
- The rewrite must be complete — not a partial edit with placeholders""",
    ),
    (
        "human",
        """DRAFT TO ELEVATE:
{draft}

---
TARGET AUDIENCE: {audience}
CONTENT TYPE:    {content_type}
KEYWORDS:        {keywords}

Audit against all 8 criteria. Rewrite the complete article now.""",
    ),
])

refinement_chain = refinement_prompt | llm


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

CONTENT_TYPES = {
    "1": ("Blog Post",      "800–1200 words"),
    "2": ("Article",        "1200–2000 words"),
    "3": ("Pillar Page",    "2500–3500 words"),
    "4": ("Social Snippet", "150–280 characters"),
}

TONES = {
    "1": "Professional",
    "2": "Conversational",
    "3": "Educational",
    "4": "Persuasive",
}

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:50]

def save_output(topic: str, content: str) -> str:
    os.makedirs("outputs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"outputs/{slugify(topic)}_{timestamp}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    return filename


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 54)
    print("         CONTENT GENERATION AGENT  v3")
    print("=" * 54)

    topic    = input("\n  Topic: ").strip()
    keywords = input("  Target keywords (comma-separated): ").strip()
    audience = input("  Target audience [general readers]: ").strip() or "general readers"

    print("\n  Content Type:")
    for k, (name, wc) in CONTENT_TYPES.items():
        print(f"    {k}.  {name:<16} ({wc})")
    ct_choice = input("  Choose (1–4) [1]: ").strip() or "1"
    content_type, word_count = CONTENT_TYPES.get(ct_choice, CONTENT_TYPES["1"])

    print("\n  Tone:")
    for k, name in TONES.items():
        print(f"    {k}.  {name}")
    tone_choice = input("  Choose (1–4) [1]: ").strip() or "1"
    tone = TONES.get(tone_choice, "Professional")

    # ── Stage 1: Research ──────────────────────────────
    print(f"\n{'─' * 54}")
    print(f"  [1/3]  Researching → {topic}")
    print(f"{'─' * 54}\n")

    research_query = (
        f"Research this topic thoroughly for a {content_type} "
        f"targeting the keywords '{keywords}' "
        f"for this audience: {audience}. Topic: {topic}"
    )

    try:
        research_text = run_research(research_query)
    except Exception as e:
        print(f"\n  ⚠  Research error: {e}")
        print("  Proceeding with minimal context...\n")
        research_text = f"Topic: {topic}. Keywords: {keywords}."

    # ── Stage 2: Write ─────────────────────────────────
    print(f"\n{'─' * 54}")
    print(f"  [2/3]  Writing {content_type}...")
    print(f"{'─' * 54}\n")

    try:
        write_result = writer_chain.invoke({
            "research":     research_text,
            "topic":        topic,
            "content_type": content_type,
            "keywords":     keywords,
            "audience":     audience,
            "tone":         tone,
            "word_count":   word_count,
        })

        draft = (
            write_result.content
            if hasattr(write_result, "content")
            else str(write_result)
        )

    except Exception as e:
        print(f"\n  ✗  Writing failed: {e}")
        raise

    # ── Stage 3: Refine ────────────────────────────────
    print(f"\n{'─' * 54}")
    print(f"  [3/3]  Refining and elevating...")
    print(f"{'─' * 54}\n")

    try:
        refined_result = refinement_chain.invoke({
            "draft":        draft,
            "audience":     audience,
            "content_type": content_type,
            "keywords":     keywords,
        })

        content = (
            refined_result.content
            if hasattr(refined_result, "content")
            else str(refined_result)
        )

    except Exception as e:
        print(f"\n  ⚠  Refinement failed — saving draft instead: {e}")
        content = draft

    filepath = save_output(topic, content)

    print("=" * 54)
    print("  ✓  Done!")
    print(f"  →  Saved to: {filepath}")
    print("=" * 54)
    print("\n  ── PREVIEW ───────────────────────────────────────\n")
    print(content[:600])
    print("\n  ... [full content in file above] ...\n")


if __name__ == "__main__":
    main()