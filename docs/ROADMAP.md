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
- **Statut** : `COMPLETE` — **9 banques implémentées et enregistrées dans le
  dispatch générique** :
  - ECB (`EcbDecisionExtractor` v5.2.0) — 3 taux (main_refinancing,
    marginal_lending, deposit_facility), rate changes (signe conservé en bps),
    decision date, effective date, decision wording, asset purchases (APP/PEPP/TLTRO),
    forward guidance
  - Fed (`FedDecisionExtractor` v5.3.0) — federal funds target range (RANGE),
    rate changes, decision date, decision wording, forward guidance
  - BoE (`BoeDecisionExtractor` v5.4.0) — Bank Rate, rate changes (pp→bps),
    decision date, decision wording, forward guidance
  - BoC (`BocDecisionExtractor` v5.6.0) — policy interest rate / overnight rate target,
    rate changes, decision date, decision wording, forward guidance
  - SNB (`SnbDecisionExtractor` v5.5.0) — SNB policy rate, rate changes,
    decision date, decision wording, forward guidance
  - RBA (`RbaDecisionExtractor` v5.7.0) — cash rate target, rate changes,
    decision date, decision wording, forward guidance
  - RBNZ (`RbnzDecisionExtractor` v5.8.0) — Official Cash Rate (OCR), rate
    changes, decision date, decision wording, forward guidance
  - Riksbank (`RiksbankDecisionExtractor` v5.10.0) — policy rate, rate changes
    (pp→bps), decision date, decision wording, forward guidance
  - Norges (`NorgesDecisionExtractor` v5.9.0) — policy rate, rate changes
    (pp→bps), decision date, decision wording, forward guidance
  
  **BoJ** : **intentionnellement sans extracteur Decision** — la Banque du
  Japon fusionne décision et statement dans une seule publication
  `monetary_policy_statement` (voir Phase 6 / BoJ Statement extractor).
  
  **Non présent dans les décisions ECB** : vote (jamais fabriqué — les votes
  relèvent des Minutes, Phase 8) et risk assessment (relève du Monetary Policy
  Statement, Phase 6). Frontière Phase 5/6 mise en œuvre et testée. 6 fixtures
  ECB + 1 fixture par autre banque, golden tests (valeurs exactes, provenance
  verbatim, aucun fait inventé), extraction déterministe et persistance
  idempotente vérifiées ; `docs/EXTRACTORS.md` documente la couverture réelle.
  Durcissement : gating strict par classification (la table `classifications`
  est la source de vérité unique ; une classification absente, non-décision ou
  la seule cache dénormalisée `publication_type` refusent l'extraction) et
  persistance idempotente résultats vides compris (une ré-extraction vide
  efface les faits périmés du document). Tests de dispatch générique ajoutés
  pour toutes les 9 banques.

## Phase 6 — Monetary Policy Statement

- **Objectif** : extraire justification de la décision, inflation, croissance,
  emploi, risques, conditions financières, orientation future, changements de
  formulation.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4 (et Phase 5 pour la cohérence du wording).
- **Critères de validation** : chaque extrait est relié à un passage précis du
  document normalisé.
- **Statut** : `COMPLETE` — **9 banques implémentées et enregistrées dans le
  dispatch générique** :
  - ECB (`EcbMonetaryPolicyStatementExtractor` v6.0.0) — justification,
    forward guidance, inflation/core/expectations, croissance (growth/gdp),
    marché du travail, conditions financières, risques
  - Fed (`FedStatementExtractor` v6.1.0) — monetary policy, forward guidance,
    inflation, growth, labour market
  - BoE (`BoeStatementExtractor` v6.1.0) — monetary policy, forward guidance,
    inflation, growth
  - BoJ (`BojStatementExtractor` v6.1.0) — **fusionne décision + statement** :
    decision date, policy target (uncollateralized overnight call rate),
    decision wording + vote sentence, forward guidance, price/growth/risk assessment
  - BoC (`BocStatementExtractor` v6.1.0) — monetary policy, forward guidance,
    inflation, growth
  - SNB (`SnbStatementExtractor` v6.1.0) — monetary policy, forward guidance,
    inflation, growth
  - RBA (`RbaStatementExtractor` v6.1.0) — monetary policy, forward guidance,
    inflation, growth
  - RBNZ (`RbnzStatementExtractor` v6.1.0) — monetary policy, forward guidance,
    inflation, growth
  - Riksbank (`RiksbankStatementExtractor` v6.1.0) — monetary policy, forward
    guidance, inflation, growth
  
  **Norges** : pas de `monetary_policy_statement` — la publication de type
  rapport (Monetary Policy Report) est le contenu mixte (voir Phase 10 / Norges
  Report extractor).
  
  Implémenté par banque : justification de la décision (`monetary_policy/rationale`,
  verbatim), orientation future (`policy_guidance/statement`, verbatim, jamais
  interprétée), inflation / core inflation / inflation expectations, croissance
  (`growth` qualitatif, `gdp` quantitatif), marché du travail (`labour_market` /
  `unemployment` / `wages`), conditions financières, évaluation des risques
  (`risk` / `inflation_risk` / `growth_risk`, orientations catégoriques
  upside/downside/balanced ou texte verbatim quand aucune orientation n'est
  énoncée), valeurs quantitatives avec période de référence (`FactPeriod`).
  Routage déterministe par titre de section avec repli content-first étroit
  (guidance > risque > justification). 5 fixtures ECB + 1 fixture par autre
  banque, golden tests (valeurs exactes, périodes, provenance verbatim, aucun
  fait inventé), extraction déterministe, persistance idempotente, gating par
  classification et coexistence Phase 5/6 vérifiées ; `docs/EXTRACTORS.md`
  documente la couverture réelle. Durcissement : gating strict par
  classification (la table `classifications` est la source de vérité unique ;
  une classification absente, non-statement ou la seule cache dénormalisée
  `publication_type` refusent l'extraction, et un refus ne supprime jamais les
  faits déjà persistés) et persistance idempotente résultats vides compris
  (une ré-extraction vide efface les faits périmés du document, sans toucher
  aux autres documents ni aux autres publications). La frontière avec la Phase 5
  (décision, taux) et avec la Phase 12 (analyse des changements de formulation,
  seulement préservés verbatim) est mise en œuvre et testée. Tests de dispatch
  générique ajoutés pour toutes les 9 banques.

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

**Extension multi-banque (Phase 4.x)** — extracteur Fed
`FedPressConferenceExtractor` (v7.1.0, `src/argus/press_conferences/fed.py`, +
`_shared.py` structurel), enregistré pour le dispatch générique. Les
transcriptions FOMC (`/FOMCpresconf<date>.pdf`, `/press-?conference/`) étaient
classées `unknown` : règle TypeRule `press_conference` ajoutée (URL/titre).
Transcription Fed en dialogue par tours (labels ALL-CAPS) : premier tour Fed
= remarks (collectif, `speaker=None`), ensuite réponses individuelles
(`answer:{turn}:{n}`, locuteur verbatim, e.g. `CHAIRMAN WARSH`), tout label non
Fed (journalistes, `MR./MS.`) = frontière de tour jamais exploitée. Mêmes sujets,
gating, valeurs (avec `GDP_NEAR_MISS`) et boundary que Phase 7 ; pas de
Phase 5/6/12/13/14/15 sémantique. 1 fixture + tests synthétiques
(`tests/test_press_conferences_fed.py`, 33 tests) ; la Phase 16 reste
`DEFERRED`.

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
  `UNKNOWN ≠ ECONOMIC`), **correction pré-Phase 13** : les headings non
  économiques connus sont routés par **identité exacte** (`_IGNORE_HEADINGS`)
  plus les familles de titre explicites (« Account of the monetary policy
  meeting … », « Minutes of … ») — **plus aucun routage par sous-chaîne**
  (« External monetary policy developments », « Statistical annexes »,
  « Copyright notice » ne sont jamais lus comme les headings contrôlés, et
  jamais comme ECONOMIC), classification content-first par phrase (guidance >
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
  provenance verbatim, attribution, aucun fait inventé), tests de frontière de
  routage IGNORE (identité exacte, near-misses, familles de titre, déterminisme
  catégoriel), extraction
  déterministe, persistance idempotente, gating par classification et
  coexistence Phases 5/6/7 vérifiées ; `docs/EXTRACTORS.md` documente la
  couverture réelle.

