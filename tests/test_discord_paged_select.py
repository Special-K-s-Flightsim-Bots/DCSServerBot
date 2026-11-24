"""``PagedSelectView``: a multi-select that keeps its ticks across pages.

One select holds 25 options, so a pick over every preset file has to be split into pages without
losing what the operator already ticked. The view keeps the ticks itself and re-paints every page
with ``SelectOption(default=True)``, so a tick is visible again when its page comes back, and
reports its values in LIST order (page order, then option order) because the caller merges in that
order. These tests drive the REAL view callbacks with doubles for Discord's own objects; no live
Discord is involved.
"""
from __future__ import annotations

import asyncio

import pytest

from discord import SelectOption

from core.utils.discord import PagedSelectView


# -------------------------------------------------------------------------------------- doubles

class _Response:
    """The slice of ``InteractionResponse`` the view touches, recording every edit."""

    def __init__(self):
        self.edits: list[dict] = []
        self.defers = 0

    def is_done(self) -> bool:
        return False

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)

    async def defer(self):
        self.defers += 1


class _Interaction:
    def __init__(self):
        self.response = _Response()


# ------------------------------------------------------------------------------------- helpers

def _options(*values) -> list[SelectOption]:
    return [SelectOption(label=value, value=value) for value in values]


async def _tick(view: PagedSelectView, *values) -> _Interaction:
    """Submit a tick on the page the view is showing, as Discord's select would.

    ``_values`` is the select's own selection slot; ``Select.values`` reads it when no interaction
    context has been set, which is exactly what the callback receives in a live pick.
    """
    view.picks._values = list(values)
    interaction = _Interaction()
    await view.picks.callback(interaction)
    return interaction


async def _press(item) -> _Interaction:
    interaction = _Interaction()
    await item.callback(interaction)
    return interaction


def _button(view: PagedSelectView, label: str):
    """The view's button labelled *label*; a missing one is refused with the labels that are drawn."""
    for item in view.children:
        if getattr(item, 'label', None) == label:
            return item
    labels = [getattr(item, 'label', None) or type(item).__name__ for item in view.children]
    raise AssertionError(f'no {label!r} button on the view; it draws {labels}')


def _rows(view: PagedSelectView) -> list[list[str]]:
    """The action rows of the message the view paints, as their visible labels."""
    return [[component.get('label') or component.get('placeholder') or ''
             for component in row['components']]
            for row in view.to_components()]


def _painted(view: PagedSelectView) -> list[tuple[str, str, bool]]:
    """The current page's options as (label, value, default-ticked)."""
    select = view.to_components()[0]['components'][0]
    return [(option['label'], option['value'], option.get('default', False))
            for option in select['options']]


def _select(view: PagedSelectView) -> dict:
    """The select of the message the view paints, as Discord receives it."""
    return view.to_components()[0]['components'][0]


def _assert_discord_accepts(view: PagedSelectView) -> None:
    """The select's bounds must not exceed the options it carries: Discord answers 50035 otherwise."""
    select = _select(view)
    options = select.get('options', [])
    assert select['max_values'] <= len(options), \
        f"Discord refuses max_values={select['max_values']} over {len(options)} options " \
        f"('Must be {select['max_values']} or more in length')"
    assert select['min_values'] <= select['max_values'], \
        f"min_values={select['min_values']} exceeds max_values={select['max_values']}"


# --------------------------------------------------------------------------------------- the pins

@pytest.mark.asyncio
async def test_a_tick_on_one_page_and_a_tick_on_another_come_back_together_in_list_order():
    view = PagedSelectView(pages=[_options('a0', 'a1', 'a2'), _options('b0', 'b1', 'b2')],
                           placeholder='presets', max_values=5)

    await _tick(view, 'a2')
    await _press(view.next_page)
    await _tick(view, 'b2', 'b0')

    # back on page 1 the tick is still there, painted as the option's default
    await _press(view.prev_page)
    assert _painted(view) == [('a0', 'a0', False), ('a1', 'a1', False), ('a2', 'a2', True)]

    # and page 2 kept both of its ticks, in the page's own order rather than click order
    await _press(view.next_page)
    assert _painted(view) == [('b0', 'b0', True), ('b1', 'b1', False), ('b2', 'b2', True)]

    await _press(view.confirm)

    assert view.result == ['a2', 'b0', 'b2']


@pytest.mark.asyncio
async def test_clear_empties_the_ticks_and_the_count_and_ok_then_returns_nothing():
    view = PagedSelectView(pages=[_options('a0', 'a1'), _options('b0')], max_values=3)
    await _tick(view, 'a0', 'a1')
    assert view.page_status.label == 'page 1/2 · 2 chosen'

    cleared = await _press(view.clear_all)

    assert view.page_status.label == 'page 1/2 · 0 chosen', 'the count follows the ticks'
    assert _painted(view) == [('a0', 'a0', False), ('a1', 'a1', False)]
    assert view.is_finished() is False, 'Clear keeps the view open'
    assert cleared.response.edits, 'Clear repaints the message it sits on'

    await _press(view.confirm)

    assert view.result == []


@pytest.mark.asyncio
async def test_the_first_page_disables_the_previous_button_and_the_last_the_next_one():
    view = PagedSelectView(pages=[_options('a0'), _options('b0'), _options('c0')])

    assert view.prev_page.disabled is True, 'there is no page before the first'
    assert view.next_page.disabled is False

    await _press(view.next_page)
    assert view.prev_page.disabled is False
    assert view.next_page.disabled is False
    assert view.page_status.label == 'page 2/3 · 0 chosen'

    await _press(view.next_page)
    assert view.prev_page.disabled is False
    assert view.next_page.disabled is True, 'there is no page past the last'
    assert view.page_status.label == 'page 3/3 · 0 chosen'

    # a press that arrives anyway does not wrap around
    await _press(view.next_page)
    assert view.page_status.label == 'page 3/3 · 0 chosen'
    assert _painted(view) == [('c0', 'c0', False)]


