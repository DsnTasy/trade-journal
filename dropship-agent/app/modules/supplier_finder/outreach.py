"""Draft supplier emails. Drafts only: sending needs your approval in the dashboard.

Template-based (free, predictable). It asks the questions that matter for a UK store.
"""

from __future__ import annotations

from app.core.models import Product, Supplier


def draft_enquiry(product: Product, supplier: Supplier, store_name: str) -> tuple[str, str]:
    questions = [
        "Can you send a sample to the UK, and what would it cost including delivery?",
        "What are your delivery times to UK customers, and which warehouse would ship them?",
        "Which carrier do you use, and do UK orders get tracking numbers?",
        "What is your returns and replacement policy for faulty or damaged items?",
        "Can you send unbranded packaging (no prices or supplier marketing inside)?",
        "Who is the manufacturer, and do you have its name and address for product labelling?",
    ]
    flags = {f.get("flag") for f in (product.compliance_flags or [])}
    if flags & {"electrical", "batteries", "toys", "cosmetics", "food_contact"}:
        questions.append("Does this product have UKCA or CE marking, and can you share the Declaration of "
                         "Conformity and test reports?")
    if supplier.moq and supplier.moq > 1:
        questions.append(f"Your listing shows a minimum order of {supplier.moq}. Is single-unit dropshipping possible?")

    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    subject = f"Enquiry: {product.name}: sample and UK dropshipping"
    body = (
        f"Hello {supplier.name} team,\n\n"
        f"I run {store_name}, an online store for UK customers, and I'm interested in selling "
        f"\"{product.name}\"{f' ({supplier.url})' if supplier.url else ''}.\n\n"
        f"Before ordering a sample, could you help with a few questions?\n\n{numbered}\n\n"
        f"Thank you, I look forward to hearing from you.\n\nKind regards,\n[Your name]\n{store_name}"
    )
    return subject, body
