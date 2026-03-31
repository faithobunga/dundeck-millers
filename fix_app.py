"""
Run this from your project root:
    python fix_app.py

Fixes two bugs in app.py:
1. Invoice payment status mismatch ('partially_paid' vs 'partial')
2. Supplier creation crash (NOT NULL on grand_total before items are processed)
"""

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

original = content

# ── FIX 1: Payment status mismatch ──────────────────────────────────────────
# app.py sets 'partially_paid' but the template checks for 'partial'
# Fix: change 'partially_paid' to 'partial' in add_invoice_payment route
content = content.replace(
    "invoice.payment_status = 'partially_paid'",
    "invoice.payment_status = 'partial'"
)

# ── FIX 2: Supplier grand_total NOT NULL crash ───────────────────────────────
# The Supplier object is flushed before grand_total is calculated,
# causing a NOT NULL constraint failure. Fix: set grand_total = 0 before flush.
old_block = """        db.session.add(supplier)
        db.session.flush()
        
        # Process items
        product_types = request.form.getlist('product_type[]')"""

new_block = """        supplier.grand_total = 0  # Temporary; will be updated after items are processed
        db.session.add(supplier)
        db.session.flush()
        
        # Process items
        product_types = request.form.getlist('product_type[]')"""

content = content.replace(old_block, new_block)

# ── Verify changes were made ─────────────────────────────────────────────────
if content == original:
    print("⚠️  No changes were made — the patterns may have already been fixed")
    print("   Check app.py manually for 'partially_paid' and the supplier flush block")
else:
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    
    fixes = []
    if "invoice.payment_status = 'partial'" in content:
        fixes.append("✅ Fix 1: Invoice payment_status changed to 'partial'")
    if "supplier.grand_total = 0  # Temporary" in content:
        fixes.append("✅ Fix 2: Supplier grand_total defaults to 0 before flush")
    
    for fix in fixes:
        print(fix)
    
    print("\n🎉 app.py updated successfully!")
    print("   Restart Flask: python app.py")