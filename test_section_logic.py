#!/usr/bin/env python
"""Test script to verify the section suggestion logic for sample adjudication."""

import sys
sys.path.insert(0, r'C:\github\NSA_webservice')

from app.case_file_generator.routes import get_applicable_sections, process_form_data


def test_section_logic():
    """Test the get_applicable_sections function with various inputs."""
    
    print("Testing Section Suggestion Logic for Sample Adjudication")
    print("=" * 60)
    
    # Test Case 1: Only substandard
    form_data_1 = {'is_substandard': 'substandard', 'is_misbranded': ''}
    sections_1 = get_applicable_sections(form_data_1)
    print(f"\nTest 1 - Only Substandard:")
    print(f"  Input: is_substandard='substandard', is_misbranded=''")
    print(f"  Expected: ['51']")
    print(f"  Result: {sections_1}")
    assert sections_1 == ['51'], f"Expected ['51'], got {sections_1}"
    print("  ✓ PASSED")
    
    # Test Case 2: Only misbranded
    form_data_2 = {'is_substandard': '', 'is_misbranded': 'misbranded'}
    sections_2 = get_applicable_sections(form_data_2)
    print(f"\nTest 2 - Only Misbranded:")
    print(f"  Input: is_substandard='', is_misbranded='misbranded'")
    print(f"  Expected: ['52']")
    print(f"  Result: {sections_2}")
    assert sections_2 == ['52'], f"Expected ['52'], got {sections_2}"
    print("  ✓ PASSED")
    
    # Test Case 3: Both substandard and misbranded
    form_data_3 = {'is_substandard': 'substandard', 'is_misbranded': 'misbranded'}
    sections_3 = get_applicable_sections(form_data_3)
    print(f"\nTest 3 - Both Substandard and Misbranded:")
    print(f"  Input: is_substandard='substandard', is_misbranded='misbranded'")
    print(f"  Expected: ['51', '52']")
    print(f"  Result: {sections_3}")
    assert sections_3 == ['51', '52'], f"Expected ['51', '52'], got {sections_3}"
    print("  ✓ PASSED")
    
    # Test Case 4: Neither selected
    form_data_4 = {'is_substandard': '', 'is_misbranded': ''}
    sections_4 = get_applicable_sections(form_data_4)
    print(f"\nTest 4 - Neither Selected:")
    print(f"  Input: is_substandard='', is_misbranded=''")
    print(f"  Expected: []")
    print(f"  Result: {sections_4}")
    assert sections_4 == [], f"Expected [], got {sections_4}"
    print("  ✓ PASSED")
    
    # Test Case 5: Process form data with both selected
    form_data_5 = {
        'is_substandard': 'substandard',
        'is_misbranded': 'misbranded',
        'case_number': 'TEST/2026/001',
        'product_name': 'Test Product',
        'authorization_date': '2026-01-01',
        'inspection_date': '2026-01-02',
        'inspection_time': '12:00',
        'manufacturer_fssai': '1234567890',
        'manufacturer_name': 'Test Mfg',
        'manufacturer_fbo_name': 'Test Mfg FBO',
        'manufacturer_address': 'Test Address',
        'retailer_fssai': '1234567890',
        'retailer_name': 'Test Retail',
        'retailer_fbo_name': 'Test Retail FBO',
        'retailer_address': 'Test Address',
    }
    case_data_5 = process_form_data(form_data_5)
    print(f"\nTest 5 - Process Form Data (Both Selected):")
    print(f"  Input: is_substandard='substandard', is_misbranded='misbranded'")
    print(f"  Expected applicable_sections: ['51', '52']")
    print(f"  Result applicable_sections: {case_data_5['applicable_sections']}")
    assert case_data_5['applicable_sections'] == ['51', '52']
    print(f"  Expected applicable_sections_str: '51 and 52'")
    print(f"  Result applicable_sections_str: '{case_data_5['applicable_sections_str']}'")
    assert case_data_5['applicable_sections_str'] == '51 and 52'
    print(f"  Expected analysis_result: 'misbranded and substandard'")
    print(f"  Result analysis_result: '{case_data_5['analysis_result']}'")
    assert case_data_5['analysis_result'] == 'misbranded and substandard'
    print("  ✓ PASSED")
    
    # Test Case 6: Process form data with only substandard
    form_data_6 = {'is_substandard': 'substandard', 'is_misbranded': ''}
    case_data_6 = process_form_data(form_data_6)
    print(f"\nTest 6 - Process Form Data (Only Substandard):")
    print(f"  Input: is_substandard='substandard', is_misbranded=''")
    print(f"  Result applicable_sections: {case_data_6['applicable_sections']}")
    assert case_data_6['applicable_sections'] == ['51']
    print(f"  Result applicable_sections_str: '{case_data_6['applicable_sections_str']}'")
    assert case_data_6['applicable_sections_str'] == '51'
    print(f"  Result analysis_result: '{case_data_6['analysis_result']}'")
    assert case_data_6['analysis_result'] == 'substandard'
    print("  ✓ PASSED")
    
    print("\n" + "=" * 60)
    print("All tests passed! ✓")
    print("\nSection Logic Summary:")
    print("  - Substandard sample → Section 51")
    print("  - Misbranded sample → Section 52")
    print("  - Both conditions → Sections 51 and 52")
    print("  - Sections will be inserted into both Petition and Permission Letter")


if __name__ == '__main__':
    try:
        test_section_logic()
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
