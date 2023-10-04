from typing import Any

__all__ = [
    "ReportException",
    "UnknownReportElement",
    "UnknownGraphElement",
    "ClassNotFound",
    "ValueNotInRange",
    "TooManyElements",
    "UnknownValue",
    "NothingToPlot"
]


class ReportException(BaseException):
    pass


class UnknownReportElement(ReportException):
    def __init__(self, class_name: str):
        super().__init__(f'The class {class_name} is not a ReportElement.')


class UnknownGraphElement(ReportException):
    def __init__(self, class_name: str):
        super().__init__(f'The class {class_name} is not a GraphElement or MultiGraphElement.')


class ClassNotFound(ReportException):
    def __init__(self, class_name: str):
        super().__init__(f'The class {class_name} could not be found.')


class ValueNotInRange(ReportException):
    def __init__(self, name, value: Any, range_: list[Any]):
        super().__init__('Value "{}" of parameter {} is not in the allowed range of [{}]'.format(value, name, ', '.join(
            f'"{x}"' for x in range_)))


class TooManyElements(ReportException):
    def __init__(self, number: int):
        super().__init__(f'The SQL provided returns {number} columns. Allowed is a maximum of 3.')


class UnknownValue(ReportException):
    def __init__(self, name: str, value: str):
        super().__init__(f'The value {value} is unknown for parameter {name}.')


class NothingToPlot(ReportException):
    pass
