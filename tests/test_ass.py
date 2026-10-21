from pathlib import Path

import pytest

from app.services.ass import _ass_time, validate_ass_file, write_ass


def test_ass_time_uses_the_only_format_accepted_by_libass():
    assert _ass_time(0) == '0:00:00.00'
    assert _ass_time(.52) == '0:00:00.52'
    assert _ass_time(65.23) == '0:01:05.23'


def test_ass_groups_words_and_marks_active_token(tmp_path):
    target = write_ass([
        {'start': 0, 'end': .2, 'text': 'Uma'},
        {'start': .2, 'end': .4, 'text': 'frase'},
        {'start': .4, 'end': .6, 'text': 'legível'},
    ], tmp_path / 'legendas.ass')
    contents = target.read_text(encoding='utf8')
    assert 'PlayResY: 1920' in contents
    assert contents.count('Dialogue:') == 3
    assert r'\c&H0000FFFF&' in contents
    assert 'BorderStyle,Outline,Shadow' in contents
    assert '&H00000000' in contents
    assert 'Dialogue: 0,0:00:00.00,0:00:00.20,' in contents
    assert validate_ass_file(target) == 3


def test_retention_ass_has_safe_area_yellow_highlight_without_box(tmp_path):
    target = write_ass([
        {'start': 0, 'end': .2, 'text': 'Gancho'},
        {'start': .2, 'end': .5, 'text': 'forte'},
    ], tmp_path / 'retencao.ass', style='retention')
    contents = target.read_text(encoding='utf8')
    assert 'Arial,68' in contents
    assert ',2,60,60,290,1' in contents
    assert r'\c&H0000FFFF&' in contents


def test_highlight_ass_style_is_available_for_dynamic_and_narrative_presets(tmp_path):
    target = write_ass([
        {'start': 0, 'end': .4, 'text': 'Texto'},
        {'start': .4, 'end': .8, 'text': 'ativo'},
    ], tmp_path / 'legendas.ass', style='highlight')
    contents = target.read_text(encoding='utf8')
    assert 'Arial,68' in contents
    assert ',2,60,60,270,1' in contents
    assert r'\c&H0000FFFF&' in contents


def test_ass_never_invents_invalid_word_timing(tmp_path):
    with pytest.raises(ValueError):
        write_ass([{'start': 1, 'end': 1, 'text': 'erro'}], tmp_path / 'legendas.ass')
