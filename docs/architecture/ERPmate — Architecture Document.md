# ERPmate — Architecture Document

**AI-native business interface and operator for ERPNext**

*Based on Nous Research Hermes Agent + controlled ERPNext capability layer (clpy)*

---

## 1. Executive Summary

ERPmate is conceived as an **AI employee for companies that use ERPNext**.

It is not primarily an ERPNext chatbot or a natural-language CRUD interface. It acts as a **functional and domain consultant plus an operator**:

- The business owner expresses business intent.
- The AI understands the intent.
- It investigates ERPNext state.
- It reasons about the required business process.
- It asks the owner for decisions when human judgment is required.
- It translates approved decisions into controlled ERPNext operations.

ERPNext remains the **system of record**. ERPmate becomes the business-facing interface that abstracts ERPNext complexity from a busy business owner.

---

## 2. Product Vision

### Core principle

> **ERPNext is the system of record, not the user interface.**

The AI employee should behave like a capable business worker rather than like a database assistant.

For example, when given a supplier invoice PDF, it should not merely create a Purchase Invoice.

It should determine:

- Whether the supplier exists.
- Whether the items exist.
- Whether a Purchase Order is relevant.
- Whether quantities and taxes are consistent.
- Whether customer-specific policies are satisfied.
- Which decisions must be made by the owner.

---

## 3. Guiding Principles

### Business intent over ERP mechanics

Expose semantic business capabilities to the agent, not raw ERPNext CRUD/API primitives.

### Human decisions remain human

The agent prepares evidence and options; the business owner retains decisions such as exceptional approvals, payments, and material stock adjustments.

### Least privilege

The agent receives only the capabilities needed for ERP operation and approved knowledge research.

### Defense in depth

Hermes profile restrictions, tool restrictions, connector authorization, customer policy, approvals, audit, and runtime isolation work together.

### ERPNext version awareness

ERPNext/Frappe knowledge is retrieved dynamically through a restricted knowledge gateway rather than attempting to encode every version into static prompts.

### Customer-specific policy

Business rules such as approval thresholds and supplier requirements are configuration, not hard-coded generic ERP behavior.

### Explainability

Important transactions should have an evidence trail showing what was observed, what was inferred, what decision was requested, and what was executed.

### Untrusted external content

PDFs, scans, ERP comments, and other retrieved content are treated as data, not as instructions.

---

# 4. High-Level Architecture

The architecture separates the AI runtime from business capabilities and from the ERPNext system of record.

```text
                         Business Owner
                              │
                              ▼
                ┌─────────────────────────┐
                │   ERPNext AI Employee   │
                │      Hermes Profile     │
                └────────────┬────────────┘
                             │
                             ▼
              Business reasoning + domain
              knowledge + customer policy
                             │
                             ▼
                ┌─────────────────────────┐
                │    Capability Gateway   │
                └────────────┬────────────┘
                             │
                ┌────────────┴────────────┐
                │                         │
                ▼                         ▼
       ERPNext business           ERPNext Knowledge
        capabilities                 Gateway
                │                         │
                ▼                         ▼
        ERP domain layer         Approved external
                │                  knowledge sources
                ▼                  (version-aware)
               clpy
                │
                ▼
        ERPNext REST API
                │
                ▼
        Customer ERPNext
           Instance
```

Authorization, validation, approval and audit apply across the execution path.

---

# 5. Major Components

## 5.1 Hermes ERPNext Employee Profile

A dedicated Hermes profile, for example:

```text
erpnext-employee
```

containing:

- ERP-specific system instructions
- Skills
- Memory
- Session state
- Configuration

The profile is the agent runtime boundary, but is **not treated as the sole hard security boundary**.

---

## 5.2 ERP Domain Layer

The business-facing capability layer between the LLM and `clpy`.

It exposes semantic operations such as:

```text
find_supplier
validate_supplier_information
prepare_purchase_invoice
get_accounts_payable
analyze_stock_position
```

rather than exposing raw ERPNext CRUD/API primitives.

---

## 5.3 clpy

`clpy` remains the controlled ERPNext integration layer.

Responsibilities include:

- ERPNext API interaction
- DocType allowlists
- Operation restrictions
- RBAC
- Read/write controls
- Confirmation/authorization tokens
- Audit integration

`clpy` is **not the business reasoning layer**.

---

## 5.4 Customer Policy Layer

Stores tenant-specific business rules, such as:

- Approval thresholds
- Supplier requirements
- Invoice rules
- Stock adjustment rules
- Payment controls
- Permitted autonomous actions
- Organization-specific policies

---

## 5.5 Document Intelligence Layer

