from datetime import datetime

from num2words import num2words


def _to_datetime(date_val):
    """Coerce *date_val* to a datetime, stripping any time component.

    Accepts datetime/date objects, ISO strings (``YYYY-MM-DD`` optionally
    followed by ``THH:MM:SS[.ffffff][+HH:MM]`` or a space separator), and
    Indian strings (``DD/MM/YYYY``, ``DD-MM-YYYY``, optionally with a time
    suffix). Returns None when the value cannot be parsed.
    """
    if date_val is None or date_val == "":
        return None
    if isinstance(date_val, datetime):
        return date_val
    if hasattr(date_val, "year") and not isinstance(date_val, str):
        # date-like object (not a string)
        return datetime.combine(date_val, datetime.min.time())
    date_str = str(date_val).strip()
    if not date_str:
        return None
    # Fast path: ISO 8601 via fromisoformat (handles 'T'/space separator,
    # microseconds, and timezone offsets). fromisoformat does not accept a
    # trailing 'Z', so normalise it first.
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        pass
    # Fall back to explicit formats, including datetime variants whose time
    # part must be stripped so produced documents render DD-MM-YYYY only.
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%YT%H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%d-%m-%YT%H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%YT%H.%M.%S",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def parse_date(date_val):
    """Parse a date value into a datetime object.

    Accepts:
      - datetime objects (returned as-is)
      - date objects (converted to datetime at midnight)
      - ISO strings (YYYY-MM-DD, optionally with a time/timezone suffix)
      - Indian format strings (DD/MM/YYYY or DD-MM-YYYY, optionally with
        a time suffix such as ``T00:00:00``)

    Returns:
        datetime or None if the value cannot be parsed.

    """
    return _to_datetime(date_val)


def to_words(number):
    """Jinja filter to convert a number (integer or float) to Indian currency word representation.
    e.g., 6025.55 -> 'Six thousand, twenty-five and fifty-five hundredths' or equivalent.
    """
    if number is None or number == "":
        return ""

    try:
        # Sanitize input: strip commas, spaces, currency symbols before converting
        number_str = str(number).replace(",", "").replace(" ", "").replace("₹", "").strip()
        val = float(number_str)

        if val.is_integer():
            return num2words(int(val), lang="en_IN").capitalize()
        # Separate rupees and paise if needed
        parts = number_str.split(".")
        rupees = int(parts[0])
        paise = int(parts[1][:2]) if len(parts) > 1 else 0

        rupees_words = num2words(rupees, lang="en_IN").capitalize()
        if paise > 0:
            paise_words = num2words(paise, lang="en_IN")
            return f"{rupees_words} and {paise_words} Paise"
        return rupees_words
    except (ValueError, TypeError):
        return str(number) if number else ""


def format_date_indian(date_val):
    """Jinja filter to convert a date string or datetime object
    to Indian DD-MM-YYYY format (e.g. '15-05-2026').

    Any time component (``T00:00:00``, `` 00:00:00``, timezone, ...) is
    stripped so produced documents render the date only. Unparseable
    values are returned unchanged.
    """
    if not date_val:
        return ""
    dt = _to_datetime(date_val)
    if not dt:
        return date_val

    return dt.strftime("%d-%m-%Y")
