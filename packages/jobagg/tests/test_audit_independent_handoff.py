import importlib.util
import pytest
from pathlib import Path
spec=importlib.util.spec_from_file_location('handoff',Path(__file__).parents[1]/'scripts/activate_audit_independent.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_guard_preserves_due_work_and_rejects_unscoped_or_other_debt():
    v={'pending':None,'unscoped_held':None,'held':['scope',1,'reason'],'revalidation_reasons':[]}
    m.check_vector(v,True,True)
    m.check_vector({**v,'revalidation_reasons':['time_transition_due']},True,True)
    for changes in ({'pending':['work']},{'unscoped_held':['unknown']},{'held':None},{'revalidation_reasons':['contract_epoch_changed']}):
        with pytest.raises(ValueError):m.check_vector({**v,**changes},True,True)
    for approved,boundary in ((False,True),(True,False)):
        with pytest.raises(ValueError):m.check_vector(v,approved,boundary)
