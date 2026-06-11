"""Work modality classification."""

from __future__ import annotations

from jobagg.classification.models import FeatureBundle, LocationResult, ModalityResult, WorkModality


def classify_modality(features: FeatureBundle, location: LocationResult) -> ModalityResult:
    text = " ".join(
        [
            features.title or "",
            features.location_text or "",
            features.work_modality_raw or "",
            features.unv_work_location or "",
            str(features.evidence.get("isOnsite", "")),
        ]
    ).casefold()
    if features.source_id == "unv_uvp":
        if features.evidence.get("isOnsite") is False:
            return ModalityResult(
                value=WorkModality.ONLINE_REMOTE,
                confidence=0.95,
                evidence={"isOnsite": False},
            )
        if "on un premises" in text or features.evidence.get("isOnsite") is True:
            return ModalityResult(
                value=WorkModality.ONSITE,
                confidence=0.95,
                evidence={"isOnsite": features.evidence.get("isOnsite")},
            )
    if any(signal in text for signal in ("home-based", "home based", "homebased")):
        return ModalityResult(
            value=WorkModality.HOME_BASED,
            confidence=0.95,
            evidence={"matched": "home-based"},
        )
    if any(signal in text for signal in ("remote", "online", "virtual")):
        return ModalityResult(
            value=WorkModality.ONLINE_REMOTE,
            confidence=0.90,
            evidence={"matched": "remote/online"},
        )
    if "hybrid" in text:
        return ModalityResult(
            value=WorkModality.HYBRID,
            confidence=0.90,
            evidence={"matched": "hybrid"},
        )
    if any(signal in text for signal in ("2 locations", "3 locations", "multiple locations", "various locations")):
        return ModalityResult(
            value=WorkModality.MULTIPLE_LOCATIONS,
            confidence=0.90,
            evidence={"matched": "multiple locations"},
        )
    if location.iso2 or location.city:
        return ModalityResult(
            value=WorkModality.ONSITE,
            confidence=0.75,
            evidence={"location_confidence": location.confidence},
        )
    return ModalityResult()
