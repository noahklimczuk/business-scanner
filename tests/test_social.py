"""Matching a business to a social page. A false positive here puts a
stranger's Facebook page in front of an owner mid-pitch, so most of these
tests are about refusing to match."""
import pytest

from enrich import (SOCIAL_THRESHOLD, clean_social_url, emails_in, pick_socials,
                    social_confidence, social_handle, tokens_of)
from search import SearchHit, parse_ddg_html, unwrap

CITY = "Newmarket, ON"


# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url,expected", [
    ("https://www.facebook.com/JoesPlumbing", ("facebook", "JoesPlumbing")),
    ("https://facebook.com/JoesPlumbing/", ("facebook", "JoesPlumbing")),
    ("https://m.facebook.com/JoesPlumbing", ("facebook", "JoesPlumbing")),
    ("https://www.facebook.com/pages/Joes-Plumbing/123456", ("facebook", "Joes-Plumbing")),
    ("https://www.facebook.com/p/Joes-Plumbing-100064", ("facebook", "Joes-Plumbing-100064")),
    ("https://www.instagram.com/joesplumbing/", ("instagram", "joesplumbing")),
    ("https://www.facebook.com/JoesPlumbing?ref=page_internal",
     ("facebook", "JoesPlumbing")),
])
def test_social_handle_extracts_the_page(url, expected):
    assert social_handle(url) == expected


@pytest.mark.parametrize("url", [
    "https://www.facebook.com/login",
    "https://www.facebook.com/groups/newmarketbuysell",
    "https://www.facebook.com/marketplace/item/123",
    "https://www.facebook.com/watch/?v=123",
    "https://www.facebook.com/",
    "https://www.instagram.com/p/Cabc123/",
    "https://www.instagram.com/explore/tags/plumbing/",
    "https://www.instagram.com/accounts/login/",
    "https://joesplumbing.ca/about",
    "https://www.yelp.ca/biz/joes-plumbing",
])
def test_social_handle_rejects_everything_that_is_not_a_page(url):
    assert social_handle(url) is None


def test_clean_social_url_drops_tracking_and_mobile_hosts():
    assert (clean_social_url("https://m.facebook.com/JoesPlumbing/?ref=share")
            == "https://www.facebook.com/JoesPlumbing")


# ---------------------------------------------------------------------------
def test_a_matching_page_scores_high():
    assert social_confidence("Joe's Plumbing",
                             "https://www.facebook.com/JoesPlumbingNewmarket",
                             "Joe's Plumbing, Newmarket | Facebook", CITY) == 1.0


def test_a_run_together_handle_still_matches():
    assert social_confidence("Klimczuk Roofing",
                             "https://www.facebook.com/klimczukroofing", "", CITY) == 1.0


def test_separators_in_the_handle_do_not_break_it():
    assert social_confidence("Joe's Plumbing",
                             "https://www.facebook.com/joes.plumbing", "", CITY) == 1.0


def test_a_different_business_in_the_same_trade_scores_zero():
    # The whole failure mode: matching on "plumbing" and nothing else.
    assert social_confidence("Joe's Plumbing",
                             "https://www.facebook.com/BigBoxPlumbing",
                             "Big Box Plumbing | Facebook", CITY) == 0.0


def test_the_city_is_not_evidence():
    assert social_confidence("Joe's Plumbing",
                             "https://www.facebook.com/NewmarketPlumbingPros",
                             "Newmarket Plumbing Pros", CITY) == 0.0


def test_a_name_made_only_of_trade_words_needs_the_whole_name():
    generic = "Newmarket Plumbing"
    assert social_confidence(generic, "https://www.facebook.com/NewmarketPlumbing",
                             "", CITY) == 1.0
    assert social_confidence(generic, "https://www.facebook.com/AuroraPlumbing",
                             "", CITY) == 0.0


