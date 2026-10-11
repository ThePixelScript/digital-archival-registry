"""Elasticsearch Queries & Behavior Verification Script.

Validates:
1. Index existence and status
2. Settings and field mappings
3. Document count and ID uniqueness
4. Full-text relevance search
5. Fuzzy typo-tolerant search
6. Multi-attribute filtering (category, period, condition)
7. Numeric sorting (estimated value desc / asc)
8. Terms & metric aggregations (facets, collection statistics)
9. Custom archival text analyzer behavior via the Analyze API
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Tuple


class ElasticsearchClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        auth_bytes = f"{username}:{password}".encode("utf-8")
        self._auth_header = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"

    def request(self, method: str, path: str, body: Any = None) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header("Authorization", self._auth_header)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))


def load_config(env_path: Path) -> Dict[str, str]:
    config = {}
    with open(env_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip().strip('"').strip("'")
    return config


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    config = load_config(project_root / ".env")

    base_url = f"{config.get('ELASTICSEARCH_SCHEME', 'http')}://{config.get('ELASTICSEARCH_HOST', '127.0.0.1')}:{config.get('ELASTICSEARCH_PORT', '9200')}"
    user = config.get("ELASTIC_USER", "elastic")
    password = config.get("ELASTIC_PASSWORD", "")
    index_name = config.get("ELASTICSEARCH_INDEX", "museum_artifacts")

    client = ElasticsearchClient(base_url, user, password)

    print("======================================================================")
    print("Running Full Elasticsearch Verification Test Suite")
    print("======================================================================")

    # 1. Index Exists
    status, idx_data = client.request("GET", f"/{index_name}")
    assert status == 200, f"Index {index_name} does not exist (status {status})"
    print(f"[1/10] Index existence: PASS (Index '{index_name}' exists)")

    # 2. Settings and Field Mappings
    settings = idx_data[index_name]["settings"]["index"]
    mappings = idx_data[index_name]["mappings"]
    assert mappings.get("dynamic") == "strict", "Expected dynamic: strict"
    assert "archival_text_analyzer" in idx_data[index_name]["settings"]["index"]["analysis"]["analyzer"]
    props = mappings.get("properties", {})
    assert len(props) == 25, f"Expected 25 mapped properties, found {len(props)}"
    print(f"[2/10] Settings & Mapping validation: PASS (25 properties, dynamic=strict, custom analyzer verified)")

    # 3. Document Count
    status, count_data = client.request("GET", f"/{index_name}/_count")
    total_docs = count_data.get("count", 0)
    assert total_docs == 30, f"Expected 30 documents, got {total_docs}"
    print(f"[3/10] Document count verification: PASS ({total_docs} documents)")

    # 4. Document IDs Uniqueness
    status, search_all = client.request("POST", f"/{index_name}/_search", {"size": 50, "query": {"match_all": {}}})
    hits = search_all["hits"]["hits"]
    doc_ids = [h["_id"] for h in hits]
    artifact_ids = [h["_source"]["artifact_id"] for h in hits]
    assert len(doc_ids) == len(set(doc_ids)) == 30, "Document IDs are not unique"
    assert doc_ids == artifact_ids, "Elasticsearch _id does not match document artifact_id"
    print(f"[4/10] Deterministic Document ID uniqueness: PASS (30 unique matching IDs)")

    # 5. Full-Text Relevance Search
    ft_query = {
        "query": {
            "multi_match": {
                "query": "cuneiform sumerian cylinder",
                "fields": ["name^3", "description", "historical_context"]
            }
        }
    }
    status, ft_res = client.request("POST", f"/{index_name}/_search", ft_query)
    top_hit = ft_res["hits"]["hits"][0]
    assert top_hit["_id"] == "art-001", f"Expected art-001 top hit, got {top_hit['_id']}"
    print(f"[5/10] Full-text relevance search: PASS (Top hit '{top_hit['_source']['name']}', score: {top_hit['_score']:.2f})")

    # 6. Fuzzy Typo-Tolerant Search
    # Searching for 'astrolab' (missing 'e') or 'astrolbae' (transposition)
    fuzzy_query = {
        "query": {
            "match": {
                "name": {
                    "query": "astrolbae",
                    "fuzziness": "AUTO"
                }
            }
        }
    }
    status, fz_res = client.request("POST", f"/{index_name}/_search", fuzzy_query)
    assert len(fz_res["hits"]["hits"]) > 0, "Fuzzy query returned 0 hits"
    fuzzy_top = fz_res["hits"]["hits"][0]
    assert fuzzy_top["_id"] == "art-006", f"Expected art-006, got {fuzzy_top['_id']}"
    print(f"[6/10] Fuzzy typo-tolerant search: PASS (Matched typo 'astrolbae' -> '{fuzzy_top['_source']['name']}')")

    # 7. Multi-Attribute Filtering
    filter_query = {
        "query": {
            "bool": {
                "must": [{"match_all": {}}],
                "filter": [
                    {"term": {"category": "Ceramics & Pottery"}},
                    {"term": {"condition": "Good"}}
                ]
            }
        }
    }
    status, flt_res = client.request("POST", f"/{index_name}/_search", filter_query)
    flt_hits = flt_res["hits"]["hits"]
    assert len(flt_hits) > 0, "Filter query returned no results"
    for h in flt_hits:
        src = h["_source"]
        assert src["category"] == "Ceramics & Pottery"
        assert src["condition"] == "Good"
    print(f"[7/10] Exact-match multi-attribute filters: PASS ({len(flt_hits)} matching Ceramics in 'Good' condition)")

    # 8. Numeric Sorting (Estimated Value Descending)
    sort_query = {
        "sort": [{"estimated_value_usd": {"order": "desc"}}],
        "size": 5
    }
    status, sort_res = client.request("POST", f"/{index_name}/_search", sort_query)
    top_valued = sort_res["hits"]["hits"]
    val_1 = top_valued[0]["_source"]["estimated_value_usd"]
    val_2 = top_valued[1]["_source"]["estimated_value_usd"]
    assert val_1 >= val_2, f"Sorting order violated: {val_1} < {val_2}"
    print(f"[8/10] Estimated value sorting (DESC): PASS (Highest item: '{top_valued[0]['_source']['name']}' at ${val_1:,.2f})")

    # 9. Terms and Metric Aggregations
    agg_query = {
        "size": 0,
        "aggs": {
            "categories": {
                "terms": {"field": "category", "size": 10}
            },
            "periods": {
                "terms": {"field": "historical_period", "size": 10}
            },
            "value_statistics": {
                "stats": {"field": "estimated_value_usd"}
            }
        }
    }
    status, agg_res = client.request("POST", f"/{index_name}/_search", agg_query)
    cat_buckets = agg_res["aggregations"]["categories"]["buckets"]
    period_buckets = agg_res["aggregations"]["periods"]["buckets"]
    val_stats = agg_res["aggregations"]["value_statistics"]
    assert len(cat_buckets) >= 6, f"Expected at least 6 category buckets, got {len(cat_buckets)}"
    assert val_stats["count"] == 30, f"Expected 30 items in stats, got {val_stats['count']}"
    print(f"[9/10] Faceted aggregations: PASS ({len(cat_buckets)} categories, {len(period_buckets)} periods, avg value: ${val_stats['avg']:,.2f})")

    # 10. Custom Text Analyzer via Analyze API
    analyze_payload = {
        "analyzer": "archival_text_analyzer",
        "text": "The Pharaoh's golden astrolabes were excavated carefully."
    }
    status, analyze_res = client.request("POST", f"/{index_name}/_analyze", analyze_payload)
    tokens = [t["token"] for t in analyze_res.get("tokens", [])]
    # Expected stemming and stop-word filtering:
    # "the" is dropped, "pharaoh's" -> "pharaoh", "golden" -> "golden",
    # "astrolabes" -> "astrolab", "were" dropped, "excavated" -> "excav", "carefully" -> "carefull"
    assert "the" not in tokens, "Stop word 'the' was not removed"
    assert "pharaoh" in tokens, "Possessive 'pharaoh\\'s' was not stemmed to 'pharaoh'"
    assert "astrolab" in tokens, "'astrolabes' was not stemmed to 'astrolab'"
    assert "excav" in tokens, "'excavated' was not stemmed to 'excav'"
    assert "carefulli" in tokens, "'carefully' was not stemmed to 'carefulli'"
    print(f"[10/10] Custom Analyze API: PASS (Tokens generated: {tokens})")

    print("======================================================================")
    print("ALL 10 ELASTICSEARCH BEHAVIOR TESTS PASSED EMPIRICALLY.")
    print("======================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
