"""Search: substring/AND matching, operators, fuzzy fallback, filters, sorts, paging."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.web.conftest import make_animated_gif, make_jpeg, make_sidecar


def _seed_library(library: Path) -> None:
    office_dir = library / "04-office-software-humor" / "image"
    relationship_dir = library / "02-relationship-drama-comics" / "gif"
    nsfw_dir = library / "00 - NSFW" / "image"

    excel = office_dir / "excel-vs-powerpoint-meme.jpg"
    make_jpeg(excel, color=(10, 10, 200))
    make_sidecar(
        excel,
        title="Excel vs PowerPoint Twitter Thread",
        tags=["excel", "powerpoint"],
        topics=["technology"],
        category_name="office-software-humor",
    )

    kevin = office_dir / "kevin-hart-reaction.jpg"
    make_jpeg(kevin, color=(200, 10, 10))
    make_sidecar(
        kevin,
        title="Kevin Hart Reaction",
        tags=["kevin", "hart"],
        topics=["celebrity"],
        category_name="office-software-humor",
    )

    cat_gif = relationship_dir / "funny-cat.gif"
    make_animated_gif(cat_gif)
    make_sidecar(
        cat_gif,
        title="Funny Cat Reaction",
        tags=["cat", "animal"],
        topics=["animals"],
        category_name="relationship-drama-comics",
    )

    spicy = nsfw_dir / "spicy-meme.jpg"
    make_jpeg(spicy, color=(50, 200, 50))
    make_sidecar(spicy, title="Spicy Meme", tags=["spicy"], nsfw=True, category_name="nsfw")


@pytest.fixture
def seeded_client(library: Path, client, fastapi_app):
    _seed_library(library)
    fastapi_app.state.indexer.scan()
    return client


def test_substring_search_matches_title_and_tags(seeded_client):
    resp = seeded_client.get("/api/items", params={"q": "excel"})
    assert resp.status_code == 200
    data = resp.json()
    names = {item["name"] for item in data["items"]}
    assert "excel-vs-powerpoint-meme.jpg" in names
    assert data["total"] >= 1
    assert "took_ms" in data


def test_multi_term_and_query(seeded_client):
    resp = seeded_client.get("/api/items", params={"q": "excel powerpoint"})
    data = resp.json()
    names = {item["name"] for item in data["items"]}
    assert names == {"excel-vs-powerpoint-meme.jpg"}


def test_type_operator_filters(seeded_client):
    resp = seeded_client.get("/api/items", params={"q": "type:gif"})
    data = resp.json()
    assert all(item["media_type"] == "gif" for item in data["items"])
    assert any(item["name"] == "funny-cat.gif" for item in data["items"])


def test_cat_and_tag_operators(seeded_client):
    resp = seeded_client.get("/api/items", params={"q": "cat:relationship-drama-comics"})
    data = resp.json()
    assert all(item["category"]["name"] == "relationship-drama-comics" for item in data["items"])

    resp2 = seeded_client.get("/api/items", params={"q": "tag:cat"})
    data2 = resp2.json()
    assert any("cat" in item["tags"] for item in data2["items"])


def test_is_operators(seeded_client):
    # favorite the excel item first
    items_resp = seeded_client.get("/api/items", params={"q": "excel"}).json()
    item_id = items_resp["items"][0]["id"]
    seeded_client.put(f"/api/items/{item_id}/favorite", json={"favorite": True})

    fav_resp = seeded_client.get("/api/items", params={"q": "is:fav"})
    fav_data = fav_resp.json()
    assert any(item["id"] == item_id for item in fav_data["items"])

    nsfw_resp = seeded_client.get("/api/items", params={"q": "is:nsfw"})
    nsfw_data = nsfw_resp.json()
    assert all(item["nsfw"] for item in nsfw_data["items"])
    assert any(item["name"] == "spicy-meme.jpg" for item in nsfw_data["items"])


def test_nsfw_excluded_by_default(seeded_client):
    resp = seeded_client.get("/api/items", params={})
    data = resp.json()
    assert all(not item["nsfw"] for item in data["items"])


def test_nsfw_include_shows_everything(seeded_client):
    resp = seeded_client.get("/api/items", params={"nsfw": "include"})
    data = resp.json()
    assert data["total"] == 4


def test_fuzzy_fallback_on_typo(seeded_client):
    resp = seeded_client.get("/api/items", params={"q": "kevn hart"})
    data = resp.json()
    names = {item["name"] for item in data["items"]}
    assert "kevin-hart-reaction.jpg" in names
    assert data["fuzzy_used"] is True


def test_fuzzy_fallback_total_accounts_for_appended_items(seeded_client):
    # "kevn hart" has zero literal FTS/LIKE matches (base total 0), so every item in the
    # response comes from the rapidfuzz fallback; `total` must count them, not stay at 0.
    resp = seeded_client.get("/api/items", params={"q": "kevn hart"})
    data = resp.json()
    assert data["fuzzy_used"] is True
    assert len(data["items"]) >= 1
    assert data["total"] >= len(data["items"])


def test_fuzzy_fallback_only_applies_to_the_first_page(seeded_client):
    """Page 0 of a fuzzy-only query gets the rapidfuzz-augmented results and `fuzzy_used: true`;
    page 1+ of the exact same query must not re-run the fallback."""
    page0 = seeded_client.get("/api/items", params={"q": "kevn hart", "offset": 0}).json()
    assert page0["fuzzy_used"] is True
    assert len(page0["items"]) >= 1

    page1 = seeded_client.get("/api/items", params={"q": "kevn hart", "offset": 1}).json()
    assert page1["fuzzy_used"] is False
    # the base (non-fuzzy) FTS/LIKE match count for "kevn hart" is 0, so page 1 is empty
    assert page1["items"] == []


def test_like_wildcards_are_escaped_in_short_token_fallback(seeded_client):
    """`_`/`%` are 1-2 char terms -> the `LIKE` fallback; none of the seeded names/titles contain
    a literal `_` or `%`, so both must return nothing -- not "every row" (an unescaped `LIKE
    '%_%'`/`'%%%'` matches any non-empty string, which is exactly the injection this guards)."""
    resp_underscore = seeded_client.get("/api/items", params={"q": "_", "nsfw": "include"})
    assert resp_underscore.json()["items"] == []

    resp_percent = seeded_client.get("/api/items", params={"q": "%", "nsfw": "include"})
    assert resp_percent.json()["items"] == []


def test_filters_type_category_favorite(seeded_client):
    resp = seeded_client.get("/api/items", params={"type": "gif,video"})
    data = resp.json()
    assert all(item["media_type"] in ("gif", "video") for item in data["items"])

    resp2 = seeded_client.get("/api/items", params={"category": "office-software-humor"})
    data2 = resp2.json()
    assert all(item["category"]["name"] == "office-software-humor" for item in data2["items"])


def test_sort_name_and_size(seeded_client):
    resp = seeded_client.get("/api/items", params={"nsfw": "include", "sort": "name"})
    names = [item["name"] for item in resp.json()["items"]]
    assert names == sorted(names, key=str.lower)


def test_sort_newest_oldest_are_reverses(seeded_client):
    newest = [
        i["id"]
        for i in seeded_client.get(
            "/api/items", params={"nsfw": "include", "sort": "newest"}
        ).json()["items"]
    ]
    oldest = [
        i["id"]
        for i in seeded_client.get(
            "/api/items", params={"nsfw": "include", "sort": "oldest"}
        ).json()["items"]
    ]
    assert newest == list(reversed(oldest))


def test_random_sort_is_stable_for_a_given_seed(seeded_client):
    first = seeded_client.get(
        "/api/items", params={"nsfw": "include", "sort": "random", "seed": 42}
    ).json()
    second = seeded_client.get(
        "/api/items", params={"nsfw": "include", "sort": "random", "seed": 42}
    ).json()
    assert [i["id"] for i in first["items"]] == [i["id"] for i in second["items"]]


def test_random_sort_paging_has_no_overlap_or_gaps(seeded_client):
    common = {"nsfw": "include", "sort": "random", "seed": 7, "limit": 2}
    page1 = seeded_client.get("/api/items", params={**common, "offset": 0}).json()
    page2 = seeded_client.get("/api/items", params={**common, "offset": 2}).json()
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)
    assert ids1 | ids2 == {1, 2, 3, 4}


def test_paging_total_is_exact_regardless_of_limit(seeded_client):
    resp = seeded_client.get("/api/items", params={"nsfw": "include", "limit": 1})
    data = resp.json()
    assert data["total"] == 4
    assert len(data["items"]) == 1
