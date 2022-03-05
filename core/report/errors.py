from typing import List, Any


class UnknownReportElement(Exception):
    def __init__(self, classname: str):
        super().__init__(f'The class {classname} is not a ReportElement.')


class UnknownGraphElement(Exception):
    def __init__(self, classname: str):
        super().__init__(f'The class {classname} is not a GraphElement or MultiGraphElement.')


class ClassNotFound(Exception):
    def __init__(self, classname: str):
        super().__init__(f'The class {classname} could not be found.')


class ValueNotInRange(Exception):
    def __init__(self, name, value: Any, range_: List[Any]):
        super().__init__('Value "{}" of parameter {} is not in the allowed range of [{}]'.format(value, name, ', '.join(
            f'"{x}"' for x in range_)))


class TooManyElements(Exception):
    def __init__(self, number: int):
        super().__init__(f'The SQL provided returns {number} columns. Allowed is a maximum of 3.')
