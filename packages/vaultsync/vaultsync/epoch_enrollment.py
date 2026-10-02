"""D099 additive current-registry admission; no activation record rewriting."""

import copy

from .device_enrollment import verify_enrollment
from .epoch_catch_up import bridge_records
from .epoch_rotation import _canonical, _reject


def current_context(owner, state):
    records = bridge_records(state["components"]["history"])
    activation_id, generation = None, state["epoch"]
    if owner._history_origin is not None:
        base = owner._history(state)._bridge_context()
        activation_id, generation = base["activation_id"], base["registry_generation"]
    for raw in records:
        if raw.get("format") == "atlasvault-enrollment-bridge":
            generation = raw["enrollment"]["next_registry_generation"]
        else:
            p = raw["proof"] if "wrapper" in raw else raw
            activation_id = p["activation_id"] if "wrapper" in raw else p["root"]
            generation = p["plan"]["new_epoch"]
    if activation_id is None:
        _reject("ATLAS_RUNTIME_PROVISIONING_REQUIRED")
    return {
        "account_id": owner._context["account_id"],
        "vault_id": owner._context["vault_id"],
        "key_epoch": state["epoch"],
        "registry_generation": generation,
        "state_root": owner._state_root(state),
        "activation_id": activation_id,
    }


def accept_enrollment(owner, proof, transcript):
    s = owner._load()
    owner._active(s)
    records = bridge_records(s["components"]["history"])
    for raw in records:
        if raw.get("format") == "atlasvault-enrollment-bridge":
            prior = raw["enrollment"]
            if prior["root"] == proof.get("root"):
                if (
                    _canonical(prior) != _canonical(proof)
                    or transcript != prior["transcript_sha256"]
                ):
                    _reject("ATLAS_ENROLLMENT_REJECTED")
                return False
    registry = verify_enrollment(
        proof,
        registry=s["registry"],
        context=current_context(owner, s),
        confirmed_transcript=transcript,
        status=s["status"],
    )
    bridge = {
        "format": "atlasvault-enrollment-bridge",
        "version": 1,
        "enrollment": copy.deepcopy(proof),
    }
    history = owner._history(s)._stage_epoch(bridge)
    s["components"]["history"] = history
    s["registry"] = registry
    s["recipients"] = sorted(e["device_id"] for e in registry if e["state"] == "ACTIVE")
    s["generation"] += 1
    owner._file.write(s)
    return True
