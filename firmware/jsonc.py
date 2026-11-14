"""Vendor Pilot JSON with // line comments; strings and line numbers are preserved."""
import json


def strip_line_comments(text):
    """Remove // comments outside JSON strings; keep newlines for error positions."""
    out = []
    i, size = 0, len(text)
    in_string = False
    while i < size:
        char = text[i]
        if in_string:
            out.append(char)
            if char == '\\' and i+1 < size:
                out.append(text[i+1])
                i += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            out.append(char)
        elif char == '/' and text.startswith('//', i):
            end = text.find('\n', i)
            i = size if end < 0 else end
            continue
        else:
            out.append(char)
        i += 1
    return ''.join(out)


def loads(data):
    return json.loads(strip_line_comments(data.decode('utf-8')))
