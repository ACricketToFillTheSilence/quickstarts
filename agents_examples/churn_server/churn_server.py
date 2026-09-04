import base64
import os
from email.message import EmailMessage

from dotenv import load_dotenv
from fastmcp import FastMCP
from airbyte_agent_sdk import AirbyteAuthConfig
from airbyte_agent_sdk.connectors.stripe import StripeConnector
from airbyte_agent_sdk.connectors.salesforce import SalesforceConnector
from airbyte_agent_sdk.connectors.gmail import GmailConnector

load_dotenv()

mcp = FastMCP("churn-detection")

# Orgs spell the lost stage differently depending on how their sales process
# was configured, so match the common variants.
CLOSED_LOST_STAGES = {"Closed Lost", "Closed/Lost", "Closed - Lost"}

def _auth_for_tenant(tenant_id: str) -> AirbyteAuthConfig:
    return AirbyteAuthConfig(
        airbyte_client_id=os.getenv("AIRBYTE_CLIENT_ID"),
        airbyte_client_secret=os.getenv("AIRBYTE_CLIENT_SECRET"),
        workspace_name=tenant_id,
    )

async def _fetch_billing(auth: AirbyteAuthConfig, email: str) -> dict:
    async with StripeConnector(auth_config=auth) as stripe:
        customers = await stripe.execute("customers", "list", {"email": email})
        if not customers.data:
            return {"found": False}

        cid = customers.data[0]["id"]
        # Stripe omits canceled subscriptions by default, which would hide the
        # strongest churn signals, so ask for every status.
        subs = await stripe.execute(
            "subscriptions", "list", {"customer": cid, "limit": 5, "status": "all"}
        )
        invoices = await stripe.execute(
            "invoices", "list", {"customer": cid, "limit": 10}
        )

    return {
        "found": True,
        "customer_id": cid,
        "subscriptions": subs.data,
        "recent_invoices": invoices.data,
    }

async def _fetch_crm(auth: AirbyteAuthConfig, email: str) -> dict:
    async with SalesforceConnector(auth_config=auth) as salesforce:
        contacts = await salesforce.execute("contacts", "api_search", {
            "q": (
                "SELECT Id, Email, FirstName, LastName, AccountId "
                f"FROM Contact WHERE Email = '{email}'"
            ),
        })

        if not contacts.data:
            return {"contact": None, "opportunities": []}

        contact = contacts.data[0]
        account_id = contact.get("AccountId")

        opportunities = []
        if account_id:
            opps = await salesforce.execute("opportunities", "api_search", {
                "q": (
                    "SELECT Id, Name, StageName, Amount, CloseDate "
                    f"FROM Opportunity WHERE AccountId = '{account_id}' "
                    "LIMIT 10"
                ),
            })
            opportunities = opps.data

    return {"contact": contact, "opportunities": opportunities}

async def _create_gmail_draft(
    auth: AirbyteAuthConfig,
    recipient_email: str,
    subject: str,
    body: str,
) -> dict:
    # Gmail takes the whole email as one base64url-encoded RFC 2822 blob,
    # not as separate to/subject/body fields.
    message = EmailMessage()
    message["To"] = recipient_email
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    async with GmailConnector(auth_config=auth) as gmail:
        result = await gmail.execute("drafts", "create", {"message": {"raw": raw}})
    return result

@mcp.tool()
async def get_customer_health(tenant_id: str, customer_email: str) -> dict:
    """Pull billing and CRM signals for a customer.

    Returns Stripe subscription status, recent invoices, and the
    Salesforce contact profile with associated opportunities.
    """
    auth = _auth_for_tenant(tenant_id)
    billing = await _fetch_billing(auth, customer_email)
    crm = await _fetch_crm(auth, customer_email)
    return {"billing": billing, "crm": crm}

