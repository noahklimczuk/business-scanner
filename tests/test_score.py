from prospect import parse, score


def lead(**kw):
    base = {"review_count": 0, "rating": None, "phone": None, "hours": None,
            "category": None, "website_kind": "none"}
    base.update(kw)
    return base


def test_reviews_dominate():
    assert score(lead(review_count=80)) > score(lead(review_count=10))


def test_review_contribution_saturates():
    # Past ~90 reviews more reviews stop mattering; they are all the same lead.
    assert score(lead(review_count=200)) == score(lead(review_count=90))


def test_contactable_and_tended_listings_score_higher():
    bare = lead(review_count=30)
    assert score(lead(review_count=30, phone="905-555-0123")) > score(bare)
    assert score(lead(review_count=30, hours=["Mon: 9-5"])) > score(bare)


def test_high_value_category_beats_low_value():
    assert (score(lead(review_count=30, category="Roofing contractor"))
            > score(lead(review_count=30, category="Cafe")))


def test_bad_rating_is_a_penalty_not_a_bonus():
    # A website amplifies a reputation problem; we do not want it as a portfolio piece.
    assert (score(lead(review_count=40, rating=2.9))
            < score(lead(review_count=40, rating=4.6)))


def test_a_single_five_star_review_is_not_quality():
    assert score(lead(review_count=1, rating=5.0)) < score(lead(review_count=20, rating=4.5))


def test_near_miss_segment_scores_above_an_identical_true_zero():
    # They already believe they need a web presence — an easier sell.
    assert (score(lead(review_count=30, website_kind="social"))
            > score(lead(review_count=30, website_kind="none")))


def test_score_stays_in_range():
    assert score(lead(review_count=999, rating=5.0, phone="x", hours=["a"],
                      category="Plumber", website_kind="social")) <= 100
    assert score(lead(review_count=0, rating=1.0)) >= 0


PLACE = {
    "id": "ChIJexample",
    "displayName": {"text": "  Joe's Plumbing  "},
    "primaryTypeDisplayName": {"text": "Plumber"},
    "formattedAddress": "1 Main St, Newmarket, ON",
    "nationalPhoneNumber": "(905) 555-0123",
    "location": {"latitude": 44.05, "longitude": -79.46},
    "rating": 4.7,
    "userRatingCount": 62,
    "regularOpeningHours": {"weekdayDescriptions": ["Monday: 8:00 AM – 5:00 PM"]},
    "businessStatus": "OPERATIONAL",
}


def test_parse_maps_the_response_shape():
    rec = parse(PLACE)
    assert rec["place_id"] == "ChIJexample"
    assert rec["name"] == "Joe's Plumbing"          # whitespace stripped
    assert rec["category"] == "Plumber"
    assert rec["phone"] == "(905) 555-0123"
    assert rec["review_count"] == 62
    assert rec["website_kind"] == "none"
    assert rec["score"] > 0


def test_parse_survives_a_sparse_listing():
    rec = parse({"id": "x", "displayName": {"text": "Bare Listing"}})
    assert rec["name"] == "Bare Listing"
    assert rec["phone"] is None
    assert rec["hours"] is None
    assert rec["website_kind"] == "none"
    assert rec["score"] >= 0


def test_parse_flags_a_facebook_only_business():
    rec = parse({**PLACE, "websiteUri": "https://www.facebook.com/joesplumbing"})
    assert rec["website_kind"] == "social"
