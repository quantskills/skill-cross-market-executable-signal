import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))


def test_variants_are_fixed_one_change_at_a_time():
    import importlib.util
    assert importlib.util.find_spec('run_platform_study') is not None
    from run_platform_study import variants
    cfg={'ridge':1.,'availability_delay_calendar_days':2,
         'extra_publication_delay_days':[1,3],'ridge_sensitivity':[.1,1.,10.]}
    rows=variants(cfg)
    assert rows==[('base',2,1.),('delay_plus_1',3,1.),('delay_plus_3',5,1.),
                  ('ridge_0.1',2,.1),('ridge_10',2,10.)]
