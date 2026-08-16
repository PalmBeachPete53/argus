"""Compatibility tests: the "policy reaction" legacy API is preserved verbatim.

These tests pin two guarantees of the Temporal Relationships rename:

1. Every legacy name (``PolicyReaction``, ``PolicyReactionAnalyzer``,
   ``analyze_reactions``, ``reaction_id_of``, ``CONDITION_SUBJECTS``,
   ``REACTION_SUBJECTS``, the ``argus.reactions`` module, and the legacy store
   methods) still exists and is an alias for its canonical counterpart.
2. The persisted ``reaction_id`` value is **frozen** — the canonical
   ``temporal_relationship_id_of`` produces byte-identical ids to the legacy
   ``reaction_id_of``.
"""

from __future__ import annotations

import tempfile

import argus.reactions as reactions
import argus.temporal_relationships as tr
from argus.reactions import (
    CONDITION_SUBJECTS,
    REACTION_SUBJECTS,
    PolicyReaction,
    PolicyReactionAnalyzer,
    PolicyReactionResult,
    analyze_reactions,
    reaction_id_of,
)
from argus.store import Store
from argus.temporal_relationships import (
    EARLIER_SUBJECTS,
    LATER_SUBJECTS,
    TemporalRelationship,
    TemporalRelationshipAnalyzer,
    TemporalRelationshipResult,
    analyze_temporal_relationships,
    temporal_relationship_id_of,
)


def test_legacy_class_is_canonical_class():
    assert PolicyReaction is TemporalRelationship
    assert PolicyReactionResult is TemporalRelationshipResult
    assert PolicyReactionAnalyzer is TemporalRelationshipAnalyzer
    assert analyze_reactions is analyze_temporal_relationships


def test_legacy_reaction_id_of_accepts_legacy_kwargs():
    # reaction_id_of is a signature-preserving wrapper: it accepts the legacy
    # condition_change_id/policy_change_id keyword names.
    assert reaction_id_of(
        central_bank="ecb", condition_change_id="c1", policy_change_id="p1"
    ) == temporal_relationship_id_of(
        central_bank="ecb", earlier_change_id="c1", later_change_id="p1"
    )


def test_legacy_vocabulary_is_canonical_vocabulary():
    assert CONDITION_SUBJECTS is EARLIER_SUBJECTS
    assert REACTION_SUBJECTS is LATER_SUBJECTS


def test_shim_module_reexports_identical_objects():
    assert reactions.TemporalRelationship is tr.TemporalRelationship
    assert reactions.analyze_temporal_relationships is tr.analyze_temporal_relationships
    assert reactions.reaction_id_of is tr.reaction_id_of


def test_id_is_frozen_identical_value():
    legacy = reaction_id_of(
        central_bank="ecb", condition_change_id="c1", policy_change_id="p1"
    )
    canonical = temporal_relationship_id_of(
        central_bank="ecb", earlier_change_id="c1", later_change_id="p1"
    )
    assert legacy == canonical
    # frozen golden value — must never change across refactors.
    assert canonical == "1a1818c537fba092fa5c522870470fd20bc127dfa02893d375b7e8fc9c6f8bcd"


def test_legacy_result_reactions_property_matches_relationships():
    result = TemporalRelationshipResult()
    rel = TemporalRelationship(
        central_bank="ecb", condition_change_id="c1", policy_change_id="p1"
    )
    result.relationships = [rel]
    assert result.reactions == [rel]
    result.reactions = []
    assert result.relationships == []


def test_legacy_store_methods_delegate():
    store = Store(tempfile.mkdtemp() + "/compat.db")
    rel = TemporalRelationship(
        central_bank="ecb", condition_change_id="c1", policy_change_id="p1"
    )
    rel.resolve_id()

    store.save_reaction(rel)
    assert store.get_reaction(rel.reaction_id) is not None
    assert store.get_reactions() == store.get_temporal_relationships()
    assert store.get_reactions()[0].reaction_id == rel.reaction_id

    store.rebuild_reactions([rel], bank="ecb")
    assert len(store.get_reactions()) == 1

    store.delete_reactions(bank="ecb")
    assert store.get_reactions() == []


def test_legacy_analyze_reactions_function_works():
    # analyze_reactions is a direct alias of the canonical function.
    assert analyze_reactions.__name__ == "analyze_temporal_relationships"
    assert callable(analyze_reactions)
