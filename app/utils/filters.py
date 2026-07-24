from datetime import datetime
from num2words import num2words


def parse_date(date_val):
    """
    Parse a date value into a datetime object.

    Accepts:
      - datetime objects (returned as-is)
      - date objects (converted to datetime at midnight)
      - ISO strings (YYYY-MM-DD)
      - Indian format strings (DD/MM/YYYY or DD-MM-YYYY)

    Returns:
        datetime or None if the value cannot be parsed.
    """
    if date_val is None or date_val == '':
        return None
    if isinstance(date_val, datetime):
        return date_val
    if hasattr(date_val, 'year') and not isinstance(date_val, str):
        # date-like object (not a string)
        return datetime.combine(date_val, datetime.min.time())
    date_str = str(date_val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def to_words(number):
    """
    Jinja filter to convert a number (integer or float) to Indian currency word representation.
    e.g., 6025.55 -> 'Six thousand, twenty-five and fifty-five hundredths' or equivalent.
    """
    if number is None or number == '':
        return ""
    try:
        # Check if number has decimal part
        val = float(number)
        if val.is_integer():
            return num2words(int(val), lang='en_IN').capitalize()
        else:
            # Separate rupees and paise if needed
            parts = str(number).split('.')
            rupees = int(parts[0])
            paise = int(parts[1][:2]) if len(parts) > 1 else 0

            rupees_words = num2words(rupees, lang='en_IN').capitalize()
            if paise > 0:
                paise_words = num2words(paise, lang='en_IN')
                return f"{rupees_words} and {paise_words} Paise"
            return rupees_words
    except Exception as e:
        print(f"Filter error in to_words: {e}")
        return str(number)


def format_date_indian(date_val):
    """
    Jinja filter to convert a date string (YYYY-MM-DD, DD/MM/YYYY, etc.) or datetime object
    to Indian DD-MM-YYYY format (e.g. '15-05-2026').
    """
    if not date_val:
        return ""
    if isinstance(date_val, datetime):
        dt = date_val
    else:
        date_str = str(date_val).strip()
        dt = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue
        if not dt:
            return date_val

    return dt.strftime("%d-%m-%Y")
