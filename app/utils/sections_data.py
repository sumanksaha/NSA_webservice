import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# fss_sections.md is in the workspace root, two levels up from app/utils/
SECTION_MD_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "fss_sections.md"))


def load_sections(path: str = SECTION_MD_PATH) -> dict:
    """Parses a markdown file structured with level-1 headers of the form
    '# Section NN' into a dict: {"55": "full section text...", ...}.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"FSS sections file not found at: {path}")

    with open(path, encoding="utf-8") as f:
        text = f.read()

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


SECTIONS = load_sections()
VALID_SECTION_IDS = {"55", "56", "58", "63", "64"}
