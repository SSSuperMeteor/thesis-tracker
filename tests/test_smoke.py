"""Package smoke tests."""


def test_package_import() -> None:
    import thesis_tracker

    assert thesis_tracker.__name__ == "thesis_tracker"