def test_the_title_counts_when_the_handle_is_meaningless():
    assert social_confidence("Klimczuk Roofing",
                             "https://www.facebook.com/profile-100078",
                             "Klimczuk Roofing - Newmarket, ON", CITY) > SOCIAL_THRESHOLD


def test_a_business_with_no_usable_name_never_matches():
    assert social_confidence("The Company", "https://www.facebook.com/thecompany",
                             "", CITY) == 0.0


# ---------------------------------------------------------------------------
def test_pick_socials_takes_the_best_of_each_network():
    hits = [
        SearchHit("https://www.facebook.com/BigBoxPlumbing", "Big Box Plumbing"),
        SearchHit("https://www.facebook.com/JoesPlumbingNewmarket", "Joe's Plumbing"),
        SearchHit("https://www.instagram.com/joesplumbing/", "Joe's Plumbing"),
        SearchHit("https://joesplumbing-fanpage.example.com", "Joe's Plumbing"),
    ]
    best = pick_socials(hits, "Joe's Plumbing", CITY)
    assert best["facebook"][0] == "https://www.facebook.com/JoesPlumbingNewmarket"
    assert best["instagram"][0] == "https://www.instagram.com/joesplumbing"


def test_pick_socials_returns_nothing_rather_than_a_guess():
    hits = [SearchHit("https://www.facebook.com/SomeoneElse", "Someone Else Plumbing")]
    assert pick_socials(hits, "Joe's Plumbing", CITY) == {}


def test_tokens_drop_legal_suffixes_and_the_city():
    assert tokens_of("Klimczuk Roofing Inc.", CITY) == {"klimczuk", "roofing"}


# ---------------------------------------------------------------------------
def test_emails_found_and_junk_ignored():
    text = "Contact us at info@joesplumbing.ca or noreply@example.com"
    assert emails_in(text) == ["info@joesplumbing.ca"]


def test_no_email_is_an_empty_list_not_a_crash():
    assert emails_in("") == []
    assert emails_in(None) == []


# ---------------------------------------------------------------------------
DDG_PAGE = """
<html><body>
<div class="header"><a href="/">DuckDuckGo</a></div>
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.facebook.com%2FJoesPlumbingNewmarket%2F&amp;rut=aa">
       Joe&#x27;s Plumbing - Newmarket, ON | <b>Facebook</b></a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.facebook.com%2FJoesPlumbingNewmarket%2F">
     Joe&#x27;s Plumbing, Newmarket, Ontario. 412 likes. Licensed plumber, info@joesplumbing.ca</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.yellowpages.ca%2Fbus%2FOntario%2FNewmarket%2Fjoes%2F1.html">
       Joe&#x27;s Plumbing - Newmarket, ON - YellowPages</a>
  </h2>
  <a class="result__snippet" href="#">Find Joe&#x27;s Plumbing in Newmarket</a>
</div>
</body></html>
"""


def test_unwrap_the_redirect_wrapper():
    assert unwrap("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.ca%2Fa&rut=x") \
        == "https://example.ca/a"


def test_unwrap_leaves_a_plain_url_alone():
    assert unwrap("https://example.ca/a") == "https://example.ca/a"


def test_parse_ddg_html_pulls_results_and_skips_the_chrome():
    hits = parse_ddg_html(DDG_PAGE)
    assert [h.url for h in hits] == [
        "https://www.facebook.com/JoesPlumbingNewmarket/",
        "https://www.yellowpages.ca/bus/Ontario/Newmarket/joes/1.html",
    ]
    assert hits[0].title.startswith("Joe's Plumbing")     # entities decoded, tags gone
    assert "412 likes" in hits[0].snippet


def test_parse_ddg_html_on_a_blocked_or_empty_page():
    assert parse_ddg_html("<html><body>no results</body></html>") == []


def test_a_parsed_page_feeds_straight_into_matching():
    best = pick_socials(parse_ddg_html(DDG_PAGE), "Joe's Plumbing", CITY)
    assert best["facebook"][0] == "https://www.facebook.com/JoesPlumbingNewmarket"
