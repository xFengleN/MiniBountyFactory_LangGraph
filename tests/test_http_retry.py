import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from src.utils.http import _compute_delay


def test_compute_delay_initial():
    delay = _compute_delay(0, 1.0, 30.0, 2.0)
    assert 1.0 <= delay <= 30.0


def test_compute_delay_backoff():
    delay1 = _compute_delay(0, 1.0, 30.0, 2.0)
    delay2 = _compute_delay(1, 1.0, 30.0, 2.0)
    assert delay2 > delay1


def test_compute_delay_capped():
    delay = _compute_delay(10, 1.0, 10.0, 2.0)
    assert delay <= 10.0


def test_compute_delay_with_jitter():
    delays = [_compute_delay(0, 1.0, 30.0, 2.0) for _ in range(100)]
    # should have some variation from jitter
    assert len(set(round(d, 3) for d in delays)) > 1


if __name__ == '__main__':
    test_compute_delay_initial()
    test_compute_delay_backoff()
    test_compute_delay_capped()
    test_compute_delay_with_jitter()
    print("All http_retry tests passed!")
