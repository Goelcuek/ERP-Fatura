import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))


def test_every_ui_string_has_a_turkish_translation():
    from i18n_check import missing

    assert missing() == []


def test_placeholders_match():
    import re

    from app.translations_tr import TR

    for en, tr in TR.items():
        assert set(re.findall(r"{\w+}", en)) == set(re.findall(r"{\w+}", tr)), en
