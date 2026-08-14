# Argus — Roadmap officielle

Document directeur de l'évolution du projet Argus. Chaque phase doit être
validée avant de commencer la suivante (voir [Règle de développement](#règle-de-développement)).
Les statuts sont : `NOT STARTED`, `IN PROGRESS`, `COMPLETE`, `BLOCKED`.

---

## Vision

Argus est une infrastructure de collecte, de structuration et d'analyse des
communications officielles des principales banques centrales afin de
reconstruire dans le temps leur politique monétaire observable et d'en dériver
des données fondamentales utilisables pour l'analyse Forex.

L'objectif n'est pas initialement de construire un système de trading.

L'objectif est de construire progressivement :

```
Sources officielles
→ Documents officiels
→ Publications structurées
→ Classification
→ Extraction de faits
→ Historique des faits
→ Analyse des changements
→ État de politique monétaire
→ Fondamentaux Forex
→ éventuellement modèle/signaux de trading
```

**Principe fondamental :**
Chaque conclusion produite par Argus doit pouvoir être remontée jusqu'à la
publication officielle dont elle provient.

## Architecture cible

```
Official Sources
    ↓
Source Registry
    ↓
Source Discovery
    ↓
Publications
    ↓
Fetching
    ↓
Raw Documents
    ↓
Document Normalization
    ↓
Normalized Documents
    ↓
Publication Classification
    ↓
Publication Types
    ↓
Type-Specific Extraction
    ↓
Facts
    ↓
Temporal / Cross-Publication Analysis
    ↓
Monetary Policy State
    ↓
Forex Fundamentals
    ↓
éventuellement Trading / Signal Layer
```

Des préoccupations transversales s'appliquent à toutes les phases :

- provenance / lineage
- auditabilité
- reproductibilité
- idempotence
- tests
- validation
- gestion des erreurs
- observabilité
- absence de données inventées

## Banques initiales

- Federal Reserve
- European Central Bank
- Bank of England
- Bank of Japan
- Swiss National Bank
- Bank of Canada
- Reserve Bank of Australia
- Reserve Bank of New Zealand
- Norges Bank
- Sveriges Riksbank

L'architecture doit rester extensible à d'autres banques centrales.

---

## Phase 0 — Architecture & Spécification

- **Objectif** : définir les concepts fondamentaux avant d'implémenter les
  couches dépendantes.
- **Périmètre** : architecture générale, modèles de données, vocabulaire
  canonique, provenance, stockage, stratégie de tests, roadmap.
- **Livrables** : `docs/ARCHITECTURE.md`, `docs/SOURCES.md`, ce document
  (`docs/ROADMAP.md`), modèles dans `src/argus/models.py`, stockage SQLite
  dans `src/argus/store.py`.
- **Dépendances** : aucune.
- **Critères de validation** : les concepts (CentralBank, Source, Publication,
  Document, publication_type, classification) sont définis sans ambiguïté et
  documentés.
- **Statut** : `COMPLETE` (phase de formalisation/documentation ; finalisée par
  la création de ce document).

## Phase 1 — Source Registry & Collection

- **Objectif** : répondre à « Quelles sont les sources officielles et comment
  récupérer automatiquement leurs publications ? ».
- **Architecture** : `CentralBank` → `SourceRegistry` → `DiscoveryStrategy`
  → `Publication` → `Fetcher` → Raw Document.
- **Périmètre** : adapters déclaratifs, stratégies de discovery génériques
  (RSS, sitemap, HTML archives, pagination), retries/backoff, rate limiting,
  robots.txt, déduplication, idempotence, provenance, isolation des erreurs,
  stockage des documents bruts, SHA-256.
- **Livrables** : `src/argus/registry.py`, `discovery/`, `adapters/` (10
  banques), `collector.py`, `fetcher.py`, `http.py`, `robots.py` ; tables
  `sources`, `publications`, `documents`, `collect_errors`.
- **Dépendances** : Phase 0.
- **Critères de validation** : ajouter une banque requiert principalement une
  configuration/adaptation déclarative, non une réécriture du cœur. Tests de
  discovery (RSS, sitemap, HTML), fetch, idempotence, registry.
- **Statut** : `COMPLETE`.

## Phase 2 — Document Normalization

- **Objectif** : transformer `Raw Document` → `NormalizedDocument` sans
  interprétation économique.
- **Périmètre** : formats HTML, PDF, DOCX, XLSX, CSV, TXT ; représentation du
  texte, sections, pages, tableaux, métadonnées ; conservation de
  `document_id`, `publication_id`, `source_url`, `local_path`,
  `extraction_method`, `warnings`.
- **Principes** : pas de résumé, pas de traduction, pas d'interprétation, pas
  d'analyse économique, pas de LLM comme source de vérité. Normalisation
  réexécutable depuis les documents locaux sans accès réseau.
- **Livrables** : `src/argus/documents/` (parsers html/pdf/docx/spreadsheet/
  txt), `normalizer.py` ; tables `normalized_documents`, `document_sections`,
  `document_tables`.
- **Dépendances** : Phase 1.
- **Critères de validation** : réexécutabilité hors-ligne ; l'identité d'un
  document normalisé (`document_id`, SHA-256) est exclusivement produite par le
  `Normalizer`. Tests de parsers et de normalisation.
- **Statut** : `COMPLETE`.

## Phase 3 — Publication Classification

- **Objectif** : déterminer le type d'une publication.
- **Taxonomie initiale** : `monetary_policy_decision`,
  `monetary_policy_statement`, `press_conference`, `minutes`,
  `meeting_account`, `economic_projections`, `monetary_policy_report`,
  `speech`, `interview`, `other`, `unknown`.
- **Périmètre** : pipeline Source metadata → type_hint → URL → title →
  document metadata → content heuristics → unknown.
- **Principes** : déterministe, explicable, testable ; confidence + evidence ;
  pas de LLM initialement ; `unknown` préférable à une mauvaise classification.
- **Livrables** : `src/argus/classification/` (`classifier.py`, `rules.py`,
  `bank_rules.py`, `base.py`) ; table `classifications` comme source de
  vérité ; cache dénormalisé `publications.publication_type`.
- **Dépendances** : Phases 1 et 2.
- **Critères de validation** : chaque classification est traçable par son
  evidence ; les classifications live issues de `Source.publication_types`
  priment sur les hints périmés. Tests de classification et de CLI
  (`--classify`, `--report`).
- **Statut** : `COMPLETE`.

## Phase 4 — Fact Model

- **Objectif** : définir précisément ce qu'est un « Fact » dans Argus avant de
  développer les extracteurs spécialisés.
- **Périmètre** : le modèle doit représenter valeurs quantitatives, variations,
  projections, dates, périodes de référence, évaluations qualitatives, risques,
  forward guidance, déclarations, changements de formulation. Chaque fait doit
  conserver sa provenance exacte.
- **Modèle conceptuel cible** :
  ```
  Fact
  ├── subject
  ├── predicate
  ├── value
  ├── value_type
  ├── unit
  ├── period
  ├── effective_date
  ├── previous_value
  ├── source_document
  ├── source_location
  ├── extraction_method
  ├── confidence
  └── provenance
  ```
- **Livrables** : schéma de table `facts` + modèle dans `src/argus/facts/`,
  vocabulaire canonique (subject/predicate/ValueKind), stratégie de provenance
  (`FactLocation`), identité déterministe (`fact_id`), contrat d'extraction
  (`ExtractionResult`) ; document de référence `docs/DATA_MODEL.md`.
- **Dépendances** : Phases 2 et 3.
- **Critères de validation** : tout fait est remontable à la publication
  officielle ; aucune donnée n'est inventée (provenance requise).
- **Statut** : `COMPLETE` — modèle `Fact`, persistance idempotente
  (`facts` table, `save_fact`/`get_facts`/`rebuild_facts_for_document`),
  valeurs typées (`FactValue`), périodes (`FactPeriod`), temporalité multiple
  (`effective_date` vs `period` vs dates de publication/réunion), provenance
  (`source_text`, `source_location`), méthode/version d'extraction et
  confidence implémentés et testés. Aucun extracteur spécialisé n'a été
  implémenté. (Le champ `Publication.publication_type` reste un cache de
  classification, distinct du modèle `Fact`.)

## Phase 5 — Monetary Policy Decision

- **Objectif** : premier extracteur spécialisé.
- **Périmètre** : `MonetaryPolicyDecision` → `DecisionExtractor` → `Facts` ;
  policy rates, rate changes, decision date, effective date, vote, decision
  wording, balance-sheet decisions, asset purchases, forward guidance, risk
  assessment. Les différences entre banques doivent être gérées sans supposer
  que toutes publient les mêmes informations.
- **Livrables** : extracteur + règles par banque, tests, documentation.
- **Dépendances** : Phase 4.
- **Critères de validation** : aucun fait absent n'est inventé ; chaque taux
  extrait est lié à sa provenance.
- **Statut** : `COMPLETE` — extracteur `EcbDecisionExtractor` (v5.2.0). Implémenté :
  policy rates (3 taux), rate changes (signe conservé en bps), decision date,
  effective date, decision wording (`monetary_policy_decision/statement`,
  verbatim), asset purchases / balance-sheet decisions (APP/PEPP/TLTRO,
  `asset_purchase/decision`, identité du programme conservée), forward guidance
  (`policy_guidance/statement`, verbatim, non interprété). **Non présent dans
  les décisions ECB** : vote (jamais fabriqué — les votes relèvent des Minutes,
  Phase 8) et risk assessment (relève du Monetary Policy Statement, Phase 6).
  Frontière Phase 5/6 mise en œuvre et testée. 6 fixtures, golden tests
  (valeurs exactes, provenance verbatim, aucun fait inventé), extraction
  déterministe et persistance idempotente vérifiées ; `docs/EXTRACTORS.md`
  documente la couverture réelle.

## Phase 6 — Monetary Policy Statement

- **Objectif** : extraire justification de la décision, inflation, croissance,
  emploi, risques, conditions financières, orientation future, changements de
  formulation.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4 (et Phase 5 pour la cohérence du wording).
- **Critères de validation** : chaque extrait est relié à un passage précis du
  document normalisé.
- **Statut** : `COMPLETE` — extracteur `EcbMonetaryPolicyStatementExtractor`
  (v6.0.0, `src/argus/statements/`). Implémenté : justification de la décision
  (`monetary_policy/rationale`, verbatim), orientation future
  (`policy_guidance/statement`, verbatim, jamais interprétée), inflation /
  core inflation / inflation expectations, croissance (`growth` qualitatif,
  `gdp` quantitatif), marché du travail (`labour_market` / `unemployment` /
  `wages`), conditions financières, évaluation des risques
  (`risk` / `inflation_risk` / `growth_risk`, orientations catégoriques
  upside/downside/balanced ou texte verbatim quand aucune orientation n'est
  énoncée), valeurs quantitatives avec période de référence (`FactPeriod`).
  Routage déterministe par titre de section avec repli content-first étroit
  (guidance > risque > justification). 5 fixtures, golden tests (valeurs
  exactes, périodes, provenance verbatim, aucun fait inventé), extraction
  déterministe, persistance idempotente, gating par classification et
  coexistence Phase 5/6 vérifiées ; `docs/EXTRACTORS.md` documente la
  couverture réelle. La frontière avec la Phase 5 (décision, taux) et avec la
  Phase 12 (analyse des changements de formulation, seulement préservés
  verbatim) est mise en œuvre et testée.

## Phase 7 — Press Conferences

- **Objectif** : structurer opening statement, questions de journalistes,
  réponses des banquiers centraux.
- **Périmètre** : la provenance doit permettre de distinguer une décision
  officielle d'une déclaration individuelle.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4.
- **Critères de validation** : un propos individuel n'est jamais assimilé à une
  décision collective.
- **Statut** : `COMPLETE` — extracteur `EcbPressConferenceExtractor` (v7.0.0,
  `src/argus/press_conferences/`). Implémenté : routage opening statement
  (remarks, communication collective) vs questions/réponses (déclarations
  individuelles), attribution **verbatim** du locuteur (`Fact.speaker`,
  `President Christine Lagarde`, `Vice-President Luis de Guindos` ; jamais
  inférée — une réponse non étiquetée et les remarks restent `None`), les
  questions des journalistes ne sont jamais exploitées, identité de provenance
  `identity_qualifier` `remarks:{n}` / `answer:{turn}:{n}` (un propos individuel
  n'est jamais assimilé à une décision collective), catégories A–G
  (guidance > policy > risk > financial > inflation > labour > growth),
  évaluation des risques (orientations catégoriques upside/downside/balanced ou
  texte verbatim), valeurs quantitatives avec période de référence, questions
  non économiques ignorées (`non_economic_question_skipped`), gating strict par
  classification (`press_conference`), persistance idempotente. 7 fixtures,
  golden tests (valeurs exactes, provenance verbatim, attribution locuteur,
  aucun fait inventé), extraction déterministe, persistance idempotente, gating
  par classification et coexistence Phases 5/6/7 vérifiées ;
  `docs/EXTRACTORS.md` documente la couverture réelle.

## Phase 8 — Minutes / Meeting Accounts

- **Objectif** : extraire opinions, divergences, risques, arguments,
  préférences de politique monétaire, discussions économiques, éventuels
  dissents.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4.
- **Critères de validation** : les positions individuelles et les dissents sont
  distingués et tracés.
- **Statut** : `COMPLETE` — extracteur `EcbMinutesExtractor` (v8.0.0,
  `src/argus/minutes/`). Implémenté : routage conservateur par titre de section
  (titres économiques connus extraits ; titre du compte, notice légale, annexe
  statistique, « external monetary policy » et titres inconnus ignorés —
  `UNKNOWN ≠ ECONOMIC`), classification content-first par phrase (guidance >
  policy > risk > financial > inflation > labour > growth, mêmes ancres que la
  Phase 7), discussion économique fidèle (phrases de thème sans contenu —
  « The discussion focused on … », « Members discussed the possibility … » —
  supprimées ; le contenu explicite « Members noted that … » est extrait),
  **attribution tracée sans identité inventée** (`identity_qualifier`
  `minutes:{dissent|one_member|some_members|members|council|collective}:{n}`,
  `Fact.speaker` toujours `None`, un dissent n'est jamais transformé en vote),
  évaluation des risques (orientations catégoriques upside/downside/balanced ou
  texte verbatim), valeurs quantitatives avec période de référence, forward
  guidance en style indirect (« would be guided by », « stood ready to »),
  gating strict par classification (`minutes` / `meeting_account`),
  persistance idempotente. 4 fixtures, golden tests (valeurs exactes,
  provenance verbatim, attribution, aucun fait inventé), extraction
  déterministe, persistance idempotente, gating par classification et
  coexistence Phases 5/6/7 vérifiées ; `docs/EXTRACTORS.md` documente la
  couverture réelle.

## Phase 9 — Economic Projections

- **Objectif** : extraire les projections quantitatives et leurs révisions
  (GDP, inflation, core inflation, unemployment, autres variables publiées).
- **Périmètre** : conserver période, valeur, publication, projection
  précédente, révision.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4.
- **Critères de validation** : les révisions sont calculées entre publications
  comparables et proviennent de la provenance.
- **Statut** : `NOT STARTED`.

## Phase 10 — Monetary Policy Reports

- **Objectif** : extraire les informations de contexte : economic outlook,
  inflation drivers, growth outlook, labour market, financial conditions,
  risks, policy rationale.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4.
- **Critères de validation** : les affirmations contextuelles sont reliées à
  leur source.
- **Statut** : `NOT STARTED`.

## Phase 11 — Speeches & Interviews

- **Objectif** : conserver speaker, role, date, event, audience, topic,
  statement.
- **Périmètre** : les communications individuelles ne doivent pas être
  assimilées à des décisions collectives.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4.
- **Critères de validation** : la nature individuelle est toujours conservée
  dans la provenance.
- **Statut** : `NOT STARTED`.

## Phase 12 — Temporal / Cross-Publication Analysis

- **Objectif** : comparer les faits dans le temps : « Qu'est-ce qui a changé
  depuis la précédente réunion/publication ? ».
- **Exemples** : policy rate 4.00 → 4.25 ; inflation forecast 2.1 → 2.4 ; risk
  assessment balanced → upside ; guidance : ancienne formulation → nouvelle
  formulation.
- **Livrables** : analyseur de changements, tests, documentation.
- **Dépendances** : Phases 4–11 (historique de faits disponible).
- **Critères de validation** : chaque changement identifié est rattaché aux deux
  faits sources.
- **Statut** : `NOT STARTED`.

## Phase 13 — Policy Reaction Function

- **Objectif** : reconstruire empiriquement la réaction observable de la banque
  centrale à inflation, croissance, emploi, conditions financières,
  projections, risques, communication.
- **Périmètre** : ne jamais présenter cette reconstruction comme une « fonction
  de réaction vraie » : il s'agit d'une reconstruction empirique/inférée.
- **Livrables** : module d'analyse, tests, documentation.
- **Dépendances** : Phase 12.
- **Critères de validation** : le caractère inféré est explicite et non
  présenté comme factuel.
- **Statut** : `NOT STARTED`.

## Phase 14 — Monetary Policy State

- **Objectif** : construire un état synthétique et historisé de la politique
  monétaire.
- **Modèle cible** :
  ```
  Policy State
  ├── stance
  ├── direction
  ├── rate_level
  ├── rate_expectation
  ├── inflation_risk
  ├── growth_risk
  ├── labour_risk
  ├── guidance
  ├── confidence
  └── as_of
  ```
- **Livrables** : constructeur d'état, historisation, tests, documentation.
- **Dépendances** : Phase 12.
- **Critères de validation** : chaque état est daté (`as_of`), historisé et
  remontable aux faits.
- **Statut** : `NOT STARTED`.

## Phase 15 — Forex Fundamentals

- **Objectif** : comparer les états de politique monétaire entre banques (ex.
  ECB vs Fed) — non sur les taux actuels seuls, mais sur les trajectoires et
  anticipations de politique monétaire.
- **Livrables** : module de comparaison inter-banques, tests, documentation.
- **Dépendances** : Phase 14.
- **Critères de validation** : la comparaison utilise les états historisés et
  leurs trajectoires.
- **Statut** : `NOT STARTED`.

## Phase 16 — Historical Validation

- **Objectif** : tester Argus sur l'historique : Historical publications →
  facts → states → changes.
- **Périmètre** : vérifier cohérence temporelle, absence de look-ahead, absence
  de duplication, provenance complète, reproductibilité, stabilité des
  classifications et des extractions.
- **Livrables** : jeu de validation historique, tests, documentation.
- **Dépendances** : Phases 4–15.
- **Critères de validation** : aucun look-ahead ; résultats reproductibles.
- **Statut** : `NOT STARTED`.

## Phase 17 — Trading / Signal Layer

- **Objectif** : couche volontairement séparée du cœur d'Argus.
- **Périmètre** : Fundamental Data → Forex Analysis → éventuellement valuation /
  relative policy / expectations / risk / regime → éventuellement Trading Model
  → éventuellement Signal.
- **Principes** : le trading ne doit jamais contaminer la couche de collecte, de
  normalisation, de classification ou d'extraction.
- **Livrables** : non planifiés en détail ; tout signal doit rester distinct du
  cœur.
- **Dépendances** : Phase 15.
- **Critères de validation** : l'isolation stricte avec le cœur est vérifiée.
- **Statut** : `NOT STARTED`.

---

## Invariants du projet

1. Les sources officielles sont prioritaires.
2. Tout fait doit avoir une provenance.
3. Les transformations doivent être reproductibles.
4. La pipeline doit être idempotente.
5. Collection, normalisation, classification, extraction et analyse restent
   séparées.
6. Les couches basses ne font pas d'interprétation économique.
7. `unknown` est préférable à une donnée inventée.
8. Un LLM ne doit jamais être une source de vérité silencieuse.
9. Les transformations doivent être auditables.
10. Les spécificités bancaires doivent être encapsulées et ne pas contaminer le
    cœur.

## Règle de développement

Chaque phase future doit produire :

1. code
2. tests
3. documentation

Une phase doit être validée avant de commencer la suivante :

```
Phase N
→ implementation
→ tests
→ validation
→ documentation
→ commit
→ Phase N+1
```

## Architectural Notes

Divergences et observations entre cette roadmap et l'architecture actuelle :

- **Publication.publication_type** (`models.py`) : champ « dénormalisé » qui
  joue le rôle de cache de classification. Il ressemble superficiellement à une
  valeur de fait (ex. un type de publication), mais sa source de vérité est la
  table `classifications`. Il ne doit pas être confondu avec un `Fact` lors de
  la Phase 4. Aucune divergence bloquante.
- **Stockage** : l'implémentation repose sur SQLite (`store.py`). La roadmap
  n'impose pas de moteur spécifique ; une migration de stockage, si elle
  devait advenir, doit préserver les invariants (provenance, idempotence,
  auditabilité).
- **Mécanismes de discovery** : Phase 1 — les mécanismes implémentés couvrent
  RSS, sitemap/sitemap index et HTML archives avec pagination ; les APIs
  officielles et calendriers ne sont pas encore mobilisés pour toutes les
  banques. Cela reste du périmètre Phase 1 sans réouverture d'architecture.
- **Vocabulaire** : « state » (Phase 14) est un concept cible, sans modèle
  actuel ; toute implémentation devra définir des types de valeur explicites
  (`value_type`, `unit`, `period`) dès la Phase 4 pour éviter la dérive.
- **Extensibilité** : l'architecture cible et la Phase 1 sont alignées sur
  l'exigence « configuration déclarative » ; aucune banque initiale ne
  nécessite un codage particulier du cœur, vérifié pour les 10 banques du
  périmètre.

## Current Position

- Argus se situe au début de la **Phase 9 (Economic Projections)**.
- Les phases 0 à 8 sont marquées `COMPLETE` après vérification du repository
  (adapters, discovery, collector/fetcher, normalization, classification,
  modèle `Fact`, extracteurs ECB `EcbDecisionExtractor` v5.2.0,
  `EcbMonetaryPolicyStatementExtractor` v6.0.0,
  `EcbPressConferenceExtractor` v7.0.0 et `EcbMinutesExtractor` v8.0.0,
  tables SQLite correspondantes, tests).
- **Prochaine phase autorisée : Phase 9 — Economic Projections** (statut
  `NOT STARTED`).

## Out of Scope

Fonctionnalités qui ne doivent pas être implémentées prématurément :

- Extracteurs spécialisés (Phases 5–11) qui ne s'appuient pas sur le modèle
  `Fact`, la provenance et le contrat `ExtractionResult` définis en Phase 4.
- Analyse temporelle, fonction de réaction, état de politique monétaire et
  fondamentaux Forex avant les Phases 12–15.
- **Couche de trading / signaux** (Phase 17) tant que le cœur (collecte,
  normalisation, classification, extraction de faits) n'est pas stabilisé et
  isolé.
- Utilisation d'un LLM comme source de vérité (interdit par l'invariant 8).
- Interprétation économique dans les couches basses (interdit par l'invariant
  6) — la couche `Fact` ne produit pas d'interprétation.
