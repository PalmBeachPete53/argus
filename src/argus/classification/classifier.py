from __future__ import annotations

from datetime import datetime
from typing import Iterable

from ..models import Publication
from ..normalize import now_utc
from .bank_rules import rules_for_bank
from .base import (
    METHOD_CONTENT_HEURISTIC,
    METHOD_DOCUMENT_METADATA,
    METHOD_SOURCE_TYPE_HINT,
    METHOD_TITLE_PATTERN,
    METHOD_UNRESOLVED,
    METHOD_URL_PATTERN,
    Confidence,
    PublicationClassification,
)
from .rules import TypeRule, canonical_types, rules_for


class PublicationClassifier:
    """Phase 2B — deterministic, explainable classification of publications.

    The engine evaluates evidence tiers in a fixed order and returns the first
    tier that yields a single unambiguous candidate. It never delegates to a
    model and never fabricates a type: unresolvable publications are classified
    as ``unknown``.
    """

    def __init__(self, store=None, rules=None, registry=None) -> None:
        self.store = store
        self.rules = list(rules) if rules is not None else None
        self.registry = registry

    # ------------------------------------------------------------------
    # single publication
    # ------------------------------------------------------------------
    def classify(
        self,
        publication: Publication,
        normalized=None,
        *,
        at: datetime | None = None,
    ) -> PublicationClassification:
        bank = publication.central_bank
        rules = self._active_rules(bank)

        evidence: list[str] = []
        if publication.source_id:
            evidence.append(f"source_id={publication.source_id}")
        for raw in (publication.extra.get("type_hint") or ()):
            evidence.append(f"type_hint={raw}")

        def decide(ptype: str, confidence: Confidence, method: str, all_evidence: list[str]) -> PublicationClassification:
            return PublicationClassification(
                publication_id=publication.id or publication.dedup_key or "",
                central_bank=bank,
                publication_type=ptype,
                confidence=confidence,
                method=method,
                evidence=all_evidence,
                classified_at=at or now_utc(),
                publication_title=publication.title,
                source_id=publication.source_id,
            )

        # --- Tier 1: source metadata / type hint ---
        source_types = self._source_types(publication)
        if len(source_types) == 1:
            return decide(source_types[0], Confidence.HIGH, METHOD_SOURCE_TYPE_HINT, evidence)
        source_set = set(source_types)

        # --- Tiers 2-5: url, title, document metadata, content ---
        tier_hits = (
            ("url_pattern", self._tier_hits(rules, "url", publication, normalized), METHOD_URL_PATTERN),
            ("title_pattern", self._tier_hits(rules, "title", publication, normalized), METHOD_TITLE_PATTERN),
            ("document_metadata", self._metadata_types(rules, normalized), METHOD_DOCUMENT_METADATA),
            ("content_heuristic", self._content_types(rules, normalized), METHOD_CONTENT_HEURISTIC),
        )

        for index, (_label, hits, method) in enumerate(tier_hits):
            if not hits:
                continue
            evidence.extend(evidence_text for _, evidence_text in hits)
            types = list({t for t, _ in hits})
            if len(types) != 1:
                continue
            contender = types[0]
            # A stronger, later, single signal that disagrees means this tier is
            # not reliable on its own (e.g. a shared URL slug vs an explicit
            # title such as "Minutes of the Federal Open Market Committee").
            contradicted = any(
                len({t for t, _ in later}) == 1 and next(t for t, _ in later) != contender
                for _, later, _ in tier_hits[index + 1:]
                if later
            )
            if contradicted:
                continue
            if method == METHOD_CONTENT_HEURISTIC:
                confidence = Confidence.LOW
            else:
                confidence = Confidence.HIGH if contender in source_set else Confidence.MEDIUM
            return decide(contender, confidence, method, evidence)

        # --- Fallback: unresolvable ---
        if not evidence:
            evidence.append("unresolved")
        return decide("unknown", Confidence.LOW, METHOD_UNRESOLVED, evidence)

    # ------------------------------------------------------------------
    # batch
    # ------------------------------------------------------------------
    def classify_many(
        self,
        publications: Iterable[Publication],
        normalized_map=None,
        *,
        persist: bool = True,
    ) -> list[PublicationClassification]:
        results: list[PublicationClassification] = []
        for publication in publications:
            doc = None
            if normalized_map is not None:
                docs = normalized_map.get(publication.id or "", ())
                doc = next((d for d in docs if d.ok), None)
            classification = self.classify(publication, normalized=doc)
            if persist and self.store is not None:
                self.store.set_classification(
                    classification.publication_id,
                    central_bank=classification.central_bank,
                    publication_type=classification.publication_type,
                    confidence=classification.confidence.value,
                    method=classification.method,
                    evidence=classification.evidence,
                    classified_at=classification.classified_at,
                )
            results.append(classification)
        return results

    def classify_publications(
        self,
        publication_ids: Iterable[str],
        *,
        persist: bool = True,
    ) -> list[PublicationClassification]:
        if self.store is None:
            return []
        normalized_map = {}
        publications = []
        for pub_id in publication_ids:
            pub = self.store.get_publication(pub_id)
            if pub is None:
                continue
            normalized_map[pub_id] = self.store.normalized_documents_for_publication(pub_id)
            publications.append(pub)
        return self.classify_many(publications, normalized_map, persist=persist)

    def classify_all(
        self,
        *,
        banks: tuple[str, ...] | list[str] | None = None,
        persist: bool = True,
    ) -> list[PublicationClassification]:
        if self.store is None:
            return []
        normalized_map = {}
        publications = self.store.list_publications(bank=banks)
        for pub in publications:
            if pub.id:
                normalized_map[pub.id] = self.store.normalized_documents_for_publication(pub.id)
        return self.classify_many(publications, normalized_map, persist=persist)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _active_rules(self, bank: str) -> list[TypeRule]:
        rules = list(self.rules) if self.rules is not None else list(rules_for(bank))
        rules.extend(rules_for_bank(bank))
        return rules

    def _source_types(self, publication: Publication) -> list[str]:
        # The *live* source declaration is the single source of truth for
        # declared types: discovery stamps `extra["type_hint"]` from the same
        # declaration, which can go stale when an adapter is corrected. The
        # stored hint is only a fallback for sources that are no longer
        # registered.
        source = None
        if self.registry is not None:
            source = self.registry.source(publication.source_id)
        if source is None:
            return canonical_types(publication.extra.get("type_hint") or ())
        return canonical_types(source.publication_types or ())

    def _tier_hits(self, rules, attr: str, publication: Publication, normalized=None):
        hits: list[tuple[str, str]] = []
        source = publication.url if attr == "url" else publication.title
        for rule in rules:
            for pattern in getattr(rule, f"match_{attr}")(source):
                hits.append((rule.publication_type, f"{attr}_pattern={rule.publication_type} ({pattern})"))
        return list(hits) if hits else []

    def _metadata_types(self, rules, normalized=None):
        if normalized is None:
            return []
        fields: list[str] = []
        if normalized.title:
            fields.append(normalized.title)
        for chunk in self._metadata_strings(normalized.metadata):
            fields.append(chunk)
        hits: list[tuple[str, str]] = []
        for rule in rules:
            for field in fields:
                for pattern in rule.match_title(field):
                    hits.append((rule.publication_type, f"document_metadata={rule.publication_type} ({pattern})"))
        seen = set()
        deduped = []
        for entry in hits:
            if entry not in seen:
                seen.add(entry)
                deduped.append(entry)
        return deduped

    def _content_types(self, rules, normalized=None):
        text = ""
        if normalized is not None:
            text = normalized.text or ""
        if not text and normalized is not None:
            text = " ".join(s.heading + " " + s.text for s in normalized.sections)
        hits: list[tuple[str, str]] = []
        for rule in rules:
            for pattern in rule.match_content(text[:20000]):
                hits.append((rule.publication_type, f"content_heuristic={rule.publication_type} ({pattern})"))
        return hits

    @staticmethod
    def _metadata_strings(metadata: dict, depth: int = 0) -> list[str]:
        if depth > 3:
            return []
        values: list[str] = []
        for value in metadata.values():
            if isinstance(value, str):
                if value.strip():
                    values.append(value)
            elif isinstance(value, dict):
                values.extend(PublicationClassifier._metadata_strings(value, depth + 1))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        values.append(item)
                    elif isinstance(item, dict):
                        values.extend(PublicationClassifier._metadata_strings(item, depth + 1))
        return values