### Phase 8.x — Extension multi-banques des Minutes

- **Statut** : `COMPLETE` — la famille `minutes` / `meeting_account` est étendue
  de 1 à **6 banques** : ECB (Meeting Account, conservé) auxquelles s'ajoutent
  **BoE** (`BoeMinutesExtractor` v8.2.0), **BoJ** (`BojMinutesExtractor`
  v8.3.0), **Norges** (`NorgesMinutesExtractor` v8.4.0), **RBA**
  (`RbaMinutesExtractor` v8.6.0) et **Riksbank**
  (`RiksbankMinutesExtractor` v8.5.0) — les quatre banques déjà **classifiées**
  `minutes` par règle de banque sans extracteur ; l'écart RBA (découverte de
  l'archive Board Minutes via `src/argus/adapters/rba.py` + extraction) est
  fermé.
- Extracteurs **banque-spécifiques** partageant uniquement des helpers
  **structurels** (`src/argus/minutes/_shared.py` : normalisation des titres,
  découpage des phrases, gate de valeur explicite, attribution déterministe +
  émission avec provenance) ; aucune sémantique banque-spécifique dans le code
  partagé. Pas de symétrie artificielle : Fed (meeting_account), SNB, BoC
  et RBNZ restent `not applicable` / représentés par une autre famille.
- Contract Fact canonique respecté : banque, sujet, prédicat, valeur, kind,
  unité, période, provenance publication/document, `speaker`/`effective_date`
  `None`, qualificateur `minutes:{attribution}:{n}` avec attribution tracée
  sans identité inventée (précédence `dissent` > `one_member` >
  `some_members` > `most_members` > `members` > `committee` > `collective`) ;
  valeurs supportées par la source uniquement, forecasts sans période ignorés,
  aucune décision/statement/press-conference subject (Phases 5/6/7), aucun
  hawkish/dovish.
- 5 fixtures (`boe_minutes_full.html`, `boj_minutes.html`,
  `norges_minutes.html`, `rba_minutes.html`, `riksbank_minutes.html`), tests
  de contrat / dispatch
  générique / attribution / provenance / limites / déterminisme (répétition +
  indépendance d'ordre) / immutabilité / gating (refus + persistance idempotente
  résultats vides compris) / intégration (publication → classification →
  extracteur → faits → persistance → récupération) dans
  `tests/test_minutes_multibank.py`.
- 58+ tests verts et déterministes, `compileall` propre.
  **Phase 16 (validation historique) reste `DEFERRED`.**

## Phase 9 — Economic Projections

- **Objectif** : extraire les projections quantitatives et leurs révisions
  (GDP, inflation, core inflation, unemployment, autres variables publiées).
- **Périmètre** : conserver période, valeur, publication, projection
  précédente, révision.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4.
- **Critères de validation** : les révisions sont calculées entre publications
  comparables et proviennent de la provenance.
