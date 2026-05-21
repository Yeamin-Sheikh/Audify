"""
clean_text -- Converts markdown text to plain speech-ready text.

Strips markdown formatting, extracts code blocks into placeholders for live toggling,
and applies custom pronunciation rules.
"""

from __future__ import annotations

import re

import markdown
from bs4 import BeautifulSoup


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

    # Convert markdown -> HTML -> plain text
    html: str = markdown.markdown(md_string)
    soup = BeautifulSoup(html, "html.parser")

    # Catch any remaining pre elements first (prevents BS4 parent replacement errors for nested tags)
    for pre in soup.find_all("pre"):
        text_content = pre.get_text()
        if "[[CODE_BLOCK_" not in text_content:
            placeholder = f" [[CODE_BLOCK_{len(code_blocks)}]] "
            code_blocks.append("FENCED:" + text_content.strip())
            pre.replace_with(placeholder)

    # Catch remaining code elements (usually inline)
    for code_element in soup.find_all("code"):
        text_content = code_element.get_text()
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

    # Apply pronunciation fixes using regex word boundaries.
    for bad_word, good_word in pronunciation_dict.items():
        if bad_word.isalpha():
            pattern: str = r"\b" + re.escape(bad_word) + r"\b"
            # Use lambda callback to treat the replacement string 100% literally and prevent escape errors
            text = re.sub(pattern, lambda m, gw=good_word: gw, text, flags=re.IGNORECASE)
        else:
            text = text.replace(bad_word, good_word)

    return text.strip(), code_blocks