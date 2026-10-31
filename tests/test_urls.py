from news_summariser.urls import clean_url, url_hash


def test_tracking_params_fragment_and_host_case_removed() -> None:
    url = "https://WWW.Example.com/World/story?utm_source=rss&id=7&fbclid=abc#comments"
    assert clean_url(url) == "https://www.example.com/World/story?id=7"


def test_path_case_and_real_params_kept() -> None:
    # Paths can be case-sensitive, and non-tracking params can pick a different article.
    assert clean_url("https://x.com/A?page=2") == "https://x.com/A?page=2"


def test_param_order_does_not_change_hash() -> None:
    assert url_hash("https://x.com/a?b=2&a=1") == url_hash("https://x.com/a?a=1&b=2&utm_medium=x")