- **Statut** : `COMPLETE` — extracteur `EcbProjectionsExtractor` (v9.0.0,
  `src/argus/projections/`). Implémenté : extraction **pilotée par les
  tableaux** (`NormalizedDocument.tables`, `DocumentTable` : colonnes = années,
  lignes = variables, cellules = valeurs) qui préserve l'intégrité
  variable × année × valeur × unité du document réel (« ECB / Eurosystem staff
  macroeconomic projections for the euro area »), variables cœur extraites
  (`HICP` → `inflation`, `HICP excluding energy and food` → `core_inflation`,
  `Real GDP` → `gdp`, prédicat `projection`, période `year:` depuis les
  en-têtes de tableau, `source_location` `table`/`row`/`column`,
  `extraction_method = table_extraction`, `confidence = HIGH`), unités
  conservées explicitement (pourcentages annuels ; les **révisions** — bloc
  « Revisions vs {Mois Année} » explicitement publié, en points de pourcentage —
  en prédicat `revision` avec `unit = "pp"`, **jamais converties** en points de
  base), **révisions jamais calculées** (aucune soustraction
  `projections courantes − précédentes` : seules les révisions explicitement
  publiées sont extraites, et les projections courantes/précédentes sont
  distinguées par `identity_qualifier` `projections:{current|yyyy-mm}` /
  `projections:revision_vs:{yyyy-mm}`), **garde de valeur** (une cellule nue
  sans variable + année + unité n'est jamais un fait — `UNKNOWN ≠ PROJECTION` :
  colonnes de scénarios sans années, lignes sans libellé, table « Technical
  assumptions » et sections méthodologie/disclaimer/notice légale ignorées),
  `Fact.speaker` toujours `None`, gating strict par classification
  (`economic_projections`), persistance idempotente. 4 fixtures (`ecb_projections.html`,
  `ecb_projections_revisions.html`, `ecb_projections_ambiguous.html`,
  `ecb_projections_minimal.html`), golden tests (valeurs exactes par
  variable/année, provenance verbatim table/ligne/colonne, unités, révisions
  explicites vs non calculées, aucun fait inventé), extraction déterministe,
  persistance idempotente (résultats vides compris), gating par classification
  et coexistence Phases 5/6/7/8 vérifiées ; `docs/EXTRACTORS.md` documente la
  couverture réelle.

## Phase 10 — Monetary Policy Reports

- **Objectif** : extraire les informations de contexte : economic outlook,
  inflation drivers, growth outlook, labour market, financial conditions,
  risks, policy rationale.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4.
- **Critères de validation** : les affirmations contextuelles sont reliées à
  leur source.
- **Statut** : `COMPLETE` — extracteur `EcbReportsExtractor` (v10.0.0,
  `src/argus/reports/`), validé (hardening final inclus). Implémenté :
  routage **conservateur** par titre de
  section (titres économiques connus extraits — activité économique, prix et
  coûts, développements financiers, développements budgétaires, développements
  de politique monétaire, évaluation des risques, overview — ; titre du
  bulletin, avant-propos, notice légale, statistiques, annexes, méthodologie,
  boîtes analytiques (« Box N — … ») et **titres inconnus ignorés** —
  `UNKNOWN ≠ ECONOMIC`), classification **content-first** par phrase avec
  précédence déterministe (guidance > policy > risk > financial > inflation >
  labour > growth > fiscal), affirmations contextuelles reliées à leur source
  (sujets inflation / core inflation / inflation expectations / croissance
  (`growth` qualitatif, `gdp` quantitatif) / labour market / unemployment /
  wages / financial conditions / fiscal_policy / monetary_policy /
  policy_guidance / risk / inflation_risk / growth_risk, prédicats
  `assessment` / `statement` / `value`), valeurs quantitatives à partir de
  revendications de valeur explicites uniquement (verbe de revendication +
  pourcentage + période de référence explicite année/mois/trimestre depuis le
  libellé ; **unités de part (« % of GDP ») jamais converties en pourcentage** ;
  **prévision sans période de référence ignorée** ; points de base jamais
  pourcentages), évaluation des risques (orientations catégoriques
  upside/downside/balanced uniquement quand l'orientation est énoncée,
  sinon texte verbatim), **tableaux de données** avec garde d'unité par
  légende (unités de part rejetées, marqueurs de pourcentage acceptés, unités
  incompatibles rejetées, ligne de variable reconnue + colonnes d'années
  requises, `table`/`row`/`column` provenance), déduplication intra-course
  (la même assertion répétée en prose + tableau = un fait), `Fact.speaker`
  toujours `None` et `effective_date` toujours `None` (publication
  institutionnelle collective), `identity_qualifier`
  `report:{subject}:{ordinal}`, gating strict par classification
  (`monetary_policy_report`), persistance idempotente (résultats vides
  compris). **Hardening** : routage des titres par correspondance
  **exacte** sur les titres contrôlés, pour les sections **économiques et
  ignorées** (plus aucun routage par sous-chaîne — « Risk management »,
  « Non-economic developments », « Fiscal institutions »,
  « Employment policy », « Output developments », « Financial institutions »,
  « Core developments », « Economic history », « Legal framework for monetary
  policy », « Annexation of financial conditions » → 0 fait, testé ; les
  titres non-économiques connus forment un vocabulaire contrôlé exact
  `_IGNORE_HEADINGS`, les titres hors vocabulaire sont `UNKNOWN`) et ancres de
  contenu **contextuelles** (multi-mots requis : `core inflation`/`core hicp`,
  `real output`/`output growth`/`output gap(s)`/`potential output`,
  `(economic) activity`, `bank lending`/`lending to …`, `yield|credit|
  sovereign|bond|rate spreads`, `monetary policy transmission`,
  `funding conditions|costs|markets|constraints|gaps`, terme de politique
  **monétaire-spécifique** — `policy`/`rate` seuls retirés —, `risks to/for/
  around/surrounding/from/of/are/were/remain…` ou qualificatif directionnel,
  `employment`/`wage(s)` excluant `policy`). 5 fixtures
  (`ecb_report.html`, `ecb_report_tables.html`,
  `ecb_report_risks.html`, `ecb_report_unknown.html`,
  `ecb_report_minimal.html`), golden tests (valeurs exactes, provenance
  verbatim section/tableau, routage, garde de valeur, déduplication, aucun
  fait inventé), tests near-miss (titres et contenu, matching IGNORE exact),
  extraction déterministe,
  persistance idempotente, gating par
  classification et coexistence Phases 5/6/7/8/9 vérifiées ;
  `docs/EXTRACTORS.md` documente la couverture réelle.

### Phase 4.x — Extension multi-banques des Reports

- **Statut** : `COMPLETE` — la famille `monetary_policy_report` est étendue de
  2 à **7 banques** : ECB (Economic Bulletin, conservé) et Norges (Monetary
  Policy Report + contenu mixte, conservé), auxquelles s'ajoutent **BoE**
  (`BoeReportExtractor` v10.2.0), **BoC** (`BocReportExtractor` v10.3.0),
  **RBA** (`RbaReportExtractor` v10.4.0, Statement on Monetary Policy),
  **RBNZ** (`RbnzReportExtractor` v10.5.0, Monetary Policy Statement) et
  **Riksbank** (`RiksbankReportExtractor` v10.6.0 — voir bullet dédié ci-dessous) —
  les cinq banques déjà **classifiées** `monetary_policy_report` sans extracteur.
