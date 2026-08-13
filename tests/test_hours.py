"""Google's hour strings are the messiest input in the tool: en dashes, narrow
no-break spaces, split shifts, and kitchens that close after midnight."""
import hours

WEEK = [
    "Monday: 8:00 AM – 5:00 PM",
    "Tuesday: 8:00 AM – 5:00 PM",
    "Wednesday: 8:00 AM – 5:00 PM",
    "Thursday: 8:00 AM – 5:00 PM",
    "Friday: 8:00 AM – 5:00 PM",
    "Saturday: 9:00 AM – 1:00 PM",
    "Sunday: Closed",
]


def test_parses_a_normal_week():
    days = hours.parse(WEEK)
    assert [d["day"] for d in days] == hours.DAYS
    assert days[0]["ranges"] == [(480, 1020)]
    assert days[6]["closed"] is True


def test_handles_the_narrow_no_break_space_google_actually_sends():
    # U+202F before AM/PM — invisible, and it breaks a naive split.
    days = hours.parse(["Monday: 8:00 AM – 5:00 PM"])
    assert days[0]["ranges"] == [(480, 1020)]


def test_handles_every_dash_google_might_use():
    for dash in ("–", "—", "-", "‐"):
        days = hours.parse([f"Monday: 8:00 AM {dash} 5:00 PM"])
        assert days[0]["ranges"] == [(480, 1020)], dash


def test_open_24_hours():
    days = hours.parse(["Monday: Open 24 hours"])
    assert days[0]["all_day"] is True
    assert days[0]["ranges"] == [(0, 1440)]
    assert days[0]["label"] == "Open 24 hours"


def test_split_shift():
    days = hours.parse(["Monday: 9:00 AM – 12:00 PM, 1:00 – 5:00 PM"])
    assert days[0]["ranges"] == [(540, 720), (780, 1020)]


def test_closing_after_midnight_does_not_wrap_backwards():
    # A kitchen open 5pm–2am must not parse as a negative range.
    days = hours.parse(["Friday: 5:00 PM – 2:00 AM"])
    start, end = days[0]["ranges"][0]
    assert (start, end) == (1020, 1560)
    assert end > start


def test_missing_meridiem_on_the_opening_time_is_inferred():
    assert hours.parse(["Monday: 8:00 – 5:00 PM"])[0]["ranges"] == [(480, 1020)]
    assert hours.parse(["Monday: 1:00 – 5:00 PM"])[0]["ranges"] == [(780, 1020)]


def test_noon_and_midnight_do_not_go_backwards():
    assert hours.parse(["Monday: 12:00 PM – 6:00 PM"])[0]["ranges"] == [(720, 1080)]
    assert hours.parse(["Monday: 12:00 AM – 6:00 AM"])[0]["ranges"] == [(0, 360)]


def test_nonsense_is_dropped_rather_than_guessed():
    assert hours.parse(["Monday: by appointment"]) == []
    assert hours.parse(["nonsense"]) == []
    assert hours.parse(None) == []
    assert hours.parse([]) == []


def test_week_minutes_is_indexed_for_javascript():
    week = hours.week_minutes(hours.parse(WEEK))
    assert week[0] == []                       # Sunday, closed
    assert week[1] == [[480, 1020]]            # Monday
    assert week[6] == [[540, 780]]             # Saturday


def test_schema_spec_uses_iso_times():
    spec = hours.schema_spec(hours.parse(WEEK))
    assert spec[0]["opens"] == "08:00" and spec[0]["closes"] == "17:00"
    assert spec[0]["dayOfWeek"] == "https://schema.org/Monday"
    assert len(spec) == 6                       # Sunday is closed, so no entry


def test_schema_spec_wraps_a_past_midnight_close():
    spec = hours.schema_spec(hours.parse(["Friday: 5:00 PM – 2:00 AM"]))
    assert spec[0]["opens"] == "17:00" and spec[0]["closes"] == "02:00"


def test_labels_read_the_way_a_person_writes_them():
    days = hours.parse(WEEK)
    assert days[0]["label"] == "8am – 5pm"
    assert days[5]["label"] == "9am – 1pm"
    assert hours.parse(["Monday: 8:30 AM – 5:15 PM"])[0]["label"] == "8:30am – 5:15pm"


def test_summary_collapses_a_uniform_run():
    assert hours.summary(hours.parse(WEEK[:5])) == "Mon–Fri 8am – 5pm"


def test_summary_stays_quiet_when_the_week_is_irregular():
    assert hours.summary(hours.parse(WEEK)) == ""
    assert hours.summary(hours.parse(["Monday: 8:00 AM – 5:00 PM"])) == ""
