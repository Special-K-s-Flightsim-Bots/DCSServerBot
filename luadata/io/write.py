import codecs
from luadata.serializer.serialize import serialize


def write(path, data, encoding="utf-8", indent=None, prefix="return "):
    """Write python data to luadata file

    Args:
        path (str): file path to save data
        data (*): any variable that can be saved as luadata format
        encoding (str, optional): file encoding. Defaults to "utf-8".
        indent (str, optional): indent string. Defaults to None.
        prefix (str, optional): prefix string. Defaults to "return ".
    """
    with codecs.open(path, "w", encoding) as file:
        file.write(prefix + serialize(data, encoding=encoding, indent=indent))
