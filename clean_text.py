"""
clean_text -- Converts markdown text to plain speech-ready text.

Strips markdown formatting, replaces code blocks with a spoken summary,
and applies custom pronunciation rules.
"""

from __future__ import annotations

import re

import markdown
from bs4 import BeautifulSoup


def markdown_to_text(
    md_string: str,
    pronunciation_dict: dict[str, str] | None = None,
) -> str:
    """Convert a markdown string to plain text suitable for TTS.

    - Strips all markdown formatting via HTML conversion
    - Replaces code blocks with "[Skipped code block]" instead of reading syntax
    - Applies pronunciation dictionary substitutions (case-insensitive)

    Args:
        md_string: Raw markdown text to convert.
        pronunciation_dict: Optional mapping of words to their spoken equivalents.

    Returns:
        Clean plain text ready for speech synthesis.
    """
    if pronunciation_dict is None:
        pronunciation_dict = {}

    # Convert markdown -> HTML -> plain text
    html: str = markdown.markdown(md_string)
    soup = BeautifulSoup(html, "html.parser")

    # Replace code blocks with a spoken summary instead of raw syntax
    for code_block in soup.find_all(["pre", "code"]):
        code_block.replace_with(" [Skipped code block] ")

    # Extract text with spaces between tags
    text: str = soup.get_text(separator=" ")

    # Clean up extra newlines and markdown horizontal rules
    text = re.sub(r"\n+", "\n", text)
    text = text.replace("---", "")
    text = text.replace("___", "")

    # Apply pronunciation fixes using regex word boundaries
    for bad_word, good_word in pronunciation_dict.items():
        pattern: str = r"\b" + re.escape(bad_word) + r"\b"
        text = re.sub(pattern, good_word, text, flags=re.IGNORECASE)

    return text.strip()
