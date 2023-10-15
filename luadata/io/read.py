import codecs
from luadata.serializer.unserialize import unserialize


def read(path, encoding="utf-8", multival=False):
    """Read luadata from file

    Args:
        path (str): file path
        encoding (str, optional): file encoding. Defaults to "utf-8".

    Returns:
        tuple([*]): unserialized data from luadata file
    """
    with codecs.open(path, "r", encoding) as file:
        text = file.read().strip()
        if text[0:6] == "return":
            ch = text[6:7]
            if not (
                ("a" <= ch <= "z")
                or ("A" <= ch <= "Z")
                or ("0" <= ch <= "9")
                or ch == "_"
            ):
                text = text[6:]
        return unserialize(text, encoding=encoding, multival=False)