async def _score_churn_risk(auth: AirbyteAuthConfig, email: str) -> dict:
    """Fetch signals and score them. Plain function, not an MCP tool, so
    every tool that reports a risk score derives it from live data instead
    of taking the agent's word for it.
    """
    billing = await _fetch_billing(auth, email)

    if not billing["found"]:
        return {
            "customer_email": email,
            "risk_score": 0,
            "risk_level": "unknown",
            "signals": ["Customer not found in Stripe"],
            "billing": billing,
            "crm": {"contact": None, "opportunities": []},
        }

    crm = await _fetch_crm(auth, email)

    score = 0
    signals = []

    for inv in billing["recent_invoices"]:
        if inv.get("status") in ("uncollectible", "void"):
            score += 15
            signals.append(f"Invoice {inv['id']} is {inv['status']}")

    for sub in billing["subscriptions"]:
        if sub.get("cancel_at_period_end"):
            score += 40
            signals.append("Subscription scheduled to cancel")
        elif sub.get("status") == "past_due":
            score += 25
            signals.append("Subscription past due")
        elif sub.get("status") == "canceled":
            score += 35
            signals.append("Subscription already canceled")

    if not crm["contact"]:
        score += 10
        signals.append("No matching contact in CRM")

    for opp in crm["opportunities"]:
        if opp.get("StageName") in CLOSED_LOST_STAGES:
            score += 20
            signals.append(f"Opportunity \"{opp.get('Name')}\" marked lost")

    score = min(score, 100)
    level = "low" if score < 30 else ("medium" if score < 60 else "high")

    return {
        "customer_email": email,
        "risk_score": score,
        "risk_level": level,
        "signals": signals,
        "billing": billing,
        "crm": crm,
    }

@mcp.tool()
async def assess_churn_risk(tenant_id: str, customer_email: str) -> dict:
    """Score churn risk by combining billing and CRM signals.

    Returns a 0-100 risk score, a level (low/medium/high), and the
    signals that contributed to the score.
    """
    auth = _auth_for_tenant(tenant_id)
    return await _score_churn_risk(auth, customer_email)

# The agent proposes a discount; this ceiling is what actually decides one.
# Keep the limit in code, not in the prompt.
MAX_DISCOUNT_PERCENT = 30

@mcp.tool()
async def generate_promo_suggestion(
    tenant_id: str,
    customer_email: str,
    recipient_email: str,
    discount_percent: int,
) -> dict:
    """Email a retention recommendation for an at-risk customer.

    Re-scores the customer, then creates a Gmail draft with the risk
    details and a suggested discount for the recipient to review.
    """
    auth = _auth_for_tenant(tenant_id)

    # Score it here rather than accepting a score as a parameter, so the
    # draft can never quote a number the agent invented.
    assessment = await _score_churn_risk(auth, customer_email)
    risk_score = assessment["risk_score"]
    risk_level = assessment["risk_level"]

    discount = max(0, min(discount_percent, MAX_DISCOUNT_PERCENT))
    capped = discount != discount_percent

    subject = (
        f"Retention recommendation: {customer_email} "
        f"({risk_level} risk)"
    )
    body_lines = [
        f"Customer: {customer_email}",
        f"Churn risk: {risk_level} (score {risk_score}/100)",
        "",
        "Signals:",
        *(f"  - {s}" for s in assessment["signals"]),
        "",
        f"Suggested action: {discount}% off next renewal",
    ]
    if capped:
        body_lines.append(
            f"(Agent proposed {discount_percent}%; capped at "
            f"{MAX_DISCOUNT_PERCENT}% by policy.)"
        )
    body_lines += ["", "Please review and take appropriate action."]
    body = "\n".join(body_lines)

    result = await _create_gmail_draft(auth, recipient_email, subject, body)

    return {
        "status": "draft_created",
        "to": recipient_email,
        "subject": subject,
        "body": body,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "discount_percent": discount,
        "discount_capped": capped,
        "draft_id": result.get("id"),
    }

CONNECTOR_REGISTRY: dict[str, type] = {
    "stripe": StripeConnector,
    "salesforce": SalesforceConnector,
    "gmail": GmailConnector,
}


@mcp.tool()
async def inspect_connector(
    tenant_id: str,
    connector_name: str,
    entity_name: str | None = None,
) -> dict:
    """Discover what data is available on a connected system.

    Without entity_name: lists every entity and its available actions.
    With entity_name: returns the detailed schema for that entity.
    """
    cls = CONNECTOR_REGISTRY.get(connector_name)
    if not cls:
        available = list(CONNECTOR_REGISTRY)
        return {"error": f"Unknown connector: {connector_name}", "available": available}

    auth = _auth_for_tenant(tenant_id)
    async with cls(auth_config=auth) as conn:
        if entity_name:
            schema = conn.entity_schema(entity_name)
            if schema is None:
                return {"error": f"Entity '{entity_name}' not found on {connector_name}"}
            return {"connector": connector_name, "entity": entity_name, "schema": schema}
        return {"connector": connector_name, "entities": conn.list_entities()}


if __name__ == "__main__":
    mcp.run()