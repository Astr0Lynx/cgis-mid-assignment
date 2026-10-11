# Vendored OGC schemas

Official OGC WPS 1.0.0 and OWS 1.1.0 XML schemas (plus the W3C `xlink.xsd` / `xml.xsd` they import), copied from
http://schemas.opengis.net and http://www.w3.org, used by `tests/test_schema.py` and `scripts/validate_schema.py`.

**One change was made to the originals:** every `schemaLocation="http://..."` attribute now points to the local copy
by a relative path. The originals use absolute URLs, which makes validation depend on fetching them over HTTP at
test time; newer libxml2 builds (as on GitHub Actions) do not fetch over HTTP, so the schema then fails to load
(`VersionType does not resolve to a simple type definition`). Nothing else in these files was edited.
`test_vendored_schemas_need_no_network` guards this.
