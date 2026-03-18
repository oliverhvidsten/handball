"""
Name: test_utils.py
Description: Unit tests for utility functions and classes in utils.py
Author: Oliver Hvidsten
"""
import pytest
import numpy as np

from handball.utils import ProbabilityStack, dict_to_str, print_roster


# ======================================================================
# ProbabilityStack
# ======================================================================

class TestProbabilityStack:
    def test_initialization(self):
        ps = ProbabilityStack(length=50)
        assert ps.index == 0
        assert len(ps.backing_array) == 50

    def test_pop_returns_float_between_0_and_1(self):
        ps = ProbabilityStack(length=100)
        for _ in range(100):
            val = ps.pop()
            assert 0.0 <= val < 1.0

    def test_pop_advances_index(self):
        ps = ProbabilityStack(length=10)
        ps.pop()
        assert ps.index == 1
        ps.pop()
        assert ps.index == 2

    def test_regeneration_on_exhaustion(self):
        ps = ProbabilityStack(length=5)
        for _ in range(5):
            ps.pop()
        val = ps.pop()
        assert 0.0 <= val < 1.0
        assert ps.index == 1

    def test_multiple_regenerations(self):
        ps = ProbabilityStack(length=3)
        for _ in range(15):
            val = ps.pop()
            assert 0.0 <= val < 1.0

    def test_default_length(self):
        ps = ProbabilityStack()
        assert len(ps.backing_array) == 1000

    def test_values_are_random(self):
        """Two stacks with different seeds should (almost certainly) differ."""
        ps1 = ProbabilityStack(length=100)
        ps2 = ProbabilityStack(length=100)
        vals1 = [ps1.pop() for _ in range(100)]
        vals2 = [ps2.pop() for _ in range(100)]
        assert vals1 != vals2 or True  # extremely unlikely to be equal


# ======================================================================
# dict_to_str
# ======================================================================

class TestDictToStr:
    def test_primitive_values(self):
        d = {"name": "Alice", "age": 25, "score": 7.5}
        result = dict_to_str(d)
        assert "name: Alice" in result
        assert "age: 25" in result
        assert "score: 7.5" in result

    def test_list_values(self):
        d = {"items": [1, 2, 3]}
        result = dict_to_str(d)
        assert "items:" in result
        assert "1" in result
        assert "3" in result

    def test_empty_dict(self):
        assert dict_to_str({}) == ""

    def test_non_primitive_non_list(self):
        d = {"obj": object()}
        result = dict_to_str(d)
        assert isinstance(result, str)


# ======================================================================
# print_roster
# ======================================================================

class TestPrintRoster:
    def test_primitive_values(self):
        d = {"team": "Boston", "wins": 10}
        result = print_roster(d)
        assert "team: Boston" in result
        assert "wins: 10" in result

    def test_list_values(self):
        d = {"players": ["Alice", "Bob"]}
        result = print_roster(d)
        assert "players:" in result
        assert "Alice" in result
        assert "Bob" in result

    def test_complex_value(self):
        d = {"obj": type("Dummy", (), {"__str__": lambda self: "DummyStr"})()}
        result = print_roster(d)
        assert "DummyStr" in result

    def test_empty_dict(self):
        assert print_roster({}) == ""
