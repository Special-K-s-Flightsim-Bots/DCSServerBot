import codecs
from luadata.serializer.unserialize import unserialize


def read(path, encoding="utf-8", _multival=False):
    """
    Read the contents of a file and unserialize it.

    :param path: The path to the file.
    :param encoding: The encoding used to read the file. Defaults to "utf-8".
    :param multival: Flag indicating if the file contains multiple serialized values. Defaults to False.
    :return: The unserialized contents of the file.
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
