import pytest

from prospect import chain_names, classify_website, is_franchise, normalise_name


@pytest.mark.parametrize("url,expected", [
    (None, "none"),
    ("", "none"),
    ("   ", "none"),
    ("https://joesplumbing.ca", "real"),
    ("http://www.joesplumbing.ca/services", "real"),
    ("joesplumbing.ca", "real"),
    ("https://www.facebook.com/joesplumbing", "social"),
    ("https://m.facebook.com/joesplumbing", "social"),
    ("https://instagram.com/joesplumbing", "social"),
    ("https://linktr.ee/joes", "social"),
    ("https://www.yellowpages.ca/bus/Ontario/Newmarket/joes/123.html", "social"),
    ("https://joesplumbing.business.site", "defunct"),
    # the substring trap: a real site that merely mentions a social host
    ("https://facebook-marketing-for-plumbers.ca", "real"),
    ("https://notfacebook.com", "real"),
])
def test_classify_website(url, expected):
    assert classify_website(url) == expected


@pytest.mark.parametrize("name,expected", [
    ("Tim Hortons", True),
    ("TIM HORTONS #4821", True),
    ("Tim Hortons - Yonge St", True),
    ("McDonald's", True),
    ("A&W Restaurant", True),
    ("Mr. Lube", True),
    ("Great Clips", True),
    ("Joe's Plumbing", False),
    ("Newmarket Roofing Co.", False),
    ("", False),
    (None, False),
    # a local shop whose name contains a franchise word but is not one
    ("Tim's Custom Cabinets", False),
])
def test_is_franchise(name, expected):
    assert is_franchise(name) is expected


def test_normalise_name_strips_punctuation_and_case():
    assert normalise_name("McDonald's  #4821!") == "mcdonalds 4821"


def test_chain_names_catches_regional_chains_no_blocklist_knows():
    records = [
        {"place_id": "a", "name": "Newmarket Wings"},
        {"place_id": "b", "name": "Newmarket Wings"},
        {"place_id": "c", "name": "newmarket wings!"},
        {"place_id": "d", "name": "Joe's Plumbing"},
        {"place_id": "e", "name": "Joe's Plumbing"},
    ]
    chains = chain_names(records, min_locations=3)
    assert "newmarket wings" in chains
    assert "joes plumbing" not in chains


def test_chain_names_ignores_the_same_place_seen_in_several_cells():
    # Overlapping cells return the same place repeatedly; that is not a chain.
    records = [{"place_id": "a", "name": "Joe's Plumbing"} for _ in range(5)]
    assert chain_names(records, min_locations=3) == set()
