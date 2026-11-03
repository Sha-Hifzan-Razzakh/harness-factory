"""Writes catalog/seeds/kb/files/corpus/: the knowledge base of Dhow Freight, a Dubai logistics and online-shop
operator, for the retrieval tasks (ADR 0017).

60 short documents under opaque names (kb-001.md ... kb-060.md), so listing the folder says nothing about what is
in a file: without search, finding an answer means reading files one by one. Each retrieval task has its answer in
one or two documents and plausible wrong answers in others (a superseded policy, a draft, a cancelled change, a
stale owner, an incident with a similar number). The rest is filler on other topics, some of it sharing words with
the questions. tasks.py imports ID (document key -> kb id) and TEXT (kb id -> file text) from here.
"""

import random
import sys
from pathlib import Path

# ------------------------------------------------------------------ documents the tasks depend on

KEY_DOCS = {
    "refunds-2026": ("Customer refund policy, version 3", "2026-03-01", """
Status: in effect from 1 March 2026. This version supersedes version 2 (2024).

Who can approve a refund. A support agent may approve a refund of up to 750 AED per order without a manager's
sign-off. A refund above 750 AED needs approval from a support team lead, and a refund above 5,000 AED also needs
the finance controller.

How refunds are paid. Refunds go back to the original payment method within 5 working days. Cash on delivery
orders are refunded by bank transfer once the customer confirms their IBAN.

Partial refunds. Shipping fees are refunded only when the whole order is returned or the delay was ours.
"""),
    "refunds-2024": ("Customer refund policy, version 2", "2024-02-11", """
Status: superseded on 1 March 2026 by version 3. Kept for audits of refunds made before that date.

Who can approve a refund. A support agent may approve a refund of up to 500 AED per order without a manager's
sign-off. Anything above 500 AED goes to a support team lead.

How refunds are paid. Refunds go back to the original payment method within 7 working days.
"""),
    "refunds-2027-draft": ("DRAFT: Customer refund policy, version 4 (proposal)", "2026-09-30", """
Status: draft for discussion. Not approved and not in effect. Do not apply it to customer cases.

Proposed change. Raise the limit a support agent may approve without a manager's sign-off from the current amount
to 1,000 AED per order, and pay card refunds within 3 working days.

Open questions. Fraud review for repeat refund requests; whether marketplace orders are covered.
"""),
    "refunds-wholesale": ("Wholesale account credit notes", "2026-01-20", """
Applies to: the wholesale (B2B) desk only, not to retail customer support.

Account managers on the wholesale desk may issue a credit note of up to 2,000 AED per invoice without a manager's
sign-off. Credit notes above that need the head of wholesale. Credit notes are settled against the next invoice,
never paid out in cash.
"""),
    "refund-dashboard": ("How to use the refund dashboard", "2025-11-04", """
The refund dashboard lists open refund requests by age. Select a request to see the order, the payment method and
the customer's history. The Approve button is shown only when the amount is within your own approval limit; above
it, use Escalate and pick the team lead on shift. Every approval is logged with your name.
"""),
    "marketplace-refunds": ("Marketplace seller refunds", "2025-08-19", """
Orders sold by marketplace sellers are refunded by the seller, not by Dhow Freight support. Agents may open a
dispute for the customer if the seller does not answer within 48 hours. Support agents never approve a refund on a
marketplace order themselves, whatever the amount.
"""),
    "svc-payments-gateway": ("Service catalog: payments-gateway", "2025-11-18", """
Owner: Payments Platform. Purpose: takes card and wallet payments from the shop checkout and forwards them to the
acquiring bank.

Production: listens on TCP 8443 behind the internal load balancer (recorded at the November 2025 catalog review).
Staging: TCP 18443. Health check: GET /healthz.

Runbook: restart one pod at a time; never restart during the settlement window (23:00 to 00:30).
"""),
    "cn-2026-014": ("Change notice CN-2026-014: payments-gateway production port", "2026-03-02", """
Status: completed on 2 March 2026.

Change. From 2 March 2026 the payments-gateway service in production listens on TCP 9443 instead of 8443. The old
port is released so that payments-ledger can take it (see CN-2026-019). Load balancer rules and firewall entries
were updated in the same window. Staging is unchanged.

Owner: Payments Platform.
"""),
    "cn-2026-019": ("Change notice CN-2026-019: payments-ledger production port", "2026-03-09", """
Status: completed on 9 March 2026.

Change. The payments-ledger service in production moves from TCP 7443 to TCP 8443, the port released by
payments-gateway in CN-2026-014. Owner: Finance Systems.
"""),
    "cn-2026-022": ("Change notice CN-2026-022: payments-gateway port consolidation", "2026-04-20", """
Status: cancelled on 20 April 2026. No change was made.

Proposal. Move the payments-gateway service in production to TCP 10443 to group payment services in one port
range. Cancelled after review: the gain did not justify another change window so soon after CN-2026-014.
"""),
    "svc-payments-ledger": ("Service catalog: payments-ledger", "2025-11-18", """
Owner: Finance Systems. Purpose: double-entry ledger for payments, refunds and payouts.

Production: listens on TCP 7443 (recorded at the November 2025 catalog review). Staging: TCP 17443. The nightly
reconciliation job reads from payments-gateway settlement files.
"""),
    "inc-2291": ("Post-incident review INC-2291: label printing stopped at Al Quoz", "2026-05-14", """
Incident date: 12 May 2026, 06:10 to 09:05. Impact: no shipping labels printed at the Al Quoz warehouse for about
three hours; 412 parcels missed the morning pickup.

Root cause. The label-printer service's spool directory filled the disk: old spool files were never rotated, and
once the disk was full new print jobs failed silently.

Component: label-printer. Fix: add spool rotation and a disk usage alert. Action owner: the team that owns
label-printer.
"""),
    "inc-2219": ("Post-incident review INC-2219: customer portal logins failing", "2026-02-03", """
Incident date: 1 February 2026. Impact: customers could not log in to the customer portal for 50 minutes.

Root cause. The TLS certificate of customer-portal expired; automatic renewal had been switched off during a
migration. Component: customer-portal. Action owner: Web Platform.
"""),
    "svc-label-printer": ("Service catalog: label-printer", "2025-11-18", """
Owner: Fulfilment Apps (recorded at the November 2025 catalog review). Purpose: renders shipping labels and sends
them to the warehouse printers.

Runs on one host per warehouse. Spool directory: /var/spool/labels. Depends on route-planner for the carrier
choice.
"""),
    "org-changes-2026": ("Service ownership changes in 2026", "2026-01-15", """
These changes override the owners recorded at the November 2025 catalog review.

On 15 January 2026 the label-printer service moved from Fulfilment Apps to Warehouse Systems. On the same date
notification-hub moved from Web Platform to Customer Messaging. Fulfilment Apps keeps route-planner.
"""),
}

