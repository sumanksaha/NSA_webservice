#!/usr/bin/env python
"""Test script to verify the section suggestion logic for sample adjudication."""

import sys

sys.path.insert(0, r"C:\github\NSA_webservice")

from app.case_file_generator.routes import get_applicable_sections, process_form_data


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

    # Test Case 5: Process form data with both selected
    form_data_5 = {
        "is_substandard": "substandard",
        "is_misbranded": "misbranded",
        "case_number": "TEST/2026/001",
        "product_name": "Test Product",
        "authorization_date": "2026-01-01",
        "inspection_date": "2026-01-02",
        "inspection_time": "12:00",
        "manufacturer_fssai": "1234567890",
        "manufacturer_name": "Test Mfg",
        "manufacturer_fbo_name": "Test Mfg FBO",
        "manufacturer_address": "Test Address",
        "retailer_fssai": "1234567890",
        "retailer_name": "Test Retail",
        "retailer_fbo_name": "Test Retail FBO",
        "retailer_address": "Test Address",
    }
    case_data_5 = process_form_data(form_data_5)
    assert case_data_5["applicable_sections"] == ["51", "52"]
    assert case_data_5["applicable_sections_str"] == "51 and 52"
    assert case_data_5["analysis_result"] == "misbranded and substandard"

    # Test Case 6: Process form data with only substandard
    form_data_6 = {"is_substandard": "substandard", "is_misbranded": ""}
    case_data_6 = process_form_data(form_data_6)
    assert case_data_6["applicable_sections"] == ["51"]
    assert case_data_6["applicable_sections_str"] == "51"
    assert case_data_6["analysis_result"] == "substandard"


if __name__ == "__main__":
    try:
        test_section_logic()
    except AssertionError:
        sys.exit(1)
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
