#!/usr/bin/env python
"""Test script to verify the section suggestion logic for sample adjudication."""

import sys

sys.path.insert(0, r"C:\github\NSA_webservice")

from app.case_file_generator.routes import get_applicable_sections


def test_section_logic():
    """Test the get_applicable_sections function with various inputs."""

    # Test Case 1: Only substandard
    form_data_1 = {"is_substandard": "substandard", "is_misbranded": ""}
    sections_1 = get_applicable_sections(form_data_1)
    assert sections_1 == ["51"], f"Expected ['51'], got {sections_1}"

    # Test Case 2: Only misbranded
    form_data_2 = {"is_substandard": "", "is_misbranded": "misbranded"}
    sections_2 = get_applicable_sections(form_data_2)
    assert sections_2 == ["52"], f"Expected ['52'], got {sections_2}"

    # Test Case 3: Both substandard and misbranded
    form_data_3 = {"is_substandard": "substandard", "is_misbranded": "misbranded"}
    sections_3 = get_applicable_sections(form_data_3)
    assert sections_3 == ["51", "52"], f"Expected ['51', '52'], got {sections_3}"

    # Test Case 4: Neither selected
    form_data_4 = {"is_substandard": "", "is_misbranded": ""}
    sections_4 = get_applicable_sections(form_data_4)
    assert sections_4 == [], f"Expected [], got {sections_4}"


if __name__ == "__main__":
    try:
        test_section_logic()
    except AssertionError:
        sys.exit(1)
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
