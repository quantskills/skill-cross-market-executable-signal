import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import archive_quotes


def sample(values='69.97,70.39,71.73,72.44,–,73.79,74.64'):
    return ('STUB_1,STUB_2,STUB_3,STUB_4,Jan,Feb\n'
            '2024,Crude Oil,WTI - Cushing,,74.15,77.25\n'
            'STUB_1,STUB_2,STUB_3,Fri 12/13,Fri 12/20,Mon 12/30,Tue 12/31,Wed 1/1,Thu 1/2,Fri 1/3\n'
            f'Crude Oil,  WTI - Cushing,,{values}\n\x1a').encode('cp1252')


def parse(raw):
    fn = getattr(archive_quotes, 'parse_eia_spot_quotes', None)
    assert callable(fn), 'EIA archived spot parser is required'
    return fn(raw, '2025-01-08')


def test_spot_uses_daily_section_not_monthly_or_weekly_columns():
    rows = parse(sample())
    assert [r['observation_date'] for r in rows] == [
        '2024-12-30', '2024-12-31', '2025-01-01', '2025-01-02', '2025-01-03']
    assert [r['value'] for r in rows] == [71.73, 72.44, None, 73.79, 74.64]
    assert all(r['instrument'] == 'WTI_CUSHING_SPOT' for r in rows)


def test_spot_keeps_nonpositive_prices_for_explicit_feature_policy():
    rows = parse(sample('1,2,-36.98,0,–,73.79,74.64'))
    assert rows[0]['value'] == -36.98
    assert rows[1]['value'] == 0


@pytest.mark.parametrize('bad', [
    sample().replace(b'Fri 1/3', b'Thu 1/3'),
    sample().replace(b'Fri 1/3', b'Fri 1/10'),
    sample().replace(b'WTI - Cushing', b'Brent'),
    sample().replace(b'74.64', b'NaN'),
    sample().replace(b'74.64', b'74.64,99'),
])
def test_spot_rejects_ambiguous_or_invalid_source(bad):
    with pytest.raises(ValueError):
        parse(bad)
