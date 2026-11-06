"""qkeee-erp-associate domain modules.

Each module here corresponds to one of the domain slugs in
00-conventions.md's fixed enum (hr-payroll, accounts, mis, sales,
procurement, inventory, manufacturing, fixed-assets, system-admin,
doc-extraction, grc-audit) and declares that domain's ALLOWED_WRITE_DOCTYPES
(core.client.register_domain_allowlist()) and registers its write
operations with core/operations.py. Writes go through
operations.run_operation() / execute_write.py — a domain module has no
write function of its own.

`doc_extraction.py` is absent — that domain has no connector at all, no
doctypes to write.
"""