Handles business documents such as PDFs and scans.

Responsibilities include:

- Extracting structured business facts
- Classifying documents
- Validating extracted data
- Associating source documents with ERP transactions

---

## 5.6 ERPNext Knowledge Gateway

A restricted research capability for:

- ERPNext documentation
- Frappe documentation
- Approved source material
- Version-specific behavior

The AI does **not** receive generic internet access.

The gateway performs controlled, version-aware retrieval.

---

## 5.7 Approval and Decision Layer

Represents questions requiring business-owner decisions as explicit decision requests containing:

- Evidence
- Options
- Consequences
- Requested decision

---

## 5.8 Audit and Observability

Records significant:

- Observations
- Decisions
- Approvals
- Capability invocations
- Source documents
- Validation outcomes
- ERP transaction identifiers

---

## 5.9 Isolated Runtime

For production, the ERP employee should run in a sandbox/container or equivalent isolated environment.

Network, filesystem, credentials and process capabilities should be restricted to the minimum required.

---

# 6. AI Employee Operating Model

Business capabilities are classified into five stages.

| Stage | Meaning |
|---|---|
| **READ** | Determine what is currently true. |
| **ANALYZE** | Interpret, reconcile, diagnose and validate information. |
| **PREPARE** | Work out a proposed transaction or action without changing ERPNext. |
| **PROPOSE** | Present a concrete action and request approval where required. |
| **EXECUTE** | Perform an authorized ERPNext change. |

This allows the same agent to operate autonomously where risk is low while retaining explicit human control over consequential business decisions.

---

# 7. Initial Capability Model — 50 Business Operations

The initial capability model consists of approximately 50 semantic business operations.

## 7.1 General ERP / Business Context

| # | Capability | Class |
|---|---|---|
| 1 | `get_company_context` | READ |
| 2 | `get_fiscal_period_status` | READ |
| 3 | `get_business_configuration` | READ |
| 4 | `get_workflow_status` | READ |
| 5 | `explain_erp_entity_or_process` | ANALYZE |

---

## 7.2 Supplier Management

| # | Capability | Class |
|---|---|---|
| 6 | `find_supplier` | READ |
| 7 | `get_supplier` | READ |
| 8 | `validate_supplier_information` | ANALYZE |
| 9 | `prepare_supplier` | PREPARE |
| 10 | `create_or_update_supplier` | EXECUTE |

---

## 7.3 Customer Management

| # | Capability | Class |
|---|---|---|
| 11 | `find_customer` | READ |
| 12 | `get_customer` | READ |
| 13 | `validate_customer_information` | ANALYZE |
| 14 | `create_or_update_customer` | EXECUTE |

---

## 7.4 Item Master

| # | Capability | Class |
|---|---|---|
| 15 | `find_item` | READ |
| 16 | `get_item` | READ |
| 17 | `validate_item_for_transaction` | ANALYZE |
| 18 | `create_or_update_item` | EXECUTE |

---

## 7.5 Procurement / AP

| # | Capability | Class |
|---|---|---|
| 19 | `find_purchase_order` | READ |
| 20 | `get_purchase_order` | READ |
| 21 | `match_invoice_to_purchase_order` | ANALYZE |
| 22 | `validate_supplier_invoice` | ANALYZE |
| 23 | `prepare_purchase_invoice` | PREPARE |
| 24 | `create_purchase_invoice` | EXECUTE |
| 25 | `get_accounts_payable` | READ |
| 26 | `get_supplier_statement` | READ |
| 27 | `explain_payable_or_invoice_status` | ANALYZE |

---

## 7.6 Sales / AR

| # | Capability | Class |
|---|---|---|
| 28 | `find_sales_order` | READ |
| 29 | `get_sales_order` | READ |
| 30 | `prepare_sales_invoice` | PREPARE |
| 31 | `create_sales_invoice` | EXECUTE |
| 32 | `get_accounts_receivable` | READ |
| 33 | `get_customer_statement` | READ |
| 34 | `explain_receivable_or_invoice_status` | ANALYZE |

---

## 7.7 Inventory

| # | Capability | Class |
|---|---|---|
| 35 | `get_stock_balance` | READ |
| 36 | `get_stock_ledger` | READ |
| 37 | `find_stock_movement` | READ |
| 38 | `analyze_stock_position` | ANALYZE |
| 39 | `prepare_stock_transaction` | PREPARE |
| 40 | `execute_stock_transaction` | EXECUTE |

---

## 7.8 Payments

