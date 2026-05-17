import markdown
# pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup
import re

def markdown_to_text(md_string, pronunciation_dict=None):
    if pronunciation_dict is None:
        pronunciation_dict = {}

    """
    Converts markdown string to plain text.
    Removes markdown formatting and skips code blocks entirely.
    """
    # Convert markdown to html
    html = markdown.markdown(md_string)
    soup = BeautifulSoup(html, "html.parser")
    
    # Replace code blocks with a spoken summary instead of silently deleting them
    for code_block in soup.find_all(['pre', 'code']):
        code_block.replace_with(" [Skipped code block] ")
        
    # Extract text with spaces between tags
    text = soup.get_text(separator=' ')
    
    # Additional cleanups: remove extra newlines and markdown line breaks
    text = re.sub(r'\n+', '\n', text)
    text = text.replace('---', '')
    text = text.replace('___', '')
    
    # Apply pronunciation fixes using regex for word boundaries
    for bad_word, good_word in pronunciation_dict.items():
        pattern = r"\b" + re.escape(bad_word) + r"\b"
        text = re.sub(pattern, good_word, text, flags=re.IGNORECASE)
    
    return text.strip()
