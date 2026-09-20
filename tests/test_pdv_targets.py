import pytest
import pdv_targets


def test_sop_defaults_and_roundtrip(monkeypatch):
    saved = {}
    monkeypatch.setattr(pdv_targets.storage, 'save_setting', lambda key, value: saved.update({key: value}) or value)
    monkeypatch.setattr(pdv_targets.storage, 'load_setting', lambda key: saved.get(key))
    rules = pdv_targets.defaults()['rules']
    assert [r['minutes'] for r in rules] == [8, 11, 16, 33, 60, 180]
    rules[0]['agrupacion'] = ['K+T']
    pdv_targets.save(rules, 'test')
    assert pdv_targets.config()['rules'][0]['agrupacion'] == ['K+T']
    assert pdv_targets.config()['updated_by'] == 'test'


def test_reject_ambiguous_and_invalid():
    rules = pdv_targets.defaults()['rules']
    rules[0]['cliente'] = ['287']
    rules[1]['cliente'] = ['287']
    with pytest.raises(ValueError):
        pdv_targets.validate(rules)
    for minutes in [0, -1, float('nan'), float('inf'), 1500]:
        with pytest.raises(ValueError):
            pdv_targets.validate([dict(name='Bad', minutes=minutes)])