- Extracteurs **banque-spécifiques** partageant uniquement des helpers
  **structurels** (`src/argus/reports/_shared.py` : normalisation des titres,
  découpage des phrases, gate de valeur explicite, émission déterministe avec
  processus/déduplication) ; aucune sémantique banque-spécifique dans le code
  partagé. Pas de symétrie artificielle : Fed, BoJ et SNB restent
  `not applicable` / représentés par une autre famille (raisons documentées).
- **Extension Riksbank** : `RiksbankReportExtractor` v10.6.0
  (`src/argus/reports/riksbank.py`, dispatch générique enregistré) — la banque
  est déjà classifiée `monetary_policy_report` par la règle générique
  `url_pattern`. Vocabulaire propre : **CPIF** (mesure cible → `inflation`),
  inflation sous-jacente / CPIF hors énergie (→ `core_inflation`), Executive
  Board comme instance de décision. Le récit de la décision reste verbatim
  `monetary_policy/statement` et n'est jamais « tarifé » (frontière Phase 5) ;
  la section `forecast tables` n'est jamais extraite (frontière Phase 9) ; les
  tirets décoratifs des titres (`—` `–` `-` `−`) sont normalisés. Fixture
  `riksbank_report.html` (15 faits, aucun warning), suite dédiée
  `tests/test_reports_riksbank.py`, contrat `docs/REPORTS.md`.
- Contract Fact canonique respecté : banque, sujet, prédicat, valeur, kind,
  unité, période, provenance publication/document, `speaker`/`effective_date`
  `None`, qualificateur `report:{subject}:{ordinal}`. Valeurs supportées par la
  source uniquement ; prévision sans période ignorée ; parts (« % of GDP »)
  jamais des pourcentages ; projection vs observation respecté.
