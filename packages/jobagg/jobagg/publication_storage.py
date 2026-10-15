"""Conservative cooperative publisher write escrow; not an OS disk quota."""
import hashlib
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_ACTIVE = ContextVar("publication_storage_escrow", default=None)
_PRECHARGE_BYTES = 1024 * 1024


class PublisherEscrow:
    def __init__(self, lease, limits):
        from jobagg.storage_budget import CATEGORY_LIMITS, BudgetLedger
        self.ledger = BudgetLedger.attach(lease)
        if not isinstance(limits, dict) or set(limits) != set(CATEGORY_LIMITS) or any(
            type(value) is not int or value < 0 or value > CATEGORY_LIMITS[key]
            for key, value in limits.items()
        ):
            raise ValueError("finite_publisher_write_limits_required")
        self.keep_open = False
        self.limits = dict(limits)
        self.remaining = dict(limits)
        self.operations, self.prepaid, self.unconsumed = {}, {}, {}
        available = self.ledger.remaining_envelope()
        outstanding = self.ledger.snapshot()["operations"]
        for category, maximum in limits.items():
            old = outstanding.get(self.operation_id(category))
            if old:
                if old["maximum"] != maximum or old["writer_id"] != self.ledger.writer_id or old["category"] != category:
                    raise ValueError("publication_recovery_reservation_changed")
                self.operations[category] = self.ledger.resume(self.operation_id(category))
                # Unused precharged bytes from a crashed process stay charged.
                self.remaining[category] = maximum - old["consumed"]
                self.unconsumed[category] = self.remaining[category]
                self.prepaid[category] = 0
            elif maximum > available[category]:
                raise ValueError("publisher_write_limits_exceed_cycle_envelope")

    def operation_id(self, category):
        binding = f"publication\n{self.ledger.cycle_id}\n{self.ledger.writer_id}\n{category}"
        return hashlib.sha256(binding.encode()).hexdigest()

    def charge(self, path, count):
        if type(count) is not int or count < 0:
            raise ValueError("invalid_publication_write_bound")
        self.ledger.validate_writer(
            expected_cycle_id=self.ledger.cycle_id, expected_scope_hash=self.ledger.scope_hash,
            expected_ledger_path=self.ledger.path, allowed_phases=("publication_admitted",),
            required_path=path,
        )
        category = self.ledger.category_for(path)
        if count > self.remaining[category]:
            from jobagg.storage_budget import StorageBudgetDeferred
            raise StorageBudgetDeferred("publication_operation_envelope_exhausted")
        if not count:
            return
        if category not in self.operations:
            maximum = self.limits[category]
            self.operations[category] = self.ledger.reserve(
                category, maximum, path=path, operation_id=self.operation_id(category),
            )
            self.prepaid[category], self.unconsumed[category] = 0, maximum
        if self.prepaid[category] < count:
            grant = min(self.unconsumed[category], max(count - self.prepaid[category], _PRECHARGE_BYTES))
            self.operations[category].consume(grant)  # Persist before the write.
            self.unconsumed[category] -= grant
            self.prepaid[category] += grant
        self.prepaid[category] -= count
        self.remaining[category] -= count

    def close(self):
        for operation in self.operations.values():
            operation.settle()  # Never refund consumed blocks or deleted temps.


@contextmanager
def write_scope(lease, limits=None):
    existing = _ACTIVE.get()
    if existing is not None:
        if lease != existing.ledger.wire() or limits != existing.limits:
            raise ValueError("nested_publication_budget_binding_changed")
        yield existing
        return
    if lease is None:
        yield None
        return
    escrow = PublisherEscrow(lease, limits)
    token = _ACTIVE.set(escrow)
    try:
        yield escrow
        if not escrow.keep_open:
            escrow.close()
    finally:
        # An exception skips settlement: exact escrow remains for recovery.
        _ACTIVE.reset(token)


def charge(path, count):
    escrow = _ACTIVE.get()
    if escrow is not None:
        escrow.charge(path, count)
    elif os.environ.get("JOBAGG_STORAGE_BUDGET_LEASE"):
        raise ValueError("publication_write_requires_active_budget_scope")


def active():
    return _ACTIVE.get() is not None


def ensure_directory(path):
    path = Path(path)
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        charge(directory, 4096)
        directory.mkdir()
