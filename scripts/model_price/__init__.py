"""Query public LLM catalogs and prices without credentials.

Responsibility split:

- ``paths``        filesystem anchors (skill dir, cache, snapshots) and cache policy
- ``errors``       the errors a price source or the updater raises
- ``core``         HTTP access and the ``PriceSource`` contract
- ``parsing``      document readers (HTML tables, Markdown tables, price headers)
- ``text``         normalisation of text scraped out of vendor documents
- ``pricing``      price and record shapes
- ``models``       model identity, retired-name aliases, family matching
- ``providers``    one module per vendor
- ``descriptions`` independent model introductions, from vendors and platforms
- ``caching``      provider-scoped file cache
- ``updating``     Git self-update before an explicit refresh
- ``snapshots``    archived baselines, retention, and point-in-time selection
- ``diffing``      what moved between two baselines
- ``delta``        the scan-every-catalogue-and-compare run
- ``reporting``    the shared wording and single-value formatters
- ``messages``     the plain-text messages those formatted values are laid into
- ``budget``       how far a message overruns the character limit it has to fit
- ``registry``     wiring and cross-provider queries

Adapters preserve provider-specific conditions instead of merging prices across
regions, time bands, context tiers, or promotions.

Character limits are never met by cutting. A message over ``--max-chars`` and an
introduction over 300 characters are both rendered whole and flagged; the
summarizing that brings them under is done by whoever sends them, since this
tool takes no credentials and has no model to ask.
"""