- 4 fixtures (`boe_report.html`, `boc_report.html`, `rba_report.html`,
  `rbnz_report.html`), tests de contrat / dispatch générique / provenance /
  limites / déterminisme (répétition + indépendance d'ordre) / immutabilité /
  intégration (publication → classification → extracteur → faits →
  persistance → récupération) dans `tests/test_reports_multibank.py`.
- 67 nouveaux tests verts et déterministes (957 → 1024), `compileall` propre.
  **Extension Riksbank** : 25 nouveaux tests dédiés
  (`tests/test_reports_riksbank.py`), dispatch générique mis à jour (7 banques).
  **Phase 16 (validation historique) reste `DEFERRED`.**

## Phase 11 — Speeches & Interviews

- **Objectif** : conserver speaker, role, date, event, audience, topic,
  statement.
- **Périmètre** : les communications individuelles ne doivent pas être
  assimilées à des décisions collectives.
- **Livrables** : extracteur, tests, documentation.
- **Dépendances** : Phase 4.
- **Critères de validation** : la nature individuelle est toujours conservée
  dans la provenance.
- **Statut** : `COMPLETE` — **Speeches** implémentées et validées
  (`EcbSpeechExtractor` v11.0.0, `src/argus/speeches/`) :
  gate sur la classification `speech` (la cache `publication_type` ne suffit
  jamais), speaker **explicite uniquement** (ligne `Speaker:` du corps > auteur
  des métadonnées, jamais inféré, citations d'autrui jamais attribuées au
  locuteur), routage exact par titre (titre économique connu → extrait en
  plein ; titre non-économique connu — biographie, remerciements, remarques de
  clôture, Q&A, annexes légales — et boîtes analytiques → ignorés ; titre
  inconnu → extraction stricte, assertions explicites uniquement),
  classification content-first (guidance > policy > risk > financial >
  inflation > labour > growth), gate de valeur (pourcentages avec période
  explicite uniquement, parts/ratios jamais convertis, forecasts sans période
  ignorés), orientations de risque catégoriques uniquement quand explicites,
  déduplication intra-exécution, provenance verbatim avec `speaker` et
  qualificateurs `speech:{subject}:{ordinal}`, aucun sujet des phases 5–10.
  **Durcissement final validé** : gate qualitatif (assertion explicite requise,
  platitudes rejetées), ancres génériques remplacées ou supprimées (credit
  contextuel, demand qualifié, production sectorielle, output nu,
  recovery/recession/slowdown/expansion retirés), fixture adversariale et
  matrice de faux positifs, 513 tests verts et déterministes.
  **Interviews** (`interview`) : hors périmètre, publication type distincte.
  **Extension multi-banques (Phase 4.x — Speech family)** : ECB conservé comme
  implémentation de référence ; ajout des extracteurs Fed, BoE, BoJ, SNB, BoC,
  RBA, RBNZ, Norges, Riksbank partageant les mécanismes structurels
  `_shared.py` / `_pipeline.py` (`SpeechExtractorBase`) — vocabulaire, ancres et
  règles de classification propres à chaque banque, source officielle vérifiée
  (`COVERAGE_SOURCE`). Aucun changement de numérotation de phase.

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
- **Statut** : `COMPLETE`.

### Phase 12 — Livré (validation finale)

- **Module** : `src/argus/changes/` — `FactChange`, `ChangeType`
  (`numeric_changed` / `qualitative_changed` / `text_changed`),
  `FactChangeAnalyzer` (analyse pure, déterministe), `change_id_of`
  (identité déterministe SHA-256 sur les deux faits sources + type), et
  `analyze_changes(store, *, bank)` (persistance idempotente par
  `fact_changes`, table et méthodes dédiées dans `Store`).
- **Règles de matching** : clé `(central_bank, subject, predicate,
  value.kind, period.canonical(), identity_qualifier, publication_type)` ;
  comparaison **uniquement entre publications différentes** ; ordre par
  référence temporelle (`meeting_date` sinon `publication_date`, départage par
  `publication_id`) ; chaînage **consécutif** (F1→F2, F2→F3, jamais baseline
  fixe, **jamais de pont** par-dessus une observation incomparable) ; valeur
  identique → **aucun changement** ; mismatch de période (2027 vs 2028), de
  type de publication ou de `identity_qualifier` → aucun changement.
- **Classification = source de vérité** : le type de publication du matching
  provient de la table **`classifications`** (jamais du cache dénormalisé
  `publications.publication_type` quand une classification canonique existe) ;
  une publication sans classification canonique est **ignorée**
  (`missing_classification` / `unclassified_publication`) — `UNKNOWN >
  INVENTION`, cache périmé jamais prioritaire.
- **Repli `central_bank`** : `FactChange.central_bank = Fact.central_bank` sinon
  `Publication.central_bank`, jamais inventé si les deux sont absents.
- **Provenance complète et vérifiée** : les **deux** côtés portent fact_id,
  document_id, publication_id, période, `effective_date`, `source_text`
  (verbatim) et `value` — remontée sans ambiguïté `Change → Fact →
  publication/document`.
- **Strictement descriptif** : `delta = current − previous` (même kind/unité,
  arrondi à 10 décimales), aucun contenu économique (pas de hawkish/dovish, pas
  de tightening/easing), aucun scoring, pas de comparateur sémantique/fuzzy/LLM.
- **Provenance** : chaque changement garde les **deux** `fact_id`, les
  `document_id`, `publication_id`, périodes, `effective_date` et `source_text`
  des deux côtés ; les faits sources ne sont jamais mutés.
- **Persistance** : `fact_changes` dérivée — recomputation complète du scope
  banque (ou global) et remplacement (idempotent, vide → purge du scope,
  isolation inter-banques).
- **Durcissement profond (validation finale)** : `analyze_changes` charge la
  classification autoritative via `Store.list_classifications` et retourne un
  `FactChangeResult` (changes + warnings `missing_publication` /
  `undocumented_fact` / `missing_classification` / `unclassified_publication` /
  `undated_publication` / `valueless_fact`) ; `identity_qualifier` normalisé
  (`None` ≡ `""`), unités incompatibles jamais sommées, `effective_date`
  distincte de l'ordre et de la période, frontière de type de publication
  verrouillée (decision↔speech, minutes↔decision incomparables), `change_id`
  directionnel et déterministe, faits sources immuables (snapshot), persistance
  idempotente/reconstruible isolée par banque ; `analysis_version` v12.1.0.
- **Tests** : `tests/test_changes.py` (100 tests) — les trois types de
  changement, deltas, no-change exacts, matching, classification source de
  vérité (normal / cache périmé / absente / inconnue), repli `central_bank`,
  provenance exhaustive par type, `source_text` verbatim, observation
  incomparable sans pont, unités, `effective_date`, frontière de type,
  qualificateur, période, chaînage F1→F4, identité directionnelle, immuabilité,
  avertissements, persistance et coexistence Phases 5–11. **Suite complète :
  685 tests verts et déterministes.**

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
- **Statut** : `COMPLETE` (durcissement validé — voir « Current Position »).

## Phase 14 — Monetary Policy State

- **Objectif** : construire un état synthétique et historisé de la politique
  monétaire.
- **Modèle cible** :
  ```
  Policy State
  ├── rate_level            (policy_rate, main_refinancing_rate,
  │                          deposit_facility_rate, marginal_lending_rate)
  ├── guidance
  ├── asset_purchase
  ├── inflation_risk / growth_risk / risk
  └── as_of                 (observed_at)
  ```
- **Déviations documentées** : le modèle cible initial listait `stance`,
  `direction`, `rate_expectation`, `labour_risk`, `confidence`. Ces dimensions
  ne sont **pas structurées par les données actuelles** (aucun sujet/fait
  observé correspondant) → exclues de la Phase 14, listées comme gaps explicites
  (`unknown > invention`), jamais inventées.
- **Livrables** : constructeur d'état, historisation, tests, documentation.
- **Dépendances** : Phase 12 (source des `FactChange`) et vocabulaire de la
  Phase 13 (`STATE_SUBJECTS = REACTION_SUBJECTS`).
- **Critères de validation** : chaque état est daté (`observed_at`), historisé,
  remontable aux faits, sans look-ahead ni interprétation.
- **Statut** : `COMPLETE` (voir « Current Position »).

## Phase 15 — Forex Fundamentals

- **Objectif** : transformer les états monétaires (Phase 14) et les observations
  macro (Facts, Phase 4) en une couche de **fondamentaux Forex structurés,
  comparables, traçables**, avec différentiels inter-économies **descriptifs,
  déterministes, explicables, temporels, traçables** — jamais trading/signal/
  forecast/fair value/conviction.
- **Dimensions** : `FUNDAMENTAL_SUBJECTS = MACRO_SUBJECTS (Phase 13 condition)
  ∪ MONETARY_SUBJECTS (Phase 14 state)`. Une dimension = lineage indépendant de
  la devise : subject, predicate, value_kind, période canonique, qualifier,
  publication_type. Prédicats exclus (macro) : `projection`, `change`, `date`.
- **Sources** : les fondamentaux monétaires viennent des `MonetaryPolicyState`
  (Phase 14) — jamais reconstruits depuis les documents — et les fondamentaux
  macro des `Fact` (Phase 4) — modèle latest-known-observation. La devise est
  résolue via la relation canonique `CentralBank.currency` (une économie = une
  devise ; `unknown_currency` sinon).
- **Temporel** : `observed_at` = référence temporelle de la publication source
  (`meeting_date` sinon `publication_date`) ; `effective_date`/`period` jamais
  des temps d'observation ; aucun look-ahead (`get_fundamentals_as_of`,
  `get_differential_as_of`).
- **Différentiels** : même lineage, deux économies, ordonnés (base/quote, jamais
  inversés silencieusement), ancrés sur l'observation base (quote = dernière
  observation ≤ `base_observed_at`), valeur = `base_value − quote_value`
  (arithmétique, même unité/kind, aucune conversion). Les deux orientations
  A−B et B−A sont générées, identités distinctes. Gate de comparabilité stricte :
  lineage partagé, unité identique, kind numérique. Dimensions texte/
  qualitatives observées mais non différentiables (propriété documentée) ;
  mismatch d'unité = `incomparable_differential` ; quote manquante à l'ancre =
  `missing_side`. **Aucun instrument fusionné** (ex. `deposit_facility_rate`
  ECB vs `policy_rate` Fed = lineages différents → pas de comparaison).
