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
#  bind_tools() teaches Claude what tools exist; we run the loop ourselves.
# ══════════════════════════════════════════════════════════════════════════════

RESEARCH_SYSTEM = """You are a professional research assistant. Gather comprehensive,
accurate information on the given topic so a writer can produce high-quality content.

Workflow (follow in order):
1. Search Tavily at least twice — vary queries to cover different angles
2. Use Wikipedia for background context, definitions, or historical facts if relevant
3. Fetch 1-2 of the most relevant URLs from Tavily using fetch_webpage
4. Call get_current_date once so the content stays time-aware

Then output a structured research brief with EXACTLY these section headers:

## KEY FACTS
Bullet list of the most important facts.

## STATISTICS & DATA
Numbers, percentages, study results, and data points.

## MAIN SUBTOPICS
Key angles or sections that should be covered in the final content.

## EXPERT INSIGHTS
Quotes, expert opinions, or authoritative statements found in sources.

## SEO KEYWORDS FOUND
Related terms and phrases that naturally appear across sources.

## SOURCES
List of source titles and URLs used.

Be thorough. Research quality directly determines content quality."""

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

        # No tool calls = Claude has written its final research brief
        if not getattr(response, "tool_calls", None):
            content = response.content
            # Handle rare list-of-blocks format from newer Claude models
            if isinstance(content, list):
                return "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            return str(content)

        # Show progress
        called = [tc["name"] for tc in response.tool_calls]
        print(f"  Step {step + 1}: {called}")

        # Execute every tool call Claude requested
        for tc in response.tool_calls:
            name = tc["name"]
            args = tc["args"]   # dict, e.g. {"query": "..."} or {"url": "..."}

            if name in _tool_map:
                try:
                    result = _tool_map[name].run(args)
                except Exception as e:
                    result = f"Tool error ({name}): {e}"
            else:
                result = f"Unknown tool: {name}"

            # tool_call_id must match exactly — this ties the result to the call
            messages.append(ToolMessage(
                content=str(result),
                tool_call_id=tc["id"],
            ))

    # Max iterations reached — ask for summary of what was found
    final = llm_with_tools.invoke(messages)
    content = getattr(final, "content", str(final))
    return content if isinstance(content, str) else str(content)


# ══════════════════════════════════════════════════════════════════════════════
#  STAGE 2 — WRITER
#  Simple prompt | LLM chain. No tools needed — writer works from research only.
# ══════════════════════════════════════════════════════════════════════════════

writer_prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are an expert SEO content writer specialising in blog posts, articles, and pillar pages.

Writing Rules:
- Use only facts and data from the research brief — never fabricate statistics
- Keywords must appear naturally — zero stuffing, ever
- Heading hierarchy: H1 (title only) → H2 (main sections) → H3 (subsections)
- Paragraphs: 2–4 sentences max
- Vary sentence length for natural reading rhythm
- Write for humans first, search engines second

Output Rules:
- Return clean Markdown only — no preamble, no explanation
- First line: <!-- meta: [compelling 150–160 char meta description] -->
- Then the full H1 title, then the content
- Pillar Pages: add ## Table of Contents after the intro
- Always end with ## Conclusion and a clear call-to-action

Content Type Targets:
- Blog Post:     800–1200 words,   5–6  H2 sections, conversational
- Article:       1200–2000 words,  6–8  H2 sections, informative, data-cited
- Pillar Page:   2500–3500 words,  8–12 H2 sections, comprehensive, TOC required
- Social Snippet: platform-specific, punchy, include 3–5 relevant hashtags""",
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

Write the complete content in Markdown now.""",
    ),
])

writer_chain = writer_prompt | llm


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
    print("         CONTENT GENERATION AGENT  v2")
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
    print(f"  [1/2]  Researching → {topic}")
    print(f"{'─' * 54}\n")

    research_query = (
        f"Research this topic thoroughly for a {content_type} "
        f"targeting the keywords '{keywords}': {topic}"
    )

    try:
        research_text = run_research(research_query)
    except Exception as e:
        print(f"\n  ⚠  Research error: {e}")
        print("  Proceeding with minimal context...\n")
        research_text = f"Topic: {topic}. Keywords: {keywords}."

    # ── Stage 2: Write ─────────────────────────────────
    print(f"\n{'─' * 54}")
    print(f"  [2/2]  Writing {content_type}...")
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

        content = (
            write_result.content
            if hasattr(write_result, "content")
            else str(write_result)
        )

        filepath = save_output(topic, content)

        print("=" * 54)
        print("  ✓  Done!")
        print(f"  →  Saved to: {filepath}")
        print("=" * 54)
        print("\n  ── PREVIEW ───────────────────────────────────────\n")
        print(content[:600])
        print("\n  ... [full content in file above] ...\n")

    except Exception as e:
        print(f"\n  ✗  Writing failed: {e}")
        raise


if __name__ == "__main__":
    main()