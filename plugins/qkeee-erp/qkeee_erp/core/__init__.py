"""qkeee-erp-associate — core connector package.

Holds the single shared ERPNext (Frappe REST API) client (`client.py`) and
its confirmation-token dependency (`confirm_token.py`). Domain modules under
`scripts/domains/` import from `core.client` — see that module's docstring
for the write-allowlist gate design.
"""
