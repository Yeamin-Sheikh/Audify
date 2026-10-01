"""
clean_text -- Converts markdown text to plain speech-ready text.

Strips markdown formatting, extracts code blocks into placeholders for live toggling,
and applies custom pronunciation rules.
"""

from __future__ import annotations

import re
from functools import lru_cache

import markdown
from bs4 import BeautifulSoup


def _unescape_dunders(text: str) -> str:
    """Undo the dunder escaping inside code, where markdown keeps backslashes literally."""
    return text.replace("\\_", "_")


def markdown_to_text(
    md_string: str,
    pronunciation_dict: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Convert a markdown string to plain text suitable for TTS.

    - Replaces fenced code blocks with placeholders `[[CODE_BLOCK_index]]`
    - Strips all markdown formatting via HTML conversion
    - Applies pronunciation dictionary substitutions

    Args:
        md_string: Raw markdown text to convert.
        pronunciation_dict: Optional mapping of words to their spoken equivalents.

    Returns:
        A tuple of (cleaned_text, list_of_extracted_code_blocks).
    """
    if pronunciation_dict is None:
        pronunciation_dict = {}

    code_blocks: list[str] = []

    def code_replacer(match: re.Match) -> str:
        raw_match = match.group(0)
        # Extract code content between ``` and ```.
        # Handles Windows/Linux newlines and language descriptors.
        content = re.sub(r"^```[^\n]*\r?\n", "", raw_match)
        content = re.sub(r"```$", "", content)
        placeholder = f" [[CODE_BLOCK_{len(code_blocks)}]] "
        code_blocks.append("FENCED:" + content.strip())
        return placeholder

    # Extract fenced code blocks first (robust regex that supports any language identifier and CR/LF)
    md_string = re.sub(r"```[^\n]*\r?\n[\s\S]*?```", code_replacer, md_string)

    # Keep Python dunder names (__init__, __main__.py) from being read as bold markers
    md_string = re.sub(r"(?<![\w\\])__(\w+?)__", r"\\_\\_\1\\_\\_", md_string)

    # Convert markdown -> HTML -> plain text
    html: str = markdown.markdown(md_string)
    soup = BeautifulSoup(html, "html.parser")

    # Catch any remaining pre elements first (prevents BS4 parent replacement errors for nested tags)
    for pre in soup.find_all("pre"):
        text_content = _unescape_dunders(pre.get_text())
        if "[[CODE_BLOCK_" not in text_content:
            placeholder = f" [[CODE_BLOCK_{len(code_blocks)}]] "
            code_blocks.append("FENCED:" + text_content.strip())
            pre.replace_with(placeholder)

    # Catch remaining code elements (usually inline)
    for code_element in soup.find_all("code"):
        text_content = _unescape_dunders(code_element.get_text())
        if "[[CODE_BLOCK_" not in text_content:
            placeholder = f" [[CODE_BLOCK_{len(code_blocks)}]] "
            code_blocks.append("INLINE:" + text_content.strip())
            code_element.replace_with(placeholder)

    # Extract text with spaces between tags
    text: str = soup.get_text(separator=" ")

    # Clean up extra newlines and markdown horizontal rules
    text = re.sub(r"\n+", "\n", text)
    text = text.replace("---", "")
    text = text.replace("___", "")

    # Collapse repeated spaces/newlines
    text = re.sub(r"\s+", " ", text).strip()

    text = apply_pronunciations(text, pronunciation_dict)

    return text.strip(), code_blocks

# -- Pronunciation dictionary ------------------------------------------------------


def _case_variants(key: str) -> set[str]:
    """Spellings of ``key`` that should match.

    * ALL-CAPS keys ("API", "IT", ".NET") match exactly, so an acronym never
      hijacks an ordinary word ("IT" must not turn every "it" into "I T").
    * lowercase keys ("async", ".py") also match when capitalised at the start
      of a sentence, but not in ALL CAPS.
    * Mixed-case keys ("GitHub", "IPv4") match any common casing.
    """
    letters = [c for c in key if c.isalpha()]
    if not letters or all(c.isupper() for c in letters):
        return {key}
    if all(c.islower() for c in letters):
        return {key, key[0].upper() + key[1:]}
    return {key, key.lower(), key.upper(), key.capitalize()}


@lru_cache(maxsize=4)
def _compile_rules(rules: tuple[tuple[str, str], ...]) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    lookup: dict[str, str] = {}
    for key, spoken in rules:
        if not key.strip():
            continue
        for variant in _case_variants(key):
            lookup.setdefault(variant, spoken)
    if not lookup:
        return None, lookup

    def with_boundaries(variant: str) -> str:
        # Word-like edges must not sit inside a longer word (".ini" vs ".initialize"),
        # symbol edges ("=>", "&&") need no boundary.
        pattern = re.escape(variant)
        if variant[0].isalnum() or variant[0] == "_":
            pattern = r"(?<![\w])" + pattern
        if variant[-1].isalnum() or variant[-1] == "_":
            pattern += r"(?![\w])"
        return pattern

    # Longest first so "GPT-4o" wins over "GPT-4" and "!==" over "!="
    variants = sorted(lookup, key=len, reverse=True)
    return re.compile("|".join(with_boundaries(v) for v in variants)), lookup


def apply_pronunciations(text: str, pronunciation_dict: dict[str, str] | None) -> str:
    """Replace dictionary words/symbols with their spoken form in a single pass."""
    if not text or not pronunciation_dict:
        return text
    pattern, lookup = _compile_rules(tuple(sorted(pronunciation_dict.items())))
    if pattern is None:
        return text
    return pattern.sub(lambda m: lookup[m.group(0)], text)
