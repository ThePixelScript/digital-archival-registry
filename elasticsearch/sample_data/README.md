# Museum Collection Sample Dataset

This directory contains curated, fictional historical artifact records for seeding the Elasticsearch index during local setup.

> [!NOTE]
> All records in `sample_artifacts.json` are explicitly **fictional and synthetic**. They are designed for demonstrating search engineering, inverted index queries, relevance ranking, and faceted aggregations. They do not represent authentic physical collection items or verified provenance records.

## Dataset Specifications

- **Total Records**: 30 diverse artifacts.
- **File**: `sample_artifacts.json`.
- **Schema Compliance**: Conforms strictly (`dynamic: "strict"`) to the mapping defined in [`artifacts_mapping.json`](file:///C:/Users/Dell/Desktop/Programming/Project/museum-collection-tracker/elasticsearch/mappings/artifacts_mapping.json).
- **Identifier Strategy**: Deterministic `artifact_id` (`art-001` to `art-030`) mapped directly to Elasticsearch `_id`, paired with unique accession numbers (`ARC-2024-001` to `ARC-2024-030`).

### Diversity Breakdown

| Dimension | Scope & Represented Values |
| :--- | :--- |
| **Categories (8)** | `Sculpture`, `Ceramics & Pottery`, `Manuscripts & Rare Texts`, `Metalwork & Arms`, `Jewelry & Regalia`, `Scientific Instruments`, `Textiles & Costumes`, `Architectural Artifacts` |
| **Historical Eras (10)** | `Bronze Age`, `Classical Antiquity`, `Tang Dynasty`, `Islamic Golden Age`, `Mesoamerican Classic`, `Medieval Europe`, `Italian Renaissance`, `Edo Period`, `Age of Enlightenment`, `Industrial Era` |
| **Cultures (15+)** | Sumerian, Ancient Egyptian, Classical & Hellenistic Greek, Roman Imperial, Tang Chinese, Song Chinese, Abbasid Caliphate, Maya, Limousin Gothic, Edo Japanese, Benin Kingdom, Viking Norse, Inca, Florentine Renaissance, Safavid Persian, Etruscan, Industrial British |
| **Condition Grades** | `Pristine`, `Excellent`, `Good`, `Fair`, `Fragile`, `Critical` |
| **Valuations** | $95,000.00 to $14,500,000.00 USD (wide distribution for sorting & range metrics) |
| **Display Status** | 26 on active exhibition, 4 in deep vault / textile / conservation storage |

### Search Capabilities Supported

1. **Full-Text Multi-Field Search**: Multi-paragraph narrative across `description`, `historical_context`, `provenance`, and `restoration_notes`.
2. **Typo Tolerance & Fuzziness**: Target terms including `astrolabe`, `cuneiform`, `obsidian`, `reliquary`, `cartouche`, `granulation`, and `chrysography`.
3. **Faceted Filtering**: Exact keyword matching on `category`, `historical_period`, `condition`, and `storage_location`.
4. **Numeric Sorting & Metrics**: Min, max, average, and sorted ranges on `estimated_value_usd` and creation year span.
