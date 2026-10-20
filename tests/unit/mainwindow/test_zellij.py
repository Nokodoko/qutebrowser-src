# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the zellij layer on top of tab tiling: names, cap, one stack."""

import pytest

from qutebrowser.mainwindow import tiling


class TestNames:

    def test_default_name(self):
        assert tiling.default_name(3) == "Tab #3"

    @pytest.mark.parametrize('mask, tag', [
        (0b1, 1), (0b100, 3), (0b110, 2), (0, 0), (tiling.TAGMASK, 1),
    ])
    def test_lowest_tag(self, mask, tag):
        assert tiling.lowest_tag(mask) == tag


class TestCap:

    # four tabs on tag 1, one on tag 2, one on tags 1+3
    tags = [0b1, 0b1, 0b1, 0b1, 0b10, 0b101]

    def test_count_on(self):
        assert tiling.count_on(1, self.tags) == 5
        assert tiling.count_on(2, self.tags) == 1
        assert tiling.count_on(3, self.tags) == 1
        assert tiling.count_on(9, self.tags) == 0

    def test_full_tag_refuses(self):
        # a tab on tag 2 wants to join tag 1, which holds 5 already
        assert tiling.tags_over_limit(0b1, 0b10, self.tags, 5) == [1]

    def test_already_on_tag_is_not_joining(self):
        # moving a tab that is on tag 1 to tags 1+2: only tag 2 is joined
        assert tiling.tags_over_limit(0b11, 0b1, self.tags, 5) == []

    def test_limit_off(self):
        assert tiling.tags_over_limit(0b1, 0, self.tags, 0) == []

    def test_next_tag_with_room_skips_full(self):
        assert tiling.next_tag_with_room(1, self.tags, 5) == 2

    def test_next_tag_with_room_wraps(self):
        full = [0b100000000] * 5  # tag 9 full
        assert tiling.next_tag_with_room(9, full, 5) == 1

    def test_next_tag_with_room_all_full_stays(self):
        full = [tiling.TAGMASK] * 5
        assert tiling.next_tag_with_room(4, full, 5) == 4

    def test_next_tag_with_room_limit_off(self):
        assert tiling.next_tag_with_room(1, self.tags, 0) == 1


class TestStack:

    # bar order: a(1) b(2) c(1) d(3) e(1+2)
    a, b, c, d, e = 'a', 'b', 'c', 'd', 'e'
    tabs = [(a, 0b1), (b, 0b10), (c, 0b1), (d, 0b100), (e, 0b11)]

    def test_stack_order_groups_by_tag(self):
        assert tiling.stack_order(self.tabs) == [
            (self.a, 1), (self.c, 1), (self.e, 1),
            (self.b, 2), (self.e, 2),
            (self.d, 3),
        ]

    def test_step_within_tag(self):
        assert tiling.step_focus(self.tabs, self.a, 0b1, +1) == (self.c, 1)
        assert tiling.step_focus(self.tabs, self.c, 0b1, -1) == (self.a, 1)

    def test_step_past_end_enters_next_tag(self):
        # e is the last pane of tag 1: next is the first pane of tag 2
        assert tiling.step_focus(self.tabs, self.e, 0b1, +1) == (self.b, 2)

    def test_step_before_start_enters_previous_tag(self):
        assert tiling.step_focus(self.tabs, self.a, 0b1, -1) == (self.d, 3)

    def test_step_wraps_at_the_end_of_the_stack(self):
        assert tiling.step_focus(self.tabs, self.d, 0b100, +1) == (self.a, 1)

    def test_multi_tag_tab_is_located_at_its_viewed_tag(self):
        # viewing tag 2, e sits at (e, 2): next is d on tag 3
        assert tiling.step_focus(self.tabs, self.e, 0b10, +1) == (self.d, 3)

    def test_empty(self):
        assert tiling.step_focus([], None, 0b1, +1) is None
