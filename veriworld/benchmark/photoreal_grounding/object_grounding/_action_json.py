"""Read the JSON-with-line-comments notation used in grid action examples."""
import json


def load_action_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip only // comments outside strings. No key/value coercion, Python
    # evaluation, trailing-comma repair, or changes to quoted text are allowed.
    cleaned = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        char = text[i]
        if in_string:
            cleaned.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
        elif char == '"':
            in_string = True
            cleaned.append(char)
            i += 1
        elif text[i:i + 2] == "//":
            while i < len(text) and text[i] not in "\r\n":
                i += 1
        else:
            cleaned.append(char)
            i += 1
    return json.loads("".join(cleaned))
