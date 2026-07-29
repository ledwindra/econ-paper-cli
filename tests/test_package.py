from importlib import import_module, metadata


def test_package_is_importable() -> None:
    package = import_module("econ_paper_cli")

    assert package.__name__ == "econ_paper_cli"


def test_distribution_metadata_is_available() -> None:
    distribution = metadata.metadata("econ-paper-cli")

    assert distribution["Name"] == "econ-paper-cli"
