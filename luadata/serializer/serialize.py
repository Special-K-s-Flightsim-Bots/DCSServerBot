import re


def __serialize(var, encoding, indent, level):
    parts = []
    var_type = type(var)
    if var is None:
        parts.append("nil")
    elif isinstance(var, bool):
        if var:
            parts.append("true")
        else:
            parts.append("false")
    elif isinstance(var, (int, float)):
        parts.append(str(var))
    elif isinstance(var, str):
        parts.append('"')
        parts.append(
            var.encode(encoding)
            .replace(b"\\", b"\\\\")
            .replace(b'"', b'\\"')
            .replace(b"\n", b"\\\n")
            .decode(encoding)
        )
        parts.append('"')
    elif isinstance(var, (list, dict)):
        # calc lua table entries
        entries = []
        if isinstance(var, list):
            for i in range(len(var)):
                entries.append([i + 1, var[i]])
        elif isinstance(var, dict):
            for k in var:
                entries.append([k, var[k]])

        # build lua table parts
        parts.append("{")
        s_tab_equ = "="

        # process indent
        if indent is not None:
            s_tab_equ = " = "
            if len(entries) != 0:
                parts.append("\n")

        # prepare for iterator
        nohash = True
        lastkey = None
        lastval = None
        hasval = False
        for kv in entries:
            key = kv[0]
            val = kv[1]
            # judge if this is a pure list table
            if nohash and (
                not isinstance(key, int)
                or (
                    lastval is None and key != 1
                )  # first loop and index is not 1 : hash table
                or (
                    lastkey is not None and lastkey + 1 != key
                )  # key is not continuously
            ):
                nohash = False
            # process to insert to table
            # insert indent
            if indent is not None:
                parts.append(indent * (level + 1))
            # insert key
            if nohash:  # pure list: do not need a key
                pass
            elif isinstance(key, str) and re.match(
                r"^[a-zA-Z_][a-zA-Z0-9_]*$", key
            ):  # a = val
                parts.append(key)
                parts.append(s_tab_equ)
            else:  # [10010] = val # [".start with or contains special char"] = val
                parts.append("[")
                parts.append(__serialize(key, encoding, indent, level + 1))
                parts.append("]")
                parts.append(s_tab_equ)
            # insert value
            parts.append(__serialize(val, encoding, indent, level + 1))
            parts.append(",")
            if indent is not None:
                parts.append("\n")
            lastkey = key
            lastval = val
            hasval = True

        # remove last `,` if no indent
        if indent is None and hasval:
            parts.pop()

        # insert `}` with indent
        if indent is not None and len(entries) != 0:
            parts.append(indent * level)
        parts.append("}")

    return "".join(parts)


def serialize(var, encoding="utf-8", indent=None, indent_level=0):
    """Serialize variable to lua formatted data string.

    Args:
        var (number, int, float, str, dict, list): variable you want to serialize
        encoding (str, optional): target encoding, will affect string components escaping logic. Defaults to "utf-8".
        indent (str, optional): indent string, such as '\\t'. Defaults to None, means no indention.
        indent_level (int, optional): current indent level. Defaults to 0.

    Returns:
        string: serialized lua formatted data string
    """
    if isinstance(var, tuple):
        res = []
        for item in var:
            res.append(__serialize(item, encoding, indent, indent_level))
        spliter = ","
        if indent is not None:
            spliter = spliter + "\n" + indent * indent_level
        return spliter.join(res)
    return __serialize(var, encoding, indent, indent_level)
