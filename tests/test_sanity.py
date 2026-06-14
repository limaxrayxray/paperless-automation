"""Test trivial : confirme que le harnais pytest collecte et exécute, et que les
modules du projet sont importables dans un environnement hermétique (token bidon
injecté par conftest)."""


def test_harnais_vert():
    assert True


def test_config_importable_sans_env_reel():
    # config.py lit le token via l'environnement; conftest a injecté un token
    # bidon, donc l'import ne doit pas lever.
    import config

    assert config.PAPERLESS_TOKEN  # présent (bidon), non vide
    assert "TPS" in config.CUSTOM_FIELD_IDS
