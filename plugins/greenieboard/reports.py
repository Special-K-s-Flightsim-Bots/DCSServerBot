from core import report
from . import get_element, ERRORS, DISTANCE_MARKS, GRADES


class LSORating(report.EmbedElement):
    def render(self, landing: dict):
        grade = GRADES[landing['grade']].replace('_', '\\_')
        comment = get_element(landing['comment'], 'comment').replace('_', '\\_')
        wire = get_element(landing['comment'], 'wire') or '-'

        self.add_field(name="Date/Time", value=f"{landing['time']:%y-%m-%d %H:%M:%S}")
        self.add_field(name="Plane", value=f"{landing['unit_type']}")
        self.add_field(name="Carrier", value=f"{landing['place']}")

        self.add_field(name="LSO Grade", value=f"{grade}")
        self.add_field(name="Wire", value=f"{wire}")
        self.add_field(name="Points", value=f"{landing['points']}")

        self.add_field(name="LSO Comment", value=f"{comment}", inline=False)

        report.Ruler(self.env).render()

        elements = [e.strip() for e in get_element(landing['comment'], 'comment').split()]
        for mark, text in DISTANCE_MARKS.items():
            comments = ''
            for element in elements:
                if mark in element:
                    little = element.startswith('(')
                    many = element.startswith('_')
                    ignored = element.startswith('[')
                    if little or many or ignored:
                        element = element[1:-1]
                    # don't replace BC as it comes alone
                    if mark != 'BC':
                        element = element.replace(mark, '')
                    comments += '- ' + ERRORS[element] + \
                                (' (a little)' if little else ' (a lot!)' if many else ' (ignored)' if ignored else '') + '\n'
            if len(comments) > 0:
                self.embed.add_field(name=text, value=comments, inline=False)
