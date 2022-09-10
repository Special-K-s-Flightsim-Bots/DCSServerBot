import re
from core import report
from . import get_element, ERRORS, DISTANCE_MARKS, GRADES
from .trapsheet import plot_trapsheet, read_trapsheet, parse_filename


class LSORating(report.EmbedElement):
    def render(self, landing: dict):
        grade = GRADES[landing['grade']]
        comment = landing['comment']
        wire = landing['wire']

        self.add_field(name="Date/Time", value=f"{landing['time']:%y-%m-%d %H:%M:%S}")
        self.add_field(name="Plane", value=f"{landing['unit_type']}")
        self.add_field(name="Carrier", value=f"{landing['place']}")

        self.add_field(name="LSO Grade: {}".format(landing['grade'].replace('_', '\\_')), value=grade)
        self.add_field(name="Wire", value=f"{wire}")
        self.add_field(name="Points", value=f"{landing['points']}")

        self.add_field(name="LSO Comment", value=comment.replace('_', '\\_'), inline=False)

        report.Ruler(self.env).render(ruler_length=28)
        # remove unnecessary blanks
        distance_marks = list(DISTANCE_MARKS.keys())
        elements = []
        for element in [e.strip() for e in comment.split()]:
            def merge(s1: str, s2: str):
                if '(' in s1 and '(' in s2:
                    pos = s1.find(')')
                    substr2 = s2[s2.find('(') + 1:s2.find(')')]
                    s1 = s1[:pos] + substr2 + s1[pos:]
                    s2 = s2.replace('(' + substr2 + ')', '')
                if '_' in s1 and '_' in s2:
                    pos = s1.rfind('_')
                    substr2 = s2[s2.find('_') + 1:s2.rfind('_')]
                    s1 = s1[:pos] + substr2 + s1[pos:]
                    s2 = s2.replace('_' + substr2 + '_', '')
                s1 += s2
                return s1

            if len(elements) == 0:
                elements.append(element)
            else:
                if not any(distance in elements[-1] for distance in distance_marks):
                    elements[-1] = merge(elements[-1], element)
                else:
                    elements.append(element)

        for mark, text in DISTANCE_MARKS.items():
            comments = ''
            for element in elements.copy():
                if mark in element:
                    elements.remove(element)
                    if mark != 'BC':
                        element = element.replace(mark, '')

                    def deflate_comment(_element: str) -> list[str]:
                        retval = []
                        while len(_element):
                            for error in ERRORS.keys():
                                if error in _element:
                                    retval.append(ERRORS[error])
                                    _element = _element.replace(error, '')
                                    break
                            else:
                                self.log.error(f'Element {element} not found in LSO mapping!')
                                _element = ''
                        return retval

                    little = re.findall("\((.*?)\)", element)
                    if len(little):
                        for x in little:
                            for y in deflate_comment(x):
                                comments += '- ' + y + ' (a little)\n'
                            element = element.replace(f'({x})', '')
                        if not element:
                            continue
                    many = re.findall("_(.*?)_", element)
                    if len(many):
                        for x in many:
                            for y in deflate_comment(x):
                                comments += '- ' + y + ' (a lot!)\n'
                            element = element.replace(f'_{x}_', '')
                        if not element:
                            continue
                    ignored = re.findall("\[(.*?)\]", element)
                    if len(ignored):
                        for x in ignored:
                            for y in deflate_comment(x):
                                comments += '- ' + y + ' (ignored)\n'
                            element = element.replace(f'[{x}]', '')
                        if not element:
                            continue
                    for y in deflate_comment(element):
                        comments += '- ' + y + '\n'
            if len(comments) > 0:
                self.embed.add_field(name=text, value=comments, inline=False)


class TrapSheet(report.MultiGraphElement):

    def render(self, landing: dict):
        if 'trapsheet' not in landing or not landing['trapsheet']:
            return
        trapsheet = landing['trapsheet']
        ts = read_trapsheet(trapsheet)
        ps = parse_filename(trapsheet)
        plot_trapsheet(self.axes, ts, ps, trapsheet)
