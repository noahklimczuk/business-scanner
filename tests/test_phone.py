import pytest

from enrich import normalise_phone


@pytest.mark.parametrize("raw", [
    "(905) 555-0123",
    "905-555-0123",
    "905.555.0123",
    "9055550123",
    "+1 905 555 0123",
    "  (905) 555-0123 ext 2  ",
])
def test_canadian_numbers_normalise_to_one_form(raw):
    assert normalise_phone(raw).e164 == "+19055550123"


def test_blank_is_not_an_error():
    for raw in (None, "", "   "):
        info = normalise_phone(raw)
        assert info.e164 is None
        assert info.valid is False


def test_garbage_is_reported_not_raised():
    info = normalise_phone("call the shop")
    assert info.e164 is None
    assert info.valid is False


def test_a_number_that_cannot_dial_is_flagged():
    info = normalise_phone("905-555-012")          # one digit short
    assert info.valid is False


def test_toll_free_is_identified():
    # A 1-800 number usually means a franchise or a lead-gen middleman.
    assert normalise_phone("1-800-555-0199").kind == "toll_free"


def test_canadian_mobiles_are_indistinguishable_from_landlines():
    # Same numbering plan, so nothing can be inferred from the type here and
    # the score must not try. This test exists to stop someone adding that.
    assert normalise_phone("(416) 555-0123").kind == "fixed_or_mobile"


def test_region_is_respected():
    assert normalise_phone("020 7946 0958", region="GB").e164 == "+442079460958"
    assert normalise_phone("020 7946 0958", region="CA").valid is False
