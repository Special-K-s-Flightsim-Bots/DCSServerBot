import math


def unserialize(raw, encoding="utf-8", multival=False, verbose=False):
    """Unserialize stringified lua data to python data

    Args:
        raw (str): raw lua data string
        encoding (str, optional): string encoding. Defaults to "utf-8".
        multival (bool, optional): returns tuple for supporting multiple lua values likes "return 1, 2". Defaults to False.
        verbose (bool, optional): show more verbose debug information. Defaults to False.

    Raises:
        Exception: unserialize errors

    Returns:
        tuple([*]): unserialized data
    """
    sbins = raw.encode(encoding)
    root = {"entries": [], "lualen": 0, "is_root": True}
    node = root
    stack = []
    state = "SEEK_CHILD"
    pos = 0
    slen = len(sbins)
    byte_quoting_char = None
    key = None
    escaping = False
    comment = None
    component_name = None
    errmsg = None

    def sorter(kv):
        if isinstance(kv[0], int):
            return kv[0]
        return math.inf

    def node_entries_append(node, key, val):
        node["entries"].append([key, val])
        node["entries"].sort(key=sorter)
        lualen = 0
        for kv in node["entries"]:
            if kv[0] == lualen + 1:
                lualen = lualen + 1
        node["lualen"] = lualen

    def node_to_table(node):
        if len(node["entries"]) == node["lualen"]:
            lst = []
            for kv in node["entries"]:
                lst.append(kv[1])
            return lst
        else:
            dct = {}
            for kv in node["entries"]:
                dct[kv[0]] = kv[1]
            return dct

    while pos <= slen:
        byte_current = None
        byte_current_is_space = False
        if pos < slen:
            byte_current = sbins[pos: pos + 1]
            byte_current_is_space = (
                byte_current == b" "
                or byte_current == b"\r"
                or byte_current == b"\n"
                or byte_current == b"\t"
            )
        if verbose:
            print("[step] pos", pos, byte_current, state, comment, key, node)

        if comment == "MULTILINE":
            if byte_current == b"]" and sbins[pos: pos + 2] == b"]]":
                comment = None
                pos = pos + 1
        elif comment == "INLINE":
            if byte_current == b"\n":
                comment = None
        elif state == "SEEK_CHILD":
            if byte_current is None:
                break
            if byte_current == b"-" and sbins[pos: pos + 4] == b"--[[":
                comment = "MULTILINE"
                pos = pos + 3
            elif byte_current == b"-" and sbins[pos: pos + 2] == b"--":
                comment = "INLINE"
                pos = pos + 1
            elif not node["is_root"] and (
                (b"A" <= byte_current <= b"Z")
                or (b"a" <= byte_current <= b"z")
                or byte_current == b"_"
            ):
                state = "KEY_SIMPLE"
                pos1 = pos
            elif not node["is_root"] and byte_current == b"[":
                state = "KEY_EXPRESSION_OPEN"
            elif byte_current == b"}":
                if len(stack) == 0:
                    errmsg = (
                        "unexpected table closing, no matching opening braces found."
                    )
                    break
                prev_env = stack.pop()
                if prev_env["state"] == "KEY_EXPRESSION_OPEN":
                    key = node_to_table(node)
                    state = "KEY_END"
                elif prev_env["state"] == "VALUE":
                    node_entries_append(
                        prev_env["node"],
                        prev_env["key"],
                        node_to_table(node),
                    )
                    state = "VALUE_END"
                    key = None
                node = prev_env["node"]
            elif not byte_current_is_space:
                key = node["lualen"] + 1
                state = "VALUE"
                pos = pos - 1
        elif state == "VALUE":
            if byte_current is None:
                errmsg = "unexpected empty value."
                break
            elif byte_current == b"-" and sbins[pos: pos + 4] == b"--[[":
                comment = "MULTILINE"
                pos = pos + 3
            elif byte_current == b"-" and sbins[pos: pos + 2] == b"--":
                comment = "INLINE"
                pos = pos + 1
            elif byte_current == b'"' or byte_current == b"'":
                state = "TEXT"
                component_name = "VALUE"
                pos1 = pos + 1
                byte_quoting_char = byte_current
            elif byte_current == b"-" or (b"0" <= byte_current <= b"9"):
                state = "INT"
                component_name = "VALUE"
                pos1 = pos
            elif byte_current == b".":
                state = "FLOAT"
                component_name = "VALUE"
                pos1 = pos
            elif byte_current == b"t" and sbins[pos: pos + 4] == b"true":
                node_entries_append(node, key, True)
                state = "VALUE_END"
                key = None
                pos = pos + 3
            elif byte_current == b"f" and sbins[pos: pos + 5] == b"false":
                node_entries_append(node, key, False)
                state = "VALUE_END"
                key = None
                pos = pos + 4
            elif byte_current == b"{":
                stack.append({"node": node, "state": state, "key": key})
                state = "SEEK_CHILD"
                node = {"entries": [], "lualen": 0, "is_root": False}
        elif state == "TEXT":
            if byte_current is None:
                errmsg = "unexpected string ending: missing close quote."
                break
            if escaping:
                escaping = False
            elif byte_current == b"\\":
                escaping = True
            elif byte_current == byte_quoting_char:
                data = (
                    sbins[pos1:pos]
                    .replace(b"\\\n", b"\n")
                    .replace(b'\\"', b'"')
                    .replace(b"\\\\", b"\\")
                    .decode(encoding)
                )
                if component_name == "KEY":
                    key = data
                    state = "KEY_EXPRESSION_FINISH"
                elif component_name == "VALUE":
                    node_entries_append(node, key, data)
                    state = "VALUE_END"
                    key = None
        elif state == "INT":
            if byte_current == b"." or byte_current == b"e":
                state = "FLOAT"
            elif byte_current is None or byte_current < b"0" or byte_current > b"9":
                data = int(sbins[pos1:pos].decode(encoding))
                if component_name == "KEY":
                    key = data
                    state = "KEY_EXPRESSION_FINISH"
                    pos = pos - 1
                elif component_name == "VALUE":
                    node_entries_append(node, key, data)
                    state = "VALUE_END"
                    key = None
                    pos = pos - 1
        elif state == "FLOAT":
            if byte_current == b"e" or byte_current == b"-" or byte_current == b"+":
                pass
            elif byte_current is None or byte_current < b"0" or byte_current > b"9":
                if pos == pos1 + 1 and sbins[pos1:pos] == b".":
                    errmsg = "unexpected dot."
                    break
                else:
                    data = float(sbins[pos1:pos].decode(encoding))
                    if component_name == "KEY":
                        key = data
                        state = "KEY_EXPRESSION_FINISH"
                        pos = pos - 1
                    elif component_name == "VALUE":
                        node_entries_append(node, key, data)
                        state = "VALUE_END"
                        key = None
                        pos = pos - 1
        elif state == "VALUE_END":
            if byte_current is None:
                pass
            elif byte_current == b"-" and sbins[pos: pos + 4] == b"--[[":
                comment = "MULTILINE"
                pos = pos + 3
            elif byte_current == b"-" and sbins[pos: pos + 2] == b"--":
                comment = "INLINE"
                pos = pos + 1
            elif byte_current == b",":
                state = "SEEK_CHILD"
            elif byte_current == b"}":
                state = "SEEK_CHILD"
                pos = pos - 1
            elif not byte_current_is_space:
                errmsg = "unexpected character."
                break
        elif state == "KEY_EXPRESSION_OPEN":
            if byte_current is None:
                errmsg = "key expression expected."
                break
            if byte_current == b"-" and sbins[pos: pos + 4] == b"--[[":
                comment = "MULTILINE"
                pos = pos + 3
            elif byte_current == b"-" and sbins[pos: pos + 2] == b"--":
                comment = "INLINE"
                pos = pos + 1
            elif byte_current == b'"' or byte_current == b"'":
                state = "TEXT"
                component_name = "KEY"
                pos1 = pos + 1
                byte_quoting_char = byte_current
            elif byte_current == b"-" or (
                    b"0" <= byte_current <= b"9"
            ):
                state = "INT"
                component_name = "KEY"
                pos1 = pos
            elif byte_current == b".":
                state = "FLOAT"
                component_name = "KEY"
                pos1 = pos
            elif byte_current == b"t" and sbins[pos: pos + 4] == b"true":
                errmsg = "python do not support bool as dict key."
                break
            elif byte_current == b"f" and sbins[pos: pos + 5] == b"false":
                errmsg = "python do not support bool variable as dict key."
                break
            elif byte_current == b"{":
                errmsg = "python do not support lua table variable as dict key."
                break
        elif state == "KEY_EXPRESSION_FINISH":
            if byte_current is None:
                errmsg = 'unexpected end of table key expression, "]" expected.'
                break
            if byte_current == b"-" and sbins[pos: pos + 4] == b"--[[":
                comment = "MULTILINE"
                pos = pos + 3
            elif byte_current == b"-" and sbins[pos: pos + 2] == b"--":
                comment = "INLINE"
                pos = pos + 1
            elif byte_current == b"]":
                state = "KEY_EXPRESSION_CLOSE"
            elif not byte_current_is_space:
                errmsg = 'unexpected character, "]" expected.'
                break
        elif state == "KEY_EXPRESSION_CLOSE":
            if byte_current == b"=":
                state = "VALUE"
            elif byte_current == b"-" and sbins[pos: pos + 4] == b"--[[":
                comment = "MULTILINE"
                pos = pos + 3
            elif byte_current == b"-" and sbins[pos: pos + 2] == b"--":
                comment = "INLINE"
                pos = pos + 1
            elif not byte_current_is_space:
                errmsg = 'unexpected character, "=" expected.'
                break
        elif state == "KEY_SIMPLE":
            if not (
                (b"A" <= byte_current <= b"Z")
                or (b"a" <= byte_current <= b"z")
                or (b"0" <= byte_current <= b"9")
                or byte_current == b"_"
            ):
                key = sbins[pos1:pos].decode(encoding)
                state = "KEY_SIMPLE_END"
                pos = pos - 1
        elif state == "KEY_SIMPLE_END":
            if byte_current_is_space:
                pass
            elif byte_current == b"-" and sbins[pos: pos + 4] == b"--[[":
                comment = "MULTILINE"
                pos = pos + 3
            elif byte_current == b"-" and sbins[pos: pos + 2] == b"--":
                comment = "INLINE"
                pos = pos + 1
            elif byte_current == b"=":
                state = "VALUE"
            elif byte_current == b"," or byte_current == b"}":
                if key == "true":
                    node_entries_append(node, node["lualen"] + 1, True)
                    state = "VALUE_END"
                    key = None
                    pos = pos - 1
                elif key == "false":
                    node_entries_append(node, node["lualen"] + 1, False)
                    state = "VALUE_END"
                    key = None
                    pos = pos - 1
                else:
                    key = None
                    errmsg = "invalid table simple key character."
                    break
        pos += 1
        if verbose:
            print("          ", pos, "    ", state, comment, key, node)

    # check if there is any errors
    if errmsg is None and len(stack) != 0:
        errmsg = 'unexpected end of table, "}" expected.'
    if errmsg is None and root["lualen"] == 0:
        errmsg = "nothing can be unserialized from input string."
    if errmsg is not None:
        pos = min(pos, slen)
        start_pos = max(0, pos - 4)
        end_pos = min(pos + 10, slen)
        err_parts = sbins[start_pos:end_pos].decode(encoding)
        err_indent = " " * (pos - start_pos)
        raise Exception(f"Unserialize luadata failed on pos {pos}:\n    {err_parts}\n    {err_indent}^\n    {errmsg}")

    res = []
    for kv in root["entries"]:
        res.append(kv[1])
    if multival:
        return tuple(res)
    return res[0]