# ------------------------------------------------------------------ filler

SERVICES = {
    "route-planner": ("Fulfilment Apps", "plans delivery routes and picks the carrier for each parcel"),
    "inventory-sync": ("Warehouse Systems", "keeps stock levels in the shop in step with the warehouses"),
    "notification-hub": ("Web Platform", "sends order emails, SMS and app notifications"),
    "customer-portal": ("Web Platform", "lets customers track orders and manage returns"),
    "pricing-engine": ("Commerce", "computes prices, discounts and VAT for the shop"),
    "customs-docs": ("Trade Compliance", "prepares customs declarations for cross-border parcels"),
}
NEW_OWNERS = {"notification-hub": "Customer Messaging"}             # org-changes-2026
INCIDENTS = [
    ("2290", "route-planner", "delivery routes not generated for the evening wave",
     "a carrier API changed its date format and every request was rejected"),
    ("2292", "inventory-sync", "duplicate stock updates in the shop",
     "a retry loop replayed events that had already been applied"),
    ("2209", "notification-hub", "order confirmation emails delayed by two hours",
     "the email provider throttled the account after a bulk campaign"),
    ("2281", "pricing-engine", "wrong VAT shown on gift cards",
     "gift cards were tagged as goods instead of vouchers"),
    ("2229", "customs-docs", "customs declarations rejected for Oman",
     "a new commodity code list was not loaded"),
    ("2193", "customer-portal", "return labels not downloadable",
     "a storage bucket permission was removed during cleanup"),
]
POLICIES = [
    ("Travel and expenses policy", "Staff book flights through the travel desk. Economy class for flights under six "
     "hours. Meals are reimbursed up to 150 AED a day with receipts. Expense claims are approved by the line manager "
     "and paid with the next salary."),
    ("Annual leave policy", "Full-time staff get 30 calendar days of annual leave a year. Leave requests go to the "
     "line manager at least two weeks ahead. Up to 10 unused days may be carried into the next year."),
    ("Password and access policy", "Passwords have at least 14 characters. Multi-factor authentication is required "
     "for every internal system. Access to production is approved by the service owner and reviewed every quarter."),
    ("Data retention policy", "Order records are kept for 7 years for tax purposes. Support chat transcripts are "
     "kept for 2 years. Access logs are kept for 90 days, then deleted."),
    ("On-call policy", "Each service team runs a weekly on-call rota. The on-call engineer answers a page within 15 "
     "minutes. An incident with customer impact gets a post-incident review within 3 working days."),
    ("Supplier payment terms", "Suppliers are paid 45 days after invoice. Early payment discounts are taken when "
     "the discount is at least 1.5 percent. The finance controller approves payments above 50,000 AED."),
]
TOPICS = [
    ("Port of Jebel Ali: sailing schedule and cut-off times", "Containers for the weekly sailing to Mumbai must be at "
     "the port gate by Tuesday 18:00. The production planning team books slots two weeks ahead. Late containers "
     "roll to the next sailing."),
    ("Warehouse locations", "Dhow Freight runs three warehouses: Al Quoz (parcels), Jebel Ali Free Zone (bulk and "
     "export) and Sharjah (returns). Opening hours are 06:00 to 22:00, Saturday to Thursday."),
    ("Public holiday calendar 2026", "Warehouses close on Eid al-Fitr and Eid al-Adha. Customer support runs a "
     "reduced shift on public holidays. Couriers do not collect on National Day."),
    ("Shipping zones and delivery times", "Zone A (Dubai, Sharjah, Ajman): next day. Zone B (other emirates): two "
     "days. GCC: three to five days. Parcels over 30 kg go by freight, not courier."),
    ("Glossary", "AWB: air waybill. COD: cash on delivery. SLA: service level agreement. RMA: return merchandise "
     "authorisation. WMS: warehouse management system."),
    ("New starter checklist", "Collect your laptop from IT on day one. Complete the security training in the first "
     "week. Your manager adds you to the team's on-call rota after your first month."),
    ("Returns handling at Sharjah", "Returned parcels are inspected within 2 working days. Items in resaleable "
     "condition go back to stock; damaged items are written off and reported weekly."),
    ("Carrier contacts", "Each carrier has an account manager and an operations desk. Pickup problems go to the "
     "operations desk first. Contract questions go to procurement."),
    ("Packaging standards", "Fragile items get double-wall boxes and corner protection. Liquids are bagged and "
     "sealed. Labels go on the largest flat side, never across a seam."),
    ("Meeting notes: logistics weekly, 6 October 2026", "Pickup misses at Al Quoz are down to two a week. The new "
     "conveyor in Jebel Ali goes live in November. Action: review courier cut-off times before the sale season."),
    ("Meeting notes: support weekly, 13 October 2026", "Average first reply time is 3 hours. Refund requests rose "
     "after the price change; agents asked for clearer escalation steps. Action: update the macros."),
    ("Sale season readiness", "Extra pickers are hired for November and December. Peak volume is planned at three "
     "times a normal day. Freeze on production changes from 20 November to 2 January."),
    ("Fleet maintenance schedule", "Vans are serviced every 15,000 km. Tyres are checked weekly. A van with a "
     "warning light is taken off the road until a mechanic clears it."),
    ("Customer support hours", "Chat and phone run from 08:00 to 22:00 every day. Email is answered within one "
     "working day. Urgent delivery problems are flagged to the logistics desk."),
    ("Cash on delivery handling", "Couriers hand in COD cash at the depot the same day. The depot counts it twice "
     "and banks it the next morning. Differences above 50 AED are reported to finance."),
    ("Address quality rules", "Every address needs a phone number and a landmark. Makani numbers are preferred in "
     "Dubai. Parcels with incomplete addresses are held and the customer is called."),
    ("Dangerous goods", "Lithium batteries ship only inside devices. Aerosols and perfumes over 100 ml go by road, "
     "never by air. The trade compliance team signs off new dangerous goods products."),
    ("Supplier onboarding", "New suppliers send a trade licence, VAT certificate and bank letter. Procurement checks "
     "them and creates the supplier in the ERP. The first order is capped at 20,000 AED."),
    ("Stock count procedure", "Cycle counts run every night for fast movers and monthly for the rest. A difference "
     "above 2 percent triggers a recount by a second person."),
    ("Office access and visitors", "Visitors sign in at reception and wear a badge. Warehouse floors require "
     "safety shoes and a high-visibility vest. Photography is not allowed on the warehouse floor."),
    ("VAT on shipments", "Domestic shipments carry 5 percent VAT. Exports to non-GCC countries are zero-rated with "
     "proof of export. Gift cards are vouchers, not goods."),
    ("Courier performance review, Q3 2026", "On-time delivery was 94 percent in zone A and 89 percent in zone B. "
     "Damaged parcel claims fell after the packaging change. One courier was put on a performance plan."),
    ("Architecture overview", "The shop front end calls pricing-engine, payments-gateway and inventory-sync. "
     "Warehouse services (label-printer, route-planner) run on hosts in each warehouse. All services log to the "
     "central log store."),
    ("Deployment process", "Changes are deployed on weekdays between 10:00 and 16:00. Every production change needs "
     "a change notice with an owner, a rollback plan and a completed status after the change."),
    ("Service catalog review process", "Owners confirm their entries every November. Changes during the year are "
     "published as change notices or ownership notices and override the catalog until the next review."),
    ("Marketplace seller onboarding", "Sellers sign the marketplace agreement and upload their catalogue. The "
     "commerce team approves the first ten listings by hand. Sellers handle their own returns."),
    ("Insurance for high-value parcels", "Parcels declared above 5,000 AED are insured automatically. Claims need "
     "photos of the packaging within 48 hours of delivery."),
]


