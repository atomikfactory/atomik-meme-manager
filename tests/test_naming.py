from atomik_meme.naming import is_reserved, resolve_collision, slugify


def test_slugify_basic():
    assert slugify("Guy Staring at Monitor in Disbelief") == "guy-staring-at-monitor-in-disbelief"


def test_slugify_strips_diacritics_and_punctuation():
    assert slugify("Café Über!! Déjà Vu???") == "cafe-uber-deja-vu"


def test_slugify_drops_leading_article():
    assert slugify("The Best Meme Ever", drop_leading_articles=True) == "best-meme-ever"
    assert slugify("A Cat Judging You", drop_leading_articles=True) == "cat-judging-you"
    assert slugify("An Honest Mistake", drop_leading_articles=True) == "honest-mistake"


def test_slugify_keeps_article_when_disabled():
    assert slugify("The Best Meme Ever", drop_leading_articles=False) == "the-best-meme-ever"


def test_slugify_does_not_drop_article_if_it_is_the_whole_title():
    assert slugify("The", drop_leading_articles=True) == "the"


def test_slugify_truncates_at_word_boundary():
    text = " ".join(f"word{i}" for i in range(20))
    slug = slugify(text, max_len=30)
    assert len(slug) <= 30
    assert not slug.endswith("-")
    # every fragment in the truncated slug should be a whole "wordN" token
    for part in slug.split("-"):
        assert part.startswith("word")


def test_slugify_empty_falls_back_to_meme():
    assert slugify("", max_len=80) == "meme"
    assert slugify("!!!???", max_len=80) == "meme"


def test_slugify_reserved_name_gets_suffix():
    assert slugify("con", max_len=80) == "con-meme"
    assert slugify("NUL", max_len=80) == "nul-meme"
    assert slugify("LPT9", max_len=80) == "lpt9-meme"


def test_is_reserved():
    for name in ("con", "CON", "prn", "aux", "nul", "com1", "COM9", "lpt1", "lpt9"):
        assert is_reserved(name)
    for name in ("console", "computer", "company", "lpt", "com", "meme"):
        assert not is_reserved(name)


def test_resolve_collision_no_conflict():
    existing: dict[str, str] = {}
    assert resolve_collision("funny-cat", "abc1234567890000", existing) == "funny-cat"


def test_resolve_collision_idempotent_for_same_owner():
    existing = {"funny-cat": "abc1234567890000"}
    assert resolve_collision("funny-cat", "abc1234567890000", existing) == "funny-cat"


def test_resolve_collision_appends_short_id_suffix():
    existing = {"funny-cat": "owner0000000000a"}
    result = resolve_collision("funny-cat", "abc1234567890000", existing)
    assert result == "funny-cat-abc123"


def test_resolve_collision_falls_back_to_long_id_suffix():
    id_ = "abc1234567890000"
    existing = {
        "funny-cat": "owner0000000000a",
        "funny-cat-abc123": "owner0000000000b",
    }
    result = resolve_collision("funny-cat", id_, existing)
    assert result == f"funny-cat-{id_[:12]}"


def test_resolve_collision_with_callable_lookup():
    owners = {"slug": "id-a"}

    def lookup(stem: str) -> str | None:
        return owners.get(stem)

    assert resolve_collision("slug", "id-a", lookup) == "slug"
    assert resolve_collision("slug", "id-b", lookup) == "slug-id-b"
