"""Claim checker rules. These protect you from publishing illegal or misleading claims."""

import pytest

from app.modules.content_generator.claim_checker import Context, blocking, check_content, check_text

CTX = Context(delivery_days_max=5, returns_days=14)


def rules(text, ctx=CTX):
    return {(f.rule, f.severity) for f in check_text(text, "x", ctx)}


def ids(text, ctx=CTX):
    return {f.rule for f in check_text(text, "x", ctx)}


@pytest.mark.parametrize("text", [
    "Join 10,000+ happy customers", "Rated 4.9/5 by our buyers", "Customers love it!",
    '"Changed my life, honestly the best purchase" - Sarah', "Thousands of happy customers can't be wrong",
])
def test_fake_reviews_are_high(text):
    assert ("fake_reviews", "high") in rules(text)


@pytest.mark.parametrize("text", [
    "Only 3 left in stock!", "Selling fast", "Ends tonight", "Hurry, while stocks last", "Limited-time offer",
    "12 people are viewing this",
])
def test_fake_urgency_is_high(text):
    assert ("fake_urgency", "high") in rules(text)


@pytest.mark.parametrize("text", [
    "Relieves back pain", "Improves posture in days", "Clinically proven", "Helps weight loss", "detox your skin",
])
def test_health_claims_are_high(text):
    assert ("health_claim", "high") in rules(text)


def test_safety_claim_high_without_documents_medium_with():
    assert ("safety_claim", "high") in rules("Non-toxic and child-safe")
    assert ("safety_claim", "medium") in rules("Non-toxic", Context(safety_docs_confirmed=True))


def test_superlatives_and_absolutes():
    assert ids("The best lint roller, number one in the UK") == {"superlative"}
    assert "absolute" in ids("Practically indestructible")


def test_price_and_green_claims():
    assert "price_claim" in ids("Was £29.99, now half price")
    assert "green_claim" in ids("Eco-friendly and sustainable")


def test_no_returns_is_high():
    assert ("no_returns", "high") in rules("All sales final")


def test_free_delivery_only_flagged_if_not_offered():
    assert "free_claim" in ids("Free UK delivery")
    assert "free_claim" not in ids("Free UK delivery", Context(free_delivery=True))


def test_guarantee_only_if_in_brand():
    assert "guarantee" in ids("30-day money-back guarantee")
    assert "guarantee" not in ids("money-back guarantee", Context(guarantee="30-day money-back guarantee"))


def test_delivery_promises_checked_against_supplier():
    assert ("delivery_promise", "high") in rules("Next-day delivery", Context(delivery_days_max=5))
    assert ("delivery_promise", "high") in rules("Fast delivery", Context(delivery_days_max=14))
    assert "delivery_promise" not in ids("5 day delivery", Context(delivery_days_max=5))
    assert "delivery_promise" not in ids("Fast UK delivery", Context(delivery_days_max=4))
    assert ("delivery_promise", "medium") in rules("Fast delivery", Context(delivery_days_max=None))


def test_placeholders_block_approval():
    assert ("placeholder", "high") in rules("Made from [CHECK: material]")


def test_clean_honest_copy_passes():
    text = ("A reusable lint roller that lifts pet hair from sofas and clothes. Rinse under the tap to reuse. "
            "Delivered in 3-5 working days. Returns accepted within 14 days.")
    assert check_text(text, "x", CTX) == []


def test_no_false_positives_on_common_words():
    # 'treats' as in dog treats is still flagged (health rule is deliberately strict), but these are fine:
    assert ids("Fits most sofas. Cleans quickly. Great for dog owners.") == set()
    assert ids("Available in green or blue") == set()  # a colour, not an eco claim
    assert ids("Best before the end of the month") == set()
    assert "superlative" in ids("Best roller for pet owners")


def test_check_content_walks_nested_fields_and_sorts():
    data = {"title": "Best roller", "bullets": ["Only 2 left!", "Lifts hair"], "faqs": [{"q": "Safe?", "a": "Non-toxic"}]}
    found = check_content(data, CTX)
    assert [f["severity"] for f in found] == sorted([f["severity"] for f in found], key=["high", "medium", "low"].index)
    fields = {f["field"] for f in found}
    assert {"title", "bullets[0]", "faqs[0].a"} <= fields
    assert len(blocking(found)) == 2
