"""sections_data.py

Loads the FSS Act section reference text (Sections 55, 56, 58, 63, 64) from a
markdown file and exposes it as a dict keyed by section number. Used by
suggester.py to ground the LangChain suggestion chain in the exact statutory
text, and by app.py to validate/render section content.
"""

import re

SECTION_MD_PATH = "fss_sections.md"


def load_sections(path: str = SECTION_MD_PATH) -> dict:
    """Parses a markdown file structured with level-1 headers of the form
    '# Section NN' into a dict: {"55": "full section text...", ...}.

    Raises FileNotFoundError if the markdown file is missing, and
    ValueError if no sections could be parsed (e.g. wrong file / bad format).
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()

    # Split on level-1 headers (# Section NN), keeping the header attached
    # to its following content.
    chunks = re.split(r"\n(?=# Section \d+)", text)

    sections = {}
    for chunk in chunks:
        match = re.match(r"# Section (\d+)", chunk.strip())
        if match:
            sections[match.group(1)] = chunk.strip()

    if not sections:
        raise ValueError(
            f"No sections parsed from '{path}'. Check the file exists and uses '# Section NN' level-1 headers.",
        )

    return sections


# Loaded once at import time. If fss_sections.md is missing or malformed,
# this will raise immediately on Flask startup rather than failing silently
# at request time.
SECTIONS = load_sections()

# The only section numbers the officer-facing UI and the AI suggester are
# allowed to work with. Anything outside this set returned by the LLM or
# submitted via the form is dropped server-side.
VALID_SECTION_IDS = {"55", "56", "58", "63", "64"}
