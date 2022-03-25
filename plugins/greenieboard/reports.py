from core import report
from . import get_element, ERRORS, DISTANCE_MARKS


class LSORating(report.EmbedElement):
    def render(self, landing: dict):
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
