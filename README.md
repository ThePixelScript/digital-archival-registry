# Digital Archival Registry & Museum Collection Tracker

A searchable, portfolio-quality digital archive designed to discover, organize, explore, and curate museum artifacts and historical records. Built with **Elasticsearch 8.x**, **FastAPI**, and **Vanilla JavaScript**, this system demonstrates practical search engineering, document-oriented data modeling, relevance scoring, faceted aggregations, and clean modular architecture.

---

## 🏛️ Product Vision

Traditional museum collections face two common challenges: rigid relational schemas that struggle with heterogeneous archival narratives, and shallow keyword searches that fail when researchers search across fragmented provenance trails or mistype archaic terms.

The **Digital Archival Registry & Museum Collection Tracker** solves these challenges by treating archival records as rich, semi-structured documents. It pairs Elasticsearch's inverted index and BM25 relevance ranking with a lightweight, responsive interface to provide:
- **Instant full-text discovery** across multi-paragraph descriptions, historical contexts, and conservation notes.
- **Typo-tolerant matching** to handle transcription variations and archaic terms.
- **Dynamic faceted navigation** providing real-time breakdowns of collection periods, preservation conditions, and valuation statistics.
- **Curatorial management** with validated CRUD operations and strict schema contracts.

---

## 🛠️ Technology Stack

| Layer | Technology | Role & Architecture Rationale |
| :--- | :--- | :--- |
| **Search Engine & Primary Store** | **Elasticsearch 8.17.x** | Document-oriented store, custom text analyzers, inverted index, BM25 relevance scoring, fuzzy matching, and multi-dimensional aggregations. |
| **Backend API** | **Python (FastAPI)** | Async REST API, strict **Pydantic v2** data modeling, query builder service, and error handling. |
| **Frontend Client** | **HTML5 / CSS / Vanilla JS** | Zero-build, fast-loading, accessible interface with instant search feedback. |
| **Local Infrastructure** | **Docker Compose** | Pinned single-node Elasticsearch cluster with local loopback binding and volume persistence. |
| **Version Control** | **Git** | Strict branch management, semantic commits, and comprehensive `.gitignore` protection. |

---

## 📂 Project Structure

```
museum-collection-tracker/
├── .env.example                     # Environment template with documented placeholders
├── .gitignore                        # Comprehensive ignore rules (Python, Docker, IDEs, env)
├── docker-compose.yml                # Pinned Elasticsearch 8.17.2 single-node service
├── README.md                         # Product overview, architecture, and setup instructions
├── docs/
│   └── architecture.md               # Technical rationale: Elasticsearch vs. RDBMS trade-offs
├── scripts/
│   ├── initialize_elasticsearch.py   # Idempotent index creation & Bulk API seeding utility
│   └── verify_elasticsearch_queries.py # Live verification for queries, filters, sorting & facets
├── backend/
│   ├── requirements.txt              # Pinned Python dependencies (FastAPI, ES client, pytest)
│   ├── app/
│   │   ├── __init__.py               # Backend package marker
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   └── config.py             # Pydantic Settings configuration loader
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── routes/
│   │   │       └── __init__.py       # Modular API route endpoints
│   │   ├── models/
│   │   │   └── __init__.py           # Pydantic data models & search schemas
│   │   └── services/
│   │       └── __init__.py           # Elasticsearch query builder & indexing logic
│   └── tests/
│       ├── __init__.py
│       └── conftest.py               # Pytest fixtures and test environment harness
├── frontend/
│   ├── index.html                    # Semantic HTML5 archival layout
│   ├── css/
│   │   └── styles.css                # Curated museum aesthetic, tokens, and responsive layout
│   └── js/
│       └── app.js                    # Vanilla ES module client entry point
└── elasticsearch/
    ├── mappings/
    │   └── artifacts_mapping.json    # Explicit schema mapping with custom analyzers
    └── sample_data/
        ├── README.md                 # Specifications for the historical artifact dataset
        └── sample_artifacts.json     # Curated fictional dataset of 30 historical artifacts
```

---

## 🚀 Prerequisites

Ensure the following tools are installed on your host machine:
- **Docker Engine** (v24.0+) & **Docker Compose** (v2.20+)
- **Python** (v3.11, v3.12, v3.13, or v3.14)
- **Git** (v2.40+)
- Modern web browser (Chrome, Firefox, Edge, Safari)

---

## 🔒 Security & Local Development Decisions

1. **Strict Loopback Binding (`127.0.0.1:9200`)**:
   Elasticsearch is explicitly bound to `127.0.0.1` rather than `0.0.0.0`. This prevents external machines or public networks from reaching the local cluster.
2. **Basic Authentication Enforced (`xpack.security.enabled=true`)**:
   The cluster requires authentication using the `elastic` superuser and the password defined in `.env` (`ELASTIC_PASSWORD`).
3. **HTTP SSL Disabled for Local Dev (`xpack.security.http.ssl.enabled=false`)**:
   In local development, disabling SSL on the HTTP layer eliminates self-signed certificate trust issues for local Python clients while retaining strict password protection. Production environments must enforce TLS.
4. **Pinned Image Tag (`elasticsearch:8.17.2`)**:
   Floating tags such as `latest` are avoided to guarantee identical cluster behavior across environments.