def service_doc(name, owner, purpose, port):
    return (f"Service catalog: {name}", "2025-11-18",
            f"\nOwner: {owner}. Purpose: {purpose}.\n\nProduction: listens on TCP {port} (recorded at the November "
            f"2025 catalog review). Health check: GET /healthz.\n\nRunbook: page the owning team's on-call engineer; "
            f"restart one instance at a time.\n")


def incident_doc(num, component, impact, cause, owner):
    return (f"Post-incident review INC-{num}: {impact}", "2026-06-01",
            f"\nImpact: {impact}.\n\nRoot cause. {cause[0].upper() + cause[1:]}.\n\nComponent: {component}. Action "
            f"owner: {owner}.\n")


def all_docs() -> dict[str, tuple[str, str, str]]:
    docs = dict(KEY_DOCS)
    rng = random.Random(2026)
    for i, (name, (owner, purpose)) in enumerate(SERVICES.items()):
        docs[f"svc-{name}"] = service_doc(name, owner, purpose, 6100 + 100 * i)
    for num, comp, impact, cause in INCIDENTS:
        owner = NEW_OWNERS.get(comp, SERVICES[comp][0])          # incidents are from 2026: after the changes
        docs[f"inc-{num}"] = incident_doc(num, comp, impact, cause, owner)
    for title, body in POLICIES + TOPICS:
        key = "doc-" + "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40]
        day = f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}"
        docs[key] = (title, day, "\n" + body + "\n")
    return docs


DOCS = all_docs()
assert len(DOCS) == 60, len(DOCS)
_order = sorted(DOCS)
random.Random(17).shuffle(_order)
ID = {key: f"kb-{n:03d}" for n, key in enumerate(_order, 1)}           # document key -> kb id
TEXT = {ID[k]: f"# {t}\n\nDocument: {ID[k]}\nUpdated: {day}\n{body}" for k, (t, day, body) in DOCS.items()}


def path(key: str) -> str:
    """The workspace path of a document, e.g. corpus/kb-017.md."""
    return f"corpus/{ID[key]}.md"


if __name__ == "__main__":
    root = Path(sys.argv[1]) / "seeds/kb/files/corpus"
    root.mkdir(parents=True, exist_ok=True)
    for kid, text in TEXT.items():
        (root / f"{kid}.md").write_text(text)
