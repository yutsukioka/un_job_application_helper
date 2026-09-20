import importlib.util
from pathlib import Path
import subprocess
import pytest

spec = importlib.util.spec_from_file_location('sleep_runner', Path(__file__).resolve().parents[1]/'runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

@pytest.mark.parametrize('clock', ['wall', 'elapsed'])
def test_child_wait_expires_if_either_clock_advances(monkeypatch, clock):
    times = {'wall': 100.0, 'elapsed': 10.0}
    monkeypatch.setattr(runner.time, 'time', lambda: times['wall'])
    monkeypatch.setattr(runner.time, 'monotonic', lambda: times['elapsed'])
    class Child:
        args = ['fixture']
        def poll(self): return None
        def wait(self, timeout):
            times[clock] += 100
            raise subprocess.TimeoutExpired(self.args, timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        runner.wait_with_deadlines(Child(), 20, 120)