---

## 📋 Core Implemented Capabilities

1. **Relevance-Ranked Full-Text Search**:
   - Multi-field query targeting: Title (`name^3`), alternate designations (`alternate_names^2`), description (`description^1.5`), tags (`tags^1.5`), historical context, provenance, materials, and restoration notes.
   - Real-time BM25 relevance scoring returned alongside each search hit.
2. **Typo-Tolerant Discovery**:
   - Optional Levenshtein edit distance fuzzy matching (`fuzziness: "AUTO"`, `prefix_length: 2`) toggled directly in the search bar.
3. **Multi-Faceted Filtering & Sorting**:
   - Dynamic facets for Archival Category, Historical Period, Conservation Condition, and Gallery Display Status.
   - Active filter chips allowing one-click removal of individual constraints.
   - Sorting by earliest creation year, estimated valuation (USD), acquisition date, title, or registry creation timestamp.
4. **Aggregated Analytics & Facet Visualizer**:
   - Elasticsearch terms and stats aggregations computed on-the-fly (`GET /api/stats`).
   - Summary metric cards (Total Artifacts, Total Valuation, Average Valuation, Range).
   - Category, historical period, and condition breakdown tables with CSS-based proportional visualizer bars.
5. **Comprehensive Artifact Detail Inspection**:
   - Accessible modal dialog inspecting all 25 mapped archival fields across chronological origin, physical measurements (H x W x D cm, weight kg), curatorial narratives, provenance chain, conservation notes, and audit timestamps.
6. **Full Curatorial CRUD Lifecycle**:
   - Register new artifacts via `POST /api/artifacts` (enforces document ID uniqueness and Pydantic validation).
   - Update existing records via `PUT /api/artifacts/{id}` with partial or full field updates.
   - Permanently decommission artifacts via `DELETE /api/artifacts/{id}` with an accessible confirmation dialog.
7. **Accessible, Zero-Build Frontend**:
   - Built entirely in semantic HTML5, CSS custom properties, and Vanilla JavaScript ES modules.
   - Strict WCAG AA contrast, keyboard accessibility (focus traps, Escape handlers, focus restoration), and live screen-reader regions.
8. **Reliable CSV Export & Spreadsheet Security**:
   - Dedicated streaming export endpoints (`GET /api/artifacts/export` and `GET /api/search/export`) utilizing the Elasticsearch Scroll API (`scroll=2m`) to export complete matching result sets beyond standard pagination limits (`max_result_window`).
   - RFC 4180 compliant CSV formatting with UTF-8 BOM (`\ufeff`) for seamless character display in Microsoft Excel, Apple Numbers, and LibreOffice Calc.
   - 28 stable, documented columns with nested physical dimensions flattened into explicit numeric fields (`dimensions_height_cm`, `dimensions_width_cm`, `dimensions_depth_cm`, `dimensions_weight_kg`).
   - Active filter awareness: one-click CSV export directly from the UI header preserving active text queries, category/period/condition/display filters, and sorting.
   - Formula injection mitigation (CWE-1236 / OWASP) neutralizing dangerous leading prefixes (`=`, `+`, `-`, `@`, `\t`, `\r`, `%`) on untrusted string fields while preserving genuine numeric and negative values.

---

## ⚙️ Setup & Execution Workflow

### 1. Environment Configuration
Create your local environment file:
```powershell
# Windows PowerShell
Copy-Item .env.example .env

# Linux / macOS
cp .env.example .env
```
*(Configure `ELASTIC_PASSWORD` in `.env` with a strong local secret)*

### 2. Start Elasticsearch via Docker Compose
```bash
docker compose up -d elasticsearch
```
Verify container status:
```bash
docker compose ps
```

### 3. Initialize Index & Ingest Dataset
Run the administrative initialization script to configure custom analyzers and ingest the 30 curated fictional records via the Bulk API:
```powershell
.\.venv\Scripts\python.exe scripts/initialize_elasticsearch.py
```

### 4. Run Automated Test Suite
Execute the 38 automated unit and integration tests covering API routing, search queries, CRUD operations, CSV export security, and frontend asset serving:
```powershell
.\.venv\Scripts\pytest.exe backend/tests -q
```

### 5. Start the Application Server
Start the FastAPI server from the repository root:
```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

Once running:
- **Interactive Web Interface**: Open [http://127.0.0.1:8000/app/](http://127.0.0.1:8000/app/) in your browser.
- **Interactive Swagger Documentation**: Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
- **Cluster Health Endpoint**: Open [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health).

*(Alternative standalone frontend dev server)*:
```powershell
python -m http.server 5500 --directory frontend
# Navigate to http://127.0.0.1:5500/
```

### 6. Run End-to-End Verification
To verify static file serving, search queries, typo tolerance, aggregations, streaming CSV export, and the full CRUD lifecycle against the live cluster:
```powershell
.\.venv\Scripts\python.exe scripts/verify_frontend_e2e.py
```

---

## 📖 Architectural Deep-Dive

For an in-depth explanation of:
- Inverted index vs. relational B-Trees
- BM25 relevance scoring mechanics
- Real-time aggregations vs. SQL `GROUP BY`
- Trade-offs between Elasticsearch and Relational Databases

See the full [Architecture Documentation](docs/architecture.md).