- **Gaps documentés** : comparaisons cross-instrument (nécessitent un mapping
  explicite des familles d'instruments — phase future) ; dimensions macro non
  structurées (`consumption`, `investment`, `trade`, `current_account`,
  `productivity`, `labour_risk`, `fiscal_stance`, `yield_curves`,
  `market_pricing`) ; pourcentages/annualisation/z-scores/rankings (aucune
  normalisation au-delà de la différence arithmétique) ; **données réelles** :
  seuls les extracteurs ECB produisent des faits → différentiels réels
  ECB-vs-Fed impossibles aujourd'hui (gap documenté, pas contourné).
- **Livrables** : `src/argus/forex/` (`base.py`, `identity.py`, `analyzer.py`),
  tables dérivées `forex_fundamentals` + `forex_differentials` (rebuild
  idempotent par devise, résultat vide = scope vidé, provenance complète des
  deux côtés), `tests/test_forex_fundamentals.py` (89 tests), contrat
  `docs/FOREX_FUNDAMENTALS.md`, documentation.
- **Dépendances** : Phase 14 (états), Phase 4 (faits), Phase 12/13 (vocabulaires
  canoniques), `CentralBank.currency` (adapters/registry).
- **Critères de validation** : chaque fondamental est daté, historisé, remontable
  à son observation source ; chaque différentiel est même-dimension,
  arithmétique, sans look-ahead, avec provenance des deux côtés ; aucun
  contenu interdit (hawkish/dovish, forecast, fair value, signal, conviction,
  ranking).
- **Statut** : `COMPLETE` (voir « Current Position »).

## Phase 16 — Historical Validation

- **Objectif** : tester Argus sur l'historique : Historical publications →
  facts → states → changes.
- **Périmètre** : vérifier cohérence temporelle, absence de look-ahead, absence
  de duplication, provenance complète, reproductibilité, stabilité des
  classifications et des extractions.
- **Livrables** : jeu de validation historique, tests, documentation.
- **Dépendances** : Phases 4–15.
- **Critères de validation** : aucun look-ahead ; résultats reproductibles.
- **Statut** : `DEFERRED`.

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
- **Vocabulaire** : « state » (Phase 14) est maintenant implémenté
  (`src/argus/states/`, `docs/MONETARY_POLICY_STATE.md`) : il consomme les
  types de valeur explicites déjà définis dès la Phase 4 (`value.kind`,
  `unit`, `FactPeriod`) et le vocabulaire de dimension de la Phase 13 ; les
  dimensions cible non structurées par les données (`stance`, `direction`,
  `rate_expectation`, `labour_risk`, `confidence`) restent des gaps
  documentés, jamais inventés.
- **Extensibilité** : l'architecture cible et la Phase 1 sont alignées sur
  l'exigence « configuration déclarative » ; aucune banque initiale ne
  nécessite un codage particulier du cœur, vérifié pour les 10 banques du
  périmètre.

## Current Position

- Argus est à la fin de la **Phase 11 (Speeches)** — marquée `COMPLETE`
  (durcissement final validé) ; **Interviews** hors périmètre.
- Les phases 0 à 10 sont marquées `COMPLETE` après vérification du repository
  (adapters, discovery, collector/fetcher, normalization, classification,
  modèle `Fact`, extracteurs ECB `EcbDecisionExtractor` v5.2.0,
  `EcbMonetaryPolicyStatementExtractor` v6.0.0,
  `EcbPressConferenceExtractor` v7.0.0, `EcbMinutesExtractor` v8.0.0,
  `EcbProjectionsExtractor` v9.0.0 et `EcbReportsExtractor` v10.0.0 —
  hardening final validé : matching exact des titres économiques **et**
  ignorés, ancres de contenu contextuelles, 440 tests verts et
  déterministes —, tables SQLite correspondantes, tests).
- **Phase 11 (Speeches)** : `EcbSpeechExtractor` v11.0.0 (`src/argus/speeches/`)
  validé — gate `speech`, speaker explicite, routage exact conservateur
  (titre économique / non-économique / inconnu strict), précision sur rappel,
  **durcissement** (gate qualitatif : assertion explicite requise, platitudes
  rejetées ; ancres génériques remplacées ou supprimées), 513 tests verts et
  déterministes.
- **Extension multi-banques Speeches (Phase 4.x)** : ECB conservé comme
  référence ; **Fed, BoE, BoJ, SNB, BoC, RBA, RBNZ, Norges, Riksbank** ajoutés
  (`src/argus/speeches/{fed,boe,boj,snb,boc,rba,rbnz,norges,riksbank}.py`),
  partageant `_shared.py` + `_pipeline.py` (`SpeechExtractorBase`),
  vocabulaire/ancres/classification propres à chaque banque (`COVERAGE_SOURCE`),
  fixtures + tests multi-banques. 1232 tests verts au total.
- **Phase 12 (Temporal / Cross-Publication Analysis)** : `src/argus/changes/`
  validé (**durcissement profond**) — `FactChangeAnalyzer` pur et déterministe,
  trois types de changement, matching exact (jamais de comparateur flou/LLM),
  **classification source de vérité** (table `classifications`, cache périmé
  ignoré), repli `central_bank`, chaînage consécutif F1→F2→F3 sans pont sur
  observation incomparable, provenance exhaustive aux deux faits sources,
  `fact_changes` persistée de façon idempotente, reconstruisible et isolée par
  banque, aucun contenu économique ; 100 tests dédiés.
- **PRE-PHASE-13 HARDENING** (Phases 6–12, avant Phase 13) : durcissement de
  précision des extracteurs existants, sans Phase 13 — ancres de risque des
  Phases 6/7/8 alignées sur la norme contrôlée des Phases 10/11 (plus de
  `\brisk` nu : « risky », « risk-free », « riskiness » ne sont jamais des
  ancres) ; routage des titres des Phases 6/7/8 converti en **identité exacte
  sur titre nettoyé** (fini le sous-chaîne : « Non-economic developments » ne
  mappe plus `economic`, « Risk management » ne mappe plus `risk`,
  « Introductory note » / « Questions and answers on monetary policy » ne
  mappent plus les titres connus) ; marqueurs Q&A Phase 7 bornés au format
  labellisé « Question : » / « Answer : » (une phrase naturelle « Question
  marks remain … » n'est jamais un marqueur) ; ancrage `gdp` Phase 10 protégé
  des near-misses « GDP deflator », « GDP per capita », « per capita GDP » ;
  gates de refus Phases 7–11 testés (un refus ne supprime jamais les faits
  d'une extraction antérieure autorisée) ; surface API Phase 12 complétée
  (`persist=False`, `limit`, `delete_changes_for_document` /
  `delete_changes_for_publication`, `created_at` préservé, deltas CURRENCY /
  DATE, entrée vide) ; attributions minutes `one_member` / `voted against`
  testées. **674 tests verts et déterministes** (documenté dans
  `docs/CHANGES.md`).
- **PHASE 8 CORRECTIVE** (avant Phase 13) : routage IGNORE des Minutes rendu
  explicite — les headings non économiques connus sont routés par **identité
  exacte** sur le titre nettoyé (`_IGNORE_HEADINGS`) plus les **familles de
  titre** « Account of the monetary policy meeting … » et « Minutes of … »
  (préfixe explicite) ; **plus aucun routage par sous-chaîne**
  (« External monetary policy developments », « Statistical annexes »,
  « Copyright notice », « Disclaimer and legal notice » ne sont jamais lus
  comme les headings contrôlés, et jamais comme ECONOMIC — ils restent
  inconnus/ignorés, 0 fait) ; les familles de titre et tous les headings
  économiques contrôlés restent intacts (testé : identité exacte IGNORE,
  near-misses, familles, déterminisme catégoriel). **680 tests verts et
  déterministes**.
- **PHASES 9–12 — DERNIER PASS DE DURCISSEMENT** (avant Phase 13) : la garde
  near-miss GDP des discours (Phase 11) est alignée sur les rapports
  (Phase 10) — « GDP deflator », « GDP per capita » et « per capita GDP » ne
  sont jamais des ancres de croissance et ne fuient jamais en valeur `gdp`,
  même dans une phrase qui mentionne par ailleurs la croissance ; la matrice
  de gating Phase 9 est complétée par la variante explicite `unknown`
  (UNKNOWN ≠ ECONOMIC : une classification `unknown` refuse l'extraction même
  avec un cache `economic_projections`, et un refus ne supprime jamais les
  faits d'une extraction antérieure autorisée) ; audits Phases 9–12 vérifiés
  (gating A–E, tables/unités, near-misses variables, déterminisme,
  routage par identité exacte, ancres de contenu, gate de valeur, attribution
  orateur explicite, dédup intra-run, clé de matching Phase 12 exacte,
  chaînage consécutif F1→F2→F3 sans pont, règles delta, provenance verbatim,
  persistance idempotente, isolation par banque). **685 tests verts et
  déterministes**.
- **PHASE 13 — POLICY REACTION FUNCTION (COMPLETE / FROZEN)** : `src/argus/reactions/`
  validé — `PolicyReactionAnalyzer` pur et déterministe (v13.0.0), relation
  **dérivée et inférée** `condition change → policy change` (`inferred=True`
  constant, jamais un `Fact`, formulation explicitement **non-causale**) ;
  vocabulaire canonique vérifié (10 sujets condition : inflation,
  core_inflation, inflation_expectations, gdp, growth, unemployment, wages,
  labour_market, financial_conditions, fiscal_policy ; 9 sujets réaction :
  policy_rate, main_refinancing_rate, deposit_facility_rate,
  marginal_lending_rate, policy_guidance, asset_purchase, risk,
  inflation_risk, growth_risk — les risk assessments sont réaction seule,
  jamais condition, choix documenté) ; règle temporelle `meeting_date` sinon
  `publication_date`, **no-look-ahead** (`condition_observed_at ≤
  policy_observed_at`), fenêtre `0 ≤ lag_days ≤ max_lag_days` (constante
  documentée `DEFAULT_MAX_LAG_DAYS = 180`, paramètre explicite jamais ajusté
  sur données) ; **isolation stricte par `central_bank`** — propriété du
  `FactChange`, jamais résolue depuis la publication (un changement sans
  `central_bank` est ignoré : `unplaced_change:<change_id>`) ; identité
  déterministe `reaction_id` = SHA-256(central_bank,
  condition_change_id, policy_change_id) ; pairement exhaustif (toute paire
  éligible même banque → exactement une réaction) ; provenance verbatim
  dénormalisée des deux côtés jusqu'aux `FactChange`/`Fact`/publications ;
  avertissements observabilité (`missing_publication:<id>` — `change_id` si le
  changement n'a pas de `current_publication_id`, sinon id de publication ;
  `undated_publication`, `unplaced_change`) ; table `policy_reactions` persistée
  de façon idempotente
  (`rebuild_reactions` par banque, vide-efface, `created_at` préservé, delete
  par document/publication/banque) ; aucune mutation de `Fact`/`FactChange`,
  aucun hawkish/dovish, aucun signal trading/forex, aucun LLM/flou/sémantique,
  aucune causalité, aucun look-ahead ; 8 fixtures golden/adversarial, tests
  négatifs explicites, isolation cross-banque, déterminisme ×2. **65 tests
  dédiés — suite complète : 750 tests verts et déterministes.**
- **PHASE 14 — MONETARY POLICY STATE (COMPLETE)** : `src/argus/states/`
  validé — `MonetaryPolicyStateAnalyzer` pur et déterministe (v14.0.0), état
  **dérivé, daté et synthétisé** des dimensions de politique monétaire
  observables (`synthesized=True` constant, jamais un `Fact`, jamais une
  interprétation) ; dimensions = vocabulaire réaction Phase 13
  (`STATE_SUBJECTS = REACTION_SUBJECTS` : policy_rate,
  main_refinancing_rate, deposit_facility_rate, marginal_lending_rate,
  policy_guidance, asset_purchase, risk, inflation_risk, growth_risk) ;
  **source = Phase 12** — chaque `FactChange` de dimension produit exactement
  un état, la valeur = côté courant du changement (verbatim, jamais inventée,
  jamais convertie, les taux ne sont jamais réduits à un seul) ; règle
  temporelle `meeting_date` sinon `publication_date`, **`effective_date` et
  `period` jamais dates d'observation** (conservées séparées) ; lignées
  « projection » exclues (`out_of_scope_change:<change_id>`) ; isolation
  stricte par `central_bank` (propriété du `FactChange`, jamais de la
  publication : `unplaced_change`) ; identité déterministe `state_id` =
  SHA-256(central_bank, source_change_id) ; **état à une date** sans look-ahead
  (`get_policy_state_as_of(bank, T)` = dernière entrée par dimension avec
  `observed_at ≤ T`, une dimension jamais observée est absente, `unknown >
  invention`) ; provenance verbatim dénormalisée du côté courant jusqu'au
  `FactChange`/`Fact`/publications, type de publication autoritatif
  (classifications, jamais le cache dénormalisé) ; avertissements
  observabilité (`missing_publication`, `undated_publication`,
  `unplaced_change`, `valueless_change`, `missing_classification`,
  `out_of_scope_change`) ; table `monetary_policy_states` persistée de façon
  idempotente (`rebuild_policy_states` par banque, vide-efface, `created_at`
  préservé, delete par document/publication/banque) ; aucune mutation de
  `Fact`/`FactChange`/`PolicyReaction`, aucun hawkish/dovish, aucun stance,
  aucun forecast, aucune comparaison inter-banques, aucun signal
  trading/forex, aucun LLM/flou/sémantique, aucune causalité ; gaps vs modèle
  cible documentés (`stance`, `direction`, `rate_expectation`, `labour_risk`,
  `confidence` non structurés par les données → exclus, jamais inventés) ; 74
  tests dédiés — **suite complète : 827 tests verts et déterministes.**
- **PHASE 15 — FOREX FUNDAMENTALS (COMPLETE)** : `src/argus/forex/` validé —
  `ForexFundamentalsAnalyzer` pur et déterministe (v15.0.0), couche
  **dérivée, datée et synthétisée** de fondamentaux + différentiels
  inter-économies (`synthesized=True` constant, jamais un `Fact`, jamais une
  interprétation) ; **dimensions = vocabulaire condition Phase 13 ∪ vocabulaire
  réaction Phase 14** (`FUNDAMENTAL_SUBJECTS = MACRO_SUBJECTS ∪
  MONETARY_SUBJECTS`, réutilisés des couches canoniques, jamais re-déclarés) ;
  une dimension = lineage indépendant de la devise (subject, predicate,
  value_kind, période canonique, qualifier, publication_type), `dimension_key`
  scopé devise + `lineage_key` indépendant ; **sources = Phase 14
  (`MonetaryPolicyState`, dimensions monétaires — jamais reconstruites depuis
  les documents) + Phase 4 (`Fact`, dimensions macro — modèle
  latest-known-observation)** ; devise résolue via la relation canonique
  `CentralBank.currency` (`SourceRegistry`, une économie = une devise,
  `unknown_currency:<bank>` sinon) ; règle temporelle `meeting_date` sinon
  `publication_date`, `effective_date`/`period` jamais dates d'observation ;
  prédicats macro exclus `FUNDAMENTAL_EXCLUDED_PREDICATES = {projection,
  change, date}` (`out_of_scope_fact:<fact_id>`, une anticipation, une variation
  et une date ne sont pas des niveaux) ; **différentiels : même lineage, deux
  économies, ordonnés (base/quote, jamais inversés silencieusement), ancrés
  base (quote = dernière observation avec `observed_at ≤ base_observed_at`,
  no-look-ahead), valeur = `base_value − quote_value` (arithmétique, même
  unité/kind, aucune conversion), les deux orientations A−B et B−A générées
  avec identités distinctes** ; gate de comparabilité stricte (lineage partagé,
  unité identique, kind numérique) — texte/qualitatif observé mais non
  différentiable (propriété documentée, jamais un warning), mismatch d'unité =
  `incomparable_differential`, quote manquante à l'ancre = `missing_side`,
  dimension absente d'un côté = absence documentée ; **aucun instrument
  fusionné** (`deposit_facility_rate` ECB vs `policy_rate` Fed = lineages
  différents → aucune comparaison) ; identités déterministes `fundamental_id` =
  SHA-256(currency, source_kind, source_id) et `differential_id` =
  SHA-256(base_currency, quote_currency, subject, predicate, base_source_id,
  quote_source_id) ; **états à une date** sans look-ahead
  (`get_fundamentals_as_of(currency, T)`, `get_differential_as_of(pair, subject,
  T)`) ; provenance verbatim dénormalisée des deux côtés jusqu'aux
  `MonetaryPolicyState`/`Fact`/publications, type de publication autoritatif
  (classifications) ; avertissements observabilité (`unknown_currency`,
  `missing_publication`, `undated_publication`, `missing_classification`,
  `unclassified_publication`, `unplaced_fact`, `valueless`,
  `out_of_scope_fact`, `missing_side`, `incomparable_differential`) ; tables
  `forex_fundamentals` + `forex_differentials` persistées de façon idempotente
  (rebuild par devise — lecture du dataset complet pour la justesse des
  différentiels, vide-efface, `created_at` préservé, delete par
  devise/document/publication) ; aucune mutation de `Fact`/`FactChange`/
  `PolicyReaction`/`MonetaryPolicyState`, aucun hawkish/dovish, aucun stance,
  aucun forecast, aucun fair value, aucun signal trading/forex, aucun ranking,
  aucune conviction, aucune causalité, aucun look-ahead, aucun
  LLM/flou/sémantique, aucun paire self ; gaps documentés (comparaisons
  cross-instrument = mapping explicite des familles d'instruments requis,
  phase future ; dimensions macro non structurées `consumption`/`investment`/
  `trade`/`current_account`/`productivity`/`labour_risk`/`fiscal_stance`/
  `yield_curves`/`market_pricing` ; aucune normalisation au-delà de la
  différence arithmétique ; **données réelles : seuls les extracteurs ECB
  produisent des faits → différentiels réels ECB-vs-Fed impossibles aujourd'hui,
  gap documenté, jamais contourné**) ; 89 tests dédiés — **suite complète :
  916 tests verts et déterministes.**
- **Prochaine phase autorisée : Phase 16 — Historical Validation** (statut
  `DEFERRED`).

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