@pytest.mark.asyncio
async def test_a_single_page_draws_no_paging_row():
    view = PagedSelectView(pages=[_options('a0', 'a1')], placeholder='presets', min_values=0)

    assert [len(row) for row in _rows(view)] == [1, 3]
    assert _rows(view)[1] == ['Clear', 'OK', 'Cancel'], \
        'a one-page pick draws no ◀, no page status and no ▶, and still offers Cancel'
    assert view.next_page not in view.children and view.prev_page not in view.children, \
        'the paging buttons are removed from the view, not only hidden'


@pytest.mark.asyncio
async def test_a_cancelled_pick_answers_no_result_and_stays_open_to_no_button():
    view = PagedSelectView(pages=[_options('a0', 'a1'), _options('b0')], max_values=3)
    await _tick(view, 'a0')
    assert view.cancelled is False, 'a fresh pick is not cancelled'

    await _press(_button(view, 'Cancel'))

    assert view.result is None, 'a cancel confirms nothing, exactly like a timeout'
    assert view.cancelled is True, 'and it is the flag, not the result, that tells the two apart'
    assert view.is_finished() is True, 'Cancel closes the pick at once instead of waiting out the timeout'
    assert view.picks.disabled is True and all(item.disabled for item in view.children), \
        'every control is locked, so no button can answer the pick a second time'


@pytest.mark.asyncio
async def test_ok_with_nothing_ticked_answers_an_empty_result_and_is_not_a_cancel():
    view = PagedSelectView(pages=[_options('a0', 'a1')], max_values=3)

    await _press(view.confirm)

    assert view.result == [], 'OK with no tick is a pick of nothing, told apart from a cancel by its list'
    assert view.cancelled is False
    assert view.picks.disabled is True, 'OK locks the controls too'


@pytest.mark.asyncio
async def test_a_page_of_26_options_is_refused_by_the_constructor():
    with pytest.raises(ValueError) as refused:
        PagedSelectView(pages=[_options(*[f'a{i}' for i in range(26)])])

    assert 'holds more than 25 options (26)' in str(refused.value)


@pytest.mark.asyncio
async def test_the_view_times_out_into_an_empty_result_instead_of_raising():
    view = PagedSelectView(pages=[_options('a0'), _options('b0')])
    await _tick(view, 'a0')
    assert view.chosen() == ['a0']

    class _Store:
        def remove_view(self, _view):
            pass

    view.timeout = 0.01
    view._start_listening_from_store(_Store())
    await asyncio.sleep(0.05)

    assert await view.wait() is True, 'the view reported its timeout instead of hanging'
    await asyncio.sleep(0)

    assert view.result is None, 'nothing was confirmed, so the caller sees no pick'
    assert view.cancelled is False, 'a timeout is not a cancel'
    assert all(component.disabled for component in view.children), 'the controls are locked'


@pytest.mark.asyncio
async def test_a_page_shorter_than_the_caller_bound_is_drawn_with_its_own_option_count():
    """A pick over a handful of presets must still send: Discord refuses max_values above the options."""
    view = PagedSelectView(pages=[_options('a0', 'a1', 'a2')], max_values=10)

    _assert_discord_accepts(view)
    assert _select(view)['max_values'] == 3, \
        'the bound follows the page, so Discord accepts the message'

    # the clamp does not take away what the page CAN offer: every option is still tickable
    await _tick(view, 'a0', 'a1', 'a2')
    assert view.chosen() == ['a0', 'a1', 'a2']


@pytest.mark.asyncio
async def test_a_short_last_page_is_valid_when_paged_to_and_keeps_the_earlier_ticks():
    view = PagedSelectView(pages=[_options(*[f'a{i}' for i in range(25)]), _options('b0')],
                           max_values=10)
    _assert_discord_accepts(view)
    assert _select(view)['max_values'] == 10, 'a full page keeps the caller bound'

    await _tick(view, 'a3')
    await _press(view.next_page)

    _assert_discord_accepts(view)
    assert _select(view)['max_values'] == 1, 'the one-option page is drawn with a bound of 1'
    assert view.chosen() == ['a3'], 'paging does not drop the tick taken on the page before'


@pytest.mark.asyncio
async def test_a_bound_inside_the_page_is_kept_as_given():
    view = PagedSelectView(pages=[_options('a0', 'a1', 'a2', 'a3', 'a4')], max_values=2)

    _assert_discord_accepts(view)
    assert _select(view)['max_values'] == 2, 'a caller bound below the page count is not raised'


@pytest.mark.asyncio
async def test_a_min_values_larger_than_a_short_page_is_lowered_to_the_page():
    view = PagedSelectView(pages=[_options('a0'), _options('b0', 'b1')], min_values=2,
                           max_values=2)
    _assert_discord_accepts(view)
    assert _select(view)['min_values'] == 1, 'one option cannot be a floor of two'

    await _press(view.next_page)
    _assert_discord_accepts(view)
    assert _select(view)['min_values'] == 2, 'the floor comes back on the page that can meet it'


@pytest.mark.asyncio
async def test_default_bounds_on_a_one_option_page_stay_inside_the_page():
    view = PagedSelectView(pages=[_options('a0')])

    _assert_discord_accepts(view)
    assert (_select(view)['min_values'], _select(view)['max_values']) == (1, 1)
