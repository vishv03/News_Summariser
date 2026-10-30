import news_summariser


def test_package_imports() -> None:
    assert callable(news_summariser.main)
