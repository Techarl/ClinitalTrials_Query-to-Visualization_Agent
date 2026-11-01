"""Official NCBI search and XML abstracts with raw receipts and shared throttling."""

import json
import re
import threading
import time
import xml.etree.ElementTree as ET

import httpx

from app.sources import content_hash, utc_now
from app.v2.contracts import ArticleInput, Artifact, Coverage, LiteratureInput

_rate_lock = threading.Lock()
_last_request = 0.0
BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


class PubMedClient:
    def __init__(self, settings, root, client=None):
        self.settings, self.root = settings, root / "pubmed"
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=30)
        self.owns_client = client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    def get(self, endpoint, params, deadline):
        global _last_request
        key = content_hash([endpoint, params])
        path = self.root / (key + ".json")
        if path.exists():
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if content_hash(receipt["raw"]) != receipt["raw_hash"]:
                raise ValueError("PubMed cache integrity check failed")
            return receipt
        wire = {**params, "tool": "cheiron_trial_analysis"}
        if self.settings.ncbi_email:
            wire["email"] = self.settings.ncbi_email
        if self.settings.ncbi_api_key.get_secret_value():
            wire["api_key"] = self.settings.ncbi_api_key.get_secret_value()
        for attempt in range(3):
            with _rate_lock:
                delay = max(0, 0.36 - (time.monotonic() - _last_request))
                if time.monotonic() + delay >= deadline:
                    raise TimeoutError("PubMed request deadline exhausted")
                time.sleep(delay)
                _last_request = time.monotonic()
            try:
                response = self.client.get(
                    BASE + endpoint, params=wire, timeout=min(30, max(0.1, deadline - time.monotonic()))
                )
                response.raise_for_status()
                raw = response.text
                # Parse before caching; an upstream error body is never a valid empty result.
                if params.get("retmode") == "json":
                    parsed = response.json()
                    if "error" in parsed or "ERROR" in parsed:
                        raise ValueError("NCBI reported a source error")
                else:
                    parsed = ET.fromstring(raw)
                    if parsed.find(".//ERROR") is not None:
                        raise ValueError("NCBI reported a source error")
                receipt = {
                    "endpoint": BASE + endpoint,
                    "query": params,
                    "fetched_at": utc_now(),
                    "raw": raw,
                    "raw_hash": content_hash(raw),
                }
                temp = path.with_suffix(".pending-" + str(threading.get_ident()))
                temp.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
                temp.replace(path)
                return receipt
            except (httpx.HTTPError, ET.ParseError, ValueError):
                if attempt == 2:
                    raise
                if time.monotonic() + 2**attempt >= deadline:
                    raise TimeoutError("PubMed retry deadline exhausted")
                time.sleep(2**attempt)

    def search(self, query, limit, deadline):
        receipt = self.get(
            "esearch.fcgi",
            {"db": "pubmed", "term": query, "retmode": "json", "retmax": limit, "sort": "relevance"},
            deadline,
        )
        parsed = json.loads(receipt["raw"]).get("esearchresult")
        if not isinstance(parsed, dict) or "idlist" not in parsed:
            raise ValueError("PubMed search response is incomplete")
        return parsed["idlist"], int(parsed["count"]), receipt

    def articles(self, pmids, deadline):
        if any(not re.fullmatch(r"\d{1,12}", pmid) for pmid in pmids):
            raise ValueError("Invalid PMID")
        receipt = self.get("efetch.fcgi", {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}, deadline)
        root = ET.fromstring(receipt["raw"])
        articles = []
        for article in root.findall("PubmedArticle"):
            pmid = article.findtext("./MedlineCitation/PMID")
            title = article.find("./MedlineCitation/Article/ArticleTitle")
            abstracts = article.findall("./MedlineCitation/Article/Abstract/AbstractText")
            articles.append(
                {
                    "citation_id": "pmid:" + pmid,
                    "pmid": pmid,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "title": "".join(title.itertext()) if title is not None else "",
                    "abstract": [
                        {"label": item.get("Label"), "text": "".join(item.itertext())} for item in abstracts
                    ],
                    "journal": article.findtext("./MedlineCitation/Article/Journal/Title"),
                    "publication_types": [item.text for item in article.findall(".//PublicationType")],
                    "corrections": [
                        {
                            "type": item.get("RefType"),
                            "pmid": item.findtext("PMID"),
                            "source": item.findtext("RefSource"),
                        }
                        for item in article.findall(".//CommentsCorrections")
                    ],
                    "nct_mentions": sorted(set(re.findall(r"NCT\d{8}", "".join(article.itertext())))),
                    "content_scope": "metadata_and_available_abstract_only",
                    "source_hash": receipt["raw_hash"],
                    "fetched_at": receipt["fetched_at"],
                }
            )
        if set(pmids) != {article["pmid"] for article in articles}:
            raise ValueError("PubMed article fetch omitted requested PMIDs")
        return articles, receipt


def register_pubmed(registry):
    def publish(args, state, search):
        registry.goal(args.goal_id, state, "literature")
        client = PubMedClient(registry.settings, registry.settings.data_directory)
        receipts, total = [], None
        try:
            if search:
                pmids, total, receipt = client.search(args.query, args.limit, registry.deadline(state))
                receipts.append(receipt)
            else:
                pmids = list(dict.fromkeys(args.pmids))
            articles = []
            if pmids:
                articles, receipt = client.articles(pmids, registry.deadline(state))
                receipts.append(receipt)
        finally:
            client.close()
        source_handle = registry.store.asset(
            state["session_id"], state["run_id"], "literature", {"articles": articles, "receipts": receipts}
        )
        artifact = Artifact(
            artifact_id="",
            result_id="",
            goal_id=args.goal_id,
            source_type="pubmed",
            title="PubMed literature",
            metric="PubMed articles, separate from registered trial counts",
            coverage=Coverage(
                statistics_basis="literature",
                count_exactness="not_applicable",
                population_coverage="not_applicable",
                evidence_coverage="article_metadata",
                fetched_count=len(articles),
                matched_count=total,
            ),
            metadata={
                "articles": articles,
                "source_handle": source_handle,
                "search_query": args.query if search else None,
                "full_text_retrieved": False,
                "trial_link_policy": "NCT mentions are explicit text links, not proof that every article reports that trial.",
            },
        )
        output = registry.publish(artifact, state, {"source_handle": source_handle})
        return {
            **output,
            "articles": [
                {"pmid": item["pmid"], "title": item["title"], "citation_id": item["citation_id"]}
                for item in articles
            ],
        }

    registry.register(
        "search_pubmed",
        "Search official PubMed and retrieve metadata/available abstracts. Separate literature citations from CTgov statistics.",
        LiteratureInput,
        lambda args, state: publish(args, state, True),
    )
    registry.register(
        "fetch_articles",
        "Retrieve exact PMIDs with abstracts, publication types and correction notices. No full-text claim.",
        ArticleInput,
        lambda args, state: publish(args, state, False),
    )