| # | Capability | Class |
|---|---|---|
| 41 | `get_payment_status` | READ |
| 42 | `get_payment_history` | READ |
| 43 | `prepare_payment_entry` | PREPARE |
| 44 | `create_payment_entry` | EXECUTE |
| 45 | `reconcile_payment` | EXECUTE |

Payment execution should initially require explicit authorization.

---

## 7.9 Documents

| # | Capability | Class |
|---|---|---|
| 46 | `extract_business_document` | ANALYZE |
| 47 | `classify_business_document` | ANALYZE |
| 48 | `validate_extracted_document_data` | ANALYZE |
| 49 | `attach_document_to_transaction` | EXECUTE |
| 50 | `find_related_document` | READ |

---

# 8. End-to-End Example — Supplier Invoice

A representative workflow is:

```text
Supplier Invoice PDF / Scan
          │
          ▼
Document Extraction
          │
          ▼
Supplier Identification
          │
          ▼
Supplier Validation
          │
          ▼
Item Identification
          │
          ▼
Purchase Order Matching
          │
          ▼
Invoice Validation
          │
          ▼
Policy Checks
          │
          ▼
Prepare Purchase Invoice
          │
          ▼
Human Approval if Required
          │
          ▼
Create / Submit in ERPNext
          │
          ▼
AP Tracking
```

### Example interaction

The owner says:

> "Upload this invoice."

The agent might determine:

> I identified the supplier as ABC Industries. The supplier exists. The invoice appears to correspond to PO-1047, but the invoice quantity for Item X is 120 while the PO has 100. I haven't created the invoice yet. Do you want me to proceed with the 120 units?

The owner makes the business decision.

The agent then executes the authorized operation.

---

# 9. ERPNext Knowledge Gateway

The product requires external knowledge because it cannot practically encode every ERPNext/Frappe version, configuration behavior, API and implementation detail in static agent knowledge.

The ERP employee should therefore have a restricted research capability rather than a generic internet/browser capability.

For example:

```text
research_erpnext(
    question,
    version,
    context
)
```

Example:

```text
research_erpnext(
    "How does Purchase Invoice update stock?",
    version="15"
)
```

The gateway can search approved sources such as:

- Frappe documentation
- ERPNext documentation
- Approved source repositories
- Approved community sources

The gateway should:

- Be version-aware.
- Use an allowlist of approved sources.
- Prevent arbitrary web browsing.
- Keep research access separate from ERP access.
- Record sources used.
- Treat retrieved content as untrusted data rather than instructions.
- Cache frequently used knowledge where useful.

---

# 10. Security and Isolation

The production system should use defense in depth.

| Layer | Control |
|---|---|
| Hermes profile | Dedicated ERP employee profile |
| Tool minimization | Only required business capabilities |
| Capability gateway | Semantic ERP operations rather than raw API access |
| clpy | DocType allowlists, RBAC, authorization and audit |
| Credentials | Kept outside model context |
| Knowledge access | Restricted research gateway |
| Network | Only required endpoints |
| Runtime | Sandbox/container isolation |
| Tenant isolation | Separate customer data, credentials, policy and memory |
| Prompt-injection defense | External content treated as data |

---

# 11. Human Decision Boundary

The product should not attempt to remove human judgment from business operations.

Instead, routine ERP work moves to the AI employee while consequential decisions remain with the business owner.

### Usually autonomous

- Lookups
- Extraction
- Validation
- Reporting
- Matching
- Preparing drafts
- Retrieving AP/AR status

### Usually approval-based

- New supplier creation
- New item creation
- Invoice creation/submission
- Exceptional tax treatment
- Policy exceptions

### Human-controlled

- Payments
- Material stock adjustments
- Cancellation of consequential transactions
- Unusual accounting decisions

These categories are configurable through customer policy.

---

# 12. Decision Request Model

When the agent needs the owner, the interaction should be a structured decision request containing:

1. What the agent observed.
2. What it believes is happening.
3. Evidence supporting that interpretation.
4. Available options.
5. Consequences of each option where material.
6. The exact decision required.

The resulting decision should be recorded as a business decision and associated with the transaction audit trail.

---

# 13. Capability Contract

Each business capability should have a formal contract before it becomes an exposed Hermes tool.

The contract should define:

- Capability name and purpose
- Inputs and output schema
- ERPNext DocTypes and operations involved
- Read/write classification
- Risk level
- Preconditions
- Validation rules
- Customer-policy checks
- Whether autonomous execution is permitted
- Approval requirement
- Postconditions
- Audit requirements
- Underlying `clpy` operations

The capability contract is the bridge between:

```text
Product requirements
        ↓
ERP domain layer
        ↓
clpy implementation
        ↓
Hermes tool definitions
        ↓
Security policy
```

