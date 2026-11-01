"""Optional source/tool integrations registered into the common tool surface."""


def register_integrations(registry):
    from app.v2.entities import register_entities
    from app.v2.overview import register_overview
    from app.v2.pubmed import register_pubmed
    from app.v2.reading import register_reading
    from app.v2.skills import register_skills

    register_entities(registry)
    register_overview(registry)
    register_pubmed(registry)
    register_skills(registry)
    register_reading(registry)
