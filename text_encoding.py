"""Conservative repairs for known export damage; never guess customer names."""
import re


def repair_text(value):
    if not isinstance(value, str):
        return value
    # Repair reversible UTF-8 decoded as Windows-1252/Latin-1, word by word.
    def decode_word(match):
        word = match.group()
        for _ in range(2):
            if not any(c in word for c in ('Ã', 'Â', 'â')):
                break
            for encoding in ('cp1252', 'latin1'):
                try:
                    fixed = word.encode(encoding).decode('utf-8')
                except (UnicodeError, LookupError):
                    continue
                word = fixed
                break
            else:
                break
        return word
    value = re.sub(r'\S+', decode_word, value)
    # These are verified business category labels. Unknown '?' stay untouched.
    for broken, fixed in [('PIZZER??A', 'PIZZERÍA'), ('CAF??', 'CAFÉ'), ('EDUCACI??N', 'EDUCACIÓN')]:
        value = re.sub(r'(?<!\w)' + re.escape(broken) + r'(?!\w)', fixed, value)
    return value


def repair_values(value):
    if isinstance(value, dict):
        return {k: repair_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [repair_values(v) for v in value]
    return repair_text(value)