---

# 14. Example Capability Contract — `create_purchase_invoice`

| Field | Definition |
|---|---|
| Purpose | Create a Purchase Invoice from validated business information |
| Inputs | Supplier, invoice date, supplier invoice number, items, taxes, company, currency, optional Purchase Order |
| Preconditions | Supplier exists; items are valid; tax treatment is valid; mandatory fields are present; duplicate check passes; applicable customer policy is satisfied |
| Risk | Medium |
| Default execution | Approval required unless customer policy explicitly permits autonomous creation |
| Postcondition | Purchase Invoice exists in ERPNext with a traceable source and audit record |
| Audit | Source document, extracted values, validation results, approvals/decisions and resulting ERPNext identifier |

---

# 15. Customer-Specific Policy Layer

ERPNext behavior alone is insufficient because different customers have different business processes.

Policies should therefore be externalized from the model.

Examples:

- Supplier creation requires GST details.
- Invoices above a configured amount require owner approval.
- Invoices without a Purchase Order may or may not be allowed.
- New items always require approval.
- Stock adjustments require explicit approval.
- Payments can be prepared by the agent but never executed autonomously.
- Invoice submission may be autonomous after all validations pass.

---

# 16. Example Audit Trail

A transaction audit trail could look like:

```text
09:32
Document received: invoice.pdf

09:32
Supplier identified:
ABC Industries

09:33
Supplier lookup:
No exact match

09:33
Possible match found:
ABC Industries Pvt Ltd
GSTIN matched

09:34
Decision requested:
Treat invoice as existing supplier?

09:35
Owner:
YES

09:35
PO matching:
PO-1047

09:36
Exception:
Invoice quantity exceeds PO quantity

09:36
Decision requested:
Proceed despite discrepancy?

09:37
Owner:
YES

09:37
Purchase Invoice prepared

09:38
Purchase Invoice created:
PI-123
```

The audit trail should capture not only the final ERP transaction but also the reasoning-relevant evidence and human decisions that led to it.

---

# 17. What the Agent Must NOT Have

The production ERP employee should not have:

- Generic shell/terminal access.
- Arbitrary filesystem access to the host.
- Generic internet/browser capability.
- Ability to create or switch Hermes profiles.
- Unrestricted MCP servers.
- Credentials for unrelated systems.
- A generic `execute_arbitrary_erpnext_method` capability.
- Authority to bypass customer policy or approval requirements.
- Ability to treat instructions embedded in invoices, ERP comments or retrieved web content as trusted commands.

---

# 18. Product Evolution

The initial 50 capabilities should not attempt to cover all of ERPNext.

Start with complete business journeys and expand.

### Phase 1 — Procure-to-Pay

Supplier invoice → validation → PO matching → Purchase Invoice → AP tracking.

### Phase 2 — Order-to-Cash

Sales orders → Sales Invoices → AR tracking → customer statements.

### Phase 3 — Inventory

Stock visibility → movements → discrepancies → controlled adjustments.

### Phase 4 — Payments and Reconciliation

Payment preparation → approval → reconciliation.

### Phase 5 — Management Assistant

- Cash obligations
- Overdue receivables
- Supplier exposure
- Inventory risk
- Operational explanations

---

# 19. Key Architectural Decisions

| Decision | Architecture Choice |
|---|---|
| Dedicated Hermes profile | Yes — use an ERP-specific profile such as `erpnext-employee` |
| Profile as hard security boundary | No — runtime sandboxing and capability restrictions provide the hard boundary |
| `clpy` | Keep as the controlled ERPNext integration layer, not the business reasoning engine |
| Business-facing tools | Expose semantic capabilities, not raw ERPNext CRUD |
| Internet | Required for ERPNext/Frappe knowledge, but exposed only through a restricted, version-aware Knowledge Gateway |
| Autonomy | Use READ → ANALYZE → PREPARE → PROPOSE → EXECUTE stages and customer-specific policy |
| Security | Defense in depth with least privilege, isolated credentials, restricted network, sandboxing, authorization and audit |
| Human role | The owner retains consequential business decisions; the AI employee performs routine work and surfaces exceptions |

---

# 20. Immediate Next Step

The next design artifact should be the detailed **Capability Contract** for all 50 operations.

For each operation, define:

- Inputs
- Outputs
- ERPNext DocTypes
- Underlying `clpy` calls
- Preconditions
- Validation
- Risk
- Approval requirements
- Customer-policy checks
- Postconditions
- Audit behavior

That artifact becomes the bridge between product requirements, `clpy` implementation, Hermes tool definitions and the security model.