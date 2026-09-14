"""Query public LLM catalogs and prices without credentials.

Responsibility split:

- ``core``       HTTP access and the ``PriceSource`` contract
- ``parsing``    document readers (HTML tables, Markdown tables, price headers)
- ``pricing``    price and record shapes
- ``models``     model identity, retired-name aliases, family matching
- ``providers``  one module per vendor
- ``caching``    provider-scoped file cache
- ``updating``   Git self-update before an explicit refresh
- ``reporting``  JSON and Markdown output
- ``registry``   wiring and cross-provider queries

Adapters preserve provider-specific conditions instead of merging prices across
regions, time bands, context tiers, or promotions.
"""
