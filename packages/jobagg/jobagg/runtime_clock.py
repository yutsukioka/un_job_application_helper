"""Boot-bound suspend-aware budget, independent of adjustable UTC.

The identical dispatcher copy bootstraps before selected-package imports.
Darwin CLOCK_MONOTONIC_RAW is backed by mach_continuous_time; Linux uses
CLOCK_BOOTTIME. No raw elapsed deadline is accepted across hosts or boots.
"""

from functools import lru_cache
import json
import math
import os
from pathlib import Path
import sys
import time

ENVIRONMENT = "JOBAGG_ENTRY_CLOCK"
SCHEMA = "jobagg-continuous-budget-1"


def now():
    if sys.platform == "darwin":
        return time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
    if sys.platform.startswith("linux"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    raise ValueError("suspend_aware_clock_not_supported")


@lru_cache(maxsize=1)
def domain():
    if sys.platform == "darwin":
        import ctypes

        library = ctypes.CDLL(None, use_errno=True)
        query = library.sysctlbyname
        query.argtypes = (
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        )
        query.restype = ctypes.c_int
        data = ctypes.create_string_buffer(128)
        size = ctypes.c_size_t(len(data))
        if query(b"kern.bootsessionuuid", data, ctypes.byref(size), None, 0) != 0:
            raise ValueError("clock_boot_identity_unavailable")
        boot = data.value.decode("ascii")
        provider = "CLOCK_MONOTONIC_RAW"
    elif sys.platform.startswith("linux"):
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        provider = "CLOCK_BOOTTIME"
    else:
        raise ValueError("suspend_aware_clock_not_supported")
    if not boot or len(boot) > 128:
        raise ValueError("clock_boot_identity_invalid")
    return {"host": os.uname().nodename, "boot": boot, "provider": provider}


@lru_cache(maxsize=8)
def _decode(text):
    return json.loads(text)


def inherited():
    value = os.environ.get(ENVIRONMENT)
    return _decode(value) if value is not None else None


def remaining(value):
    if (
        not isinstance(value, dict)
        or value.get("schema") != SCHEMA
        or value.get("domain") != domain()
        or any(
            type(value.get(key)) not in (int, float) or not math.isfinite(value[key])
            for key in ("started", "deadline")
        )
    ):
        raise ValueError("entry_clock_domain_or_budget_invalid")
    instant = now()
    if instant < value["started"] or value["deadline"] < value["started"]:
        raise ValueError("entry_elapsed_clock_regressed")
    return value["deadline"] - instant


def budget(seconds, *, parent=None, started=None, started_wall=None):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("finite_elapsed_budget_required")
    instant = now()
    if started is not None:
        if (
            type(started) not in (int, float)
            or not math.isfinite(started)
            or not 0 <= started <= instant
        ):
            raise ValueError("entry_clock_origin_invalid")
        instant = started
    value = {
        "schema": SCHEMA,
        "domain": domain(),
        "started": instant,
        "deadline": instant + seconds,
        "started_at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if started_wall is None else started_wall)
        ),
    }
    if parent is not None:
        remaining(parent)
        value["started"] = parent["started"]
        value["started_at_utc"] = parent["started_at_utc"]
        value["deadline"] = min(value["deadline"], parent["deadline"])
    return value


def clamp(value, seconds):
    remaining(value)
    return {**value, "deadline": min(value["deadline"], value["started"] + seconds)}
