from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from functools import wraps
import os
from sqlalchemy import text
from xhtml2pdf import pisa
from io import BytesIO
# ============================================================================
# APP CONFIGURATION
# ============================================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dunedeck-secret-key-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///dunedeck.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Add datetime to Jinja2 global context
@app.context_processor
def inject_now():
    return {'now': datetime.now}

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

with app.app_context():
    try:
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE invoice ADD COLUMN updated_by_id INTEGER'))
            conn.execute(text('ALTER TABLE invoice ADD COLUMN updated_at DATETIME'))
            conn.commit()
            print("✅ Added updated_by_id and updated_at columns!")
    except:
        pass  # Columns already exist, that's fine

with app.app_context():
    with db.engine.connect() as conn:
        # Add new columns to supplier table
        try:
            conn.execute(text('ALTER TABLE supplier ADD COLUMN kra_pin VARCHAR(20)'))
            print("✅ Added kra_pin column")
        except:
            print("⚠️ kra_pin already exists")
        
        try:
            conn.execute(text('ALTER TABLE supplier ADD COLUMN reference_code VARCHAR(100)'))
            print("✅ Added reference_code column")
        except:
            print("⚠️ reference_code already exists")
        
        try:
            conn.execute(text('ALTER TABLE supplier ADD COLUMN amount_paid FLOAT DEFAULT 0'))
            print("✅ Added amount_paid column")
        except:
            print("⚠️ amount_paid already exists")
        
        try:
            conn.execute(text("ALTER TABLE supplier ADD COLUMN payment_status VARCHAR(20) DEFAULT 'unpaid'"))
            print("✅ Added payment_status column")
        except:
            print("⚠️ payment_status already exists")
        
        # Update supplier_item table - rename item_description to product_type
        try:
            conn.execute(text('ALTER TABLE supplier_item ADD COLUMN product_type VARCHAR(100)'))
            print("✅ Added product_type column")
        except:
            print("⚠️ product_type already exists")
        
        try:
            conn.execute(text('ALTER TABLE supplier_item ADD COLUMN total_bags FLOAT'))
            print("✅ Added total_bags column")
        except:
            print("⚠️ total_bags already exists")
        
        try:
            conn.execute(text('ALTER TABLE supplier_item ADD COLUMN total_weight FLOAT'))
            print("✅ Added total_weight column")
        except:
            print("⚠️ total_weight already exists")
        
        try:
            conn.execute(text('ALTER TABLE supplier_item ADD COLUMN price_basis VARCHAR(20) DEFAULT "per_bag"'))
            print("✅ Added price_basis column")
        except:
            print("⚠️ price_basis already exists")
        
        # Create supplier_payment table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS supplier_payment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier_id INTEGER NOT NULL,
                payment_date DATE NOT NULL,
                amount FLOAT NOT NULL,
                payment_method VARCHAR(50) NOT NULL,
                reference_number VARCHAR(100),
                notes VARCHAR(300),
                recorded_by_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (supplier_id) REFERENCES supplier(id),
                FOREIGN KEY (recorded_by_id) REFERENCES user(id)
            )
        '''))
        print("✅ Created supplier_payment table")
        
        conn.commit()
        print("\n🎉 ALL MIGRATIONS COMPLETED SUCCESSFULLY!")


# ============================================================================
# DATABASE MODELS
# ============================================================================

class User(UserMixin, db.Model):
    """User model for authentication and role-based access"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # worker, manager, director, admin
    is_blocked = db.Column(db.Boolean, default=False)
    must_change_password = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Relationships
    sheets = db.relationship('IntakeSheet', foreign_keys='IntakeSheet.worker_id', backref='worker', lazy=True)
    authorized_sheets = db.relationship('IntakeSheet', foreign_keys='IntakeSheet.authorized_by_id', backref='authorized_by', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class IntakeSheet(db.Model):
    """Main sheet for daily weight records and inventory transactions"""
    id = db.Column(db.Integer, primary_key=True)
    sheet_date = db.Column(db.Date, nullable=False, default=date.today)
    product_type = db.Column(db.String(50), nullable=False)
    sheet_type = db.Column(db.String(20), nullable=False, default='daily')
    status = db.Column(db.String(20), default='In Progress')
    worker_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime)
    
    # Authorization fields
    authorization_status = db.Column(db.String(20), default='pending')
    authorized_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    destination = db.Column(db.String(200))
    purpose = db.Column(db.String(200))
    
    # Manager created flag
    is_manager_created = db.Column(db.Boolean, default=False)
    
    # Relationships
    entries = db.relationship('IntakeEntry', backref='sheet', lazy=True, cascade='all, delete-orphan')


class IntakeEntry(db.Model):
    """Individual cage entries within a sheet"""
    id = db.Column(db.Integer, primary_key=True)
    sheet_id = db.Column(db.Integer, db.ForeignKey('intake_sheet.id'), nullable=False)
    cage_number = db.Column(db.String(50), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class UnprocessedInventory(db.Model):
    """Raw stock inventory"""
    id = db.Column(db.Integer, primary_key=True)
    product_type = db.Column(db.String(50), unique=True, nullable=False)
    total_received_bags = db.Column(db.Float, default=0)
    total_outstock_bags = db.Column(db.Float, default=0)
    total_lost_bags = db.Column(db.Float, default=0)
    total_sent_to_processing = db.Column(db.Float, default=0)
    remaining_bags = db.Column(db.Float, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    
    def calculate_remaining(self):
        self.remaining_bags = (
            self.total_received_bags - 
            self.total_outstock_bags - 
            self.total_lost_bags - 
            self.total_sent_to_processing
        )
        return self.remaining_bags
    
    def is_low_stock(self):
        return self.remaining_bags < 50
    
    def is_critical_stock(self):
        return self.remaining_bags < 20


class ProcessedInventory(db.Model):
    """Packaged inventory"""
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100), unique=True, nullable=False)
    product_category = db.Column(db.String(50), nullable=False)
    total_quantity = db.Column(db.Float, default=0)
    total_weight_kg = db.Column(db.Float, default=0)
    total_dispatched = db.Column(db.Float, default=0)
    total_samples = db.Column(db.Float, default=0)
    remaining = db.Column(db.Float, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)


class Supplier(db.Model):
    """Supplier purchase records"""
    id = db.Column(db.Integer, primary_key=True)
    
    # Supplier details
    supplier_name = db.Column(db.String(200), nullable=False)
    kra_pin = db.Column(db.String(20))
    address = db.Column(db.String(300))
    city = db.Column(db.String(100))
    phone_number = db.Column(db.String(20))
    
    # Transaction details
    transaction_date = db.Column(db.Date, nullable=False, default=date.today)
    invoice_number = db.Column(db.String(100))
    lpo_number = db.Column(db.String(100))
    reference_code = db.Column(db.String(100))
    
    # Payment tracking
    grand_total = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, default=0)
    payment_status = db.Column(db.String(20), default='unpaid')
    
    # Legacy payment fields
    payment_date = db.Column(db.Date)
    payment_method = db.Column(db.String(50))
    bank_name = db.Column(db.String(100))
    account_number = db.Column(db.String(50))
    transaction_code = db.Column(db.String(100))
    
    # Authorization
    authorized_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    authorized_by = db.relationship('User', foreign_keys=[authorized_by_id], backref='authorized_suppliers')
    authorization_date = db.Column(db.Date)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    items = db.relationship('SupplierItem', backref='supplier', lazy=True, cascade='all, delete-orphan')
    linked_sheet_id = db.Column(db.Integer, db.ForeignKey('intake_sheet.id'))
    linked_sheet = db.relationship('IntakeSheet', foreign_keys=[linked_sheet_id], backref='supplier_record')


class SupplierItem(db.Model):
    """Items in supplier purchase"""
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    
    # Product info
    product_type = db.Column(db.String(100), nullable=False)
    item_description = db.Column(db.String(300))  # Optional
    
    # Flexible quantity
    quantity = db.Column(db.Float, nullable=False)
    total_bags = db.Column(db.Float, default=0)
    total_weight = db.Column(db.Float, default=0)
    
    # Pricing
    unit_price = db.Column(db.Float, nullable=False)
    price_basis = db.Column(db.String(20), default='per_bag')
    total_amount = db.Column(db.Float, nullable=False)


class SupplierPayment(db.Model):
    """Payment records for suppliers"""
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50), nullable=False)
    reference_number = db.Column(db.String(100))
    notes = db.Column(db.String(300))
    
    recorded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recorded_by = db.relationship('User', backref='supplier_payments_recorded')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    supplier = db.relationship('Supplier', backref='payments')


class PackagingRecord(db.Model):
    """Record of packaging operations"""
    id = db.Column(db.Integer, primary_key=True)
    date_packaged = db.Column(db.Date, nullable=False, default=date.today)
    batch_number = db.Column(db.String(100), nullable=False)
    packaged_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    packaged_by = db.relationship('User', foreign_keys=[packaged_by_id], backref='packaging_records')
    
    product_type = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    total_weight_kg = db.Column(db.Float, nullable=False)
    
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_packaging_records')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class InternalRequisition(db.Model):
    """Internal requisition form"""
    id = db.Column(db.Integer, primary_key=True)
    requisition_number = db.Column(db.String(50), unique=True, nullable=False)
    requested_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    requested_by = db.relationship('User', foreign_keys=[requested_by_id], backref='requisitions')
    date_requested = db.Column(db.Date, nullable=False, default=date.today)
    department = db.Column(db.String(100))
    purpose = db.Column(db.String(300))
    
    status = db.Column(db.String(20), default='pending')
    
    # Approval
    approved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    approved_by = db.relationship('User', foreign_keys=[approved_by_id], backref='approved_requisitions')
    approval_date = db.Column(db.Date)
    rejection_reason = db.Column(db.String(300))
    
    # Fulfillment
    received_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    received_by = db.relationship('User', foreign_keys=[received_by_id], backref='received_requisitions')
    date_received = db.Column(db.Date)
    
    total_items = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    items = db.relationship('RequisitionItem', backref='requisition', lazy=True, cascade='all, delete-orphan')
    linked_dispatch_id = db.Column(db.Integer, db.ForeignKey('dispatch.id'))


class RequisitionItem(db.Model):
    """Items in an internal requisition"""
    id = db.Column(db.Integer, primary_key=True)
    requisition_id = db.Column(db.Integer, db.ForeignKey('internal_requisition.id'), nullable=False)
    item_description = db.Column(db.String(300), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(300))


class Dispatch(db.Model):
    """Dispatch for delivery"""
    id = db.Column(db.Integer, primary_key=True)
    tracking_number = db.Column(db.String(100), unique=True, nullable=False)
    batch_number = db.Column(db.String(100))
    dispatch_date = db.Column(db.Date, nullable=False, default=date.today)
    dispatch_type = db.Column(db.String(20), default='sale')
    
    # Driver details
    driver_name = db.Column(db.String(200))
    driver_phone = db.Column(db.String(20))
    vehicle_registration = db.Column(db.String(50))
    
    # Authorization
    status = db.Column(db.String(20), default='pending')
    authorized_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    authorized_by = db.relationship('User', foreign_keys=[authorized_by_id], backref='authorized_dispatches')
    authorization_date = db.Column(db.Date)
    reason = db.Column(db.String(300))
    
    total_units = db.Column(db.Float, default=0)
    total_weight_kg = db.Column(db.Float, default=0)
    
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='created_dispatches')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    items = db.relationship('DispatchItem', backref='dispatch', lazy=True, cascade='all, delete-orphan')
    requisition_id = db.Column(db.Integer, db.ForeignKey('internal_requisition.id'))
    requisition = db.relationship('InternalRequisition', foreign_keys=[requisition_id], backref='dispatch')


class DispatchItem(db.Model):
    """Items in a dispatch"""
    id = db.Column(db.Integer, primary_key=True)
    dispatch_id = db.Column(db.Integer, db.ForeignKey('dispatch.id'), nullable=False)
    item_description = db.Column(db.String(300), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit_weight_kg = db.Column(db.Float, nullable=False)
    total_units = db.Column(db.Float, nullable=False)
    total_weight_kg = db.Column(db.Float, nullable=False)

class Invoice(db.Model):
    """Sales invoice"""
    __tablename__ = 'invoice'
    
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(100), unique=True, nullable=False)
    invoice_date = db.Column(db.Date, nullable=False, default=date.today)
    is_auto_generated = db.Column(db.Boolean, default=False)


    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)
    



    # Customer information
    customer_name = db.Column(db.String(200), nullable=False)
    phone_number = db.Column(db.String(20))
    email = db.Column(db.String(100))
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    zip_code = db.Column(db.String(20))
    kra_pin = db.Column(db.String(50))
    reference_code = db.Column(db.String(100))
    
    # Financial details
    subtotal = db.Column(db.Float, default=0)
    tax = db.Column(db.Float, default=0)
    grand_total = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, default=0)
    payment_status = db.Column(db.String(20), default='unpaid')
    
    # Relationships
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    dispatch_id = db.Column(db.Integer, db.ForeignKey('dispatch.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref='invoices_created')
    dispatch = db.relationship('Dispatch', backref='invoices')
    items = db.relationship('InvoiceItem', backref='invoice', lazy=True, cascade='all, delete-orphan')
    payments = db.relationship('Payment', backref='invoice', lazy=True, cascade='all, delete-orphan')
    updated_by = db.relationship('User', foreign_keys=[updated_by_id], backref='updated_invoices')


class InvoiceItem(db.Model):
    """Items in an invoice"""
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    description = db.Column(db.String(300), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_amount = db.Column(db.Float, nullable=False)


class Payment(db.Model):
    """Payment records for invoices"""
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50))
    reference_number = db.Column(db.String(100))
    notes = db.Column(db.String(300))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    recorded_by = db.relationship('User', foreign_keys=[recorded_by_id], backref='recorded_payments')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PasswordResetRequest(db.Model):
    """Password reset requests"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', foreign_keys=[user_id], backref='reset_requests')
    status = db.Column(db.String(20), default='pending')
    requested_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    resolved_by = db.relationship('User', foreign_keys=[resolved_by_id], backref='resolved_requests')
    resolved_at = db.Column(db.DateTime)


class AuditLog(db.Model):
    """Audit trail for all changes"""
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', foreign_keys=[user_id], backref='audit_logs')
    action = db.Column(db.String(50), nullable=False)
    table_name = db.Column(db.String(50))
    record_id = db.Column(db.Integer)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    ip_address = db.Column(db.String(50))

# Worker Clock-In/Out Model
# Worker Clock-In/Out Model
class WorkerClock(db.Model):
    """Track worker attendance with IP-locked clocking"""
    __tablename__ = 'worker_clocks'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    clock_in_time = db.Column(db.DateTime, nullable=False)
    clock_out_time = db.Column(db.DateTime, nullable=True)
    clock_in_ip = db.Column(db.String(50), nullable=False)
    clock_out_ip = db.Column(db.String(50), nullable=True)
    date = db.Column(db.Date, nullable=False)
    hours_worked = db.Column(db.Float, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='clock_records')
    
    def calculate_hours(self):
        """Calculate hours worked"""
        if self.clock_out_time and self.clock_in_time:
            delta = self.clock_out_time - self.clock_in_time
            self.hours_worked = round(delta.total_seconds() / 3600, 2)
        return self.hours_worked


# System Notification Model
class SystemNotification(db.Model):
    """Track system notifications and alerts"""
    __tablename__ = 'system_notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    notification_type = db.Column(db.String(50), nullable=False)
    priority = db.Column(db.String(20), default='normal')
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Relationships
    created_by = db.relationship('User', backref='notifications_created')



# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def update_unprocessed_inventory(product_type):
    """Update unprocessed inventory for a product"""
    inventory = UnprocessedInventory.query.filter_by(product_type=product_type).first()
    if not inventory:
        inventory = UnprocessedInventory(product_type=product_type)
        db.session.add(inventory)
    
    # Calculate received
    received_sheets = IntakeSheet.query.filter_by(
        product_type=product_type,
        sheet_type='received',
        status='Closed'
    ).all()
    
    total_received_weight = sum(
        sum(entry.weight for entry in sheet.entries)
        for sheet in received_sheets
    )
    
    # Get bag weight
    if product_type in ['Maize Grains', 'Maize Germ']:
        bag_weight = 90
    else:
        bag_weight = 50
    
    inventory.total_received_bags = total_received_weight / bag_weight if bag_weight > 0 else 0
    
    # Calculate outstock
    outstock_sheets = IntakeSheet.query.filter_by(
        product_type=product_type,
        sheet_type='outstock',
        status='Closed',
        authorization_status='authorized'
    ).all()
    
    total_outstock_weight = sum(
        sum(entry.weight for entry in sheet.entries)
        for sheet in outstock_sheets
    )
    
    inventory.total_outstock_bags = total_outstock_weight / bag_weight if bag_weight > 0 else 0
    
    # Calculate lost
    lost_sheets = IntakeSheet.query.filter_by(
        product_type=product_type,
        sheet_type='lost',
        status='Closed'
    ).all()
    
    total_lost_weight = sum(
        sum(entry.weight for entry in sheet.entries)
        for sheet in lost_sheets
    )
    
    inventory.total_lost_bags = total_lost_weight / bag_weight if bag_weight > 0 else 0
    
    # Calculate sent to processing
    packaging_records = PackagingRecord.query.all()
    total_sent = 0
    
    for record in packaging_records:
        if product_type in record.product_type:
            total_sent += record.total_weight_kg
    
    inventory.total_sent_to_processing = total_sent / bag_weight if bag_weight > 0 else 0
    
    # Calculate remaining
    inventory.calculate_remaining()
    inventory.last_updated = datetime.utcnow()
    
    db.session.commit()


def update_processed_inventory():
    """Update processed inventory"""
    # Maize Grains 1KG
    inv_1kg = ProcessedInventory.query.filter_by(product_name='Maize Grains 1KG').first()
    if not inv_1kg:
        inv_1kg = ProcessedInventory(
            product_name='Maize Grains 1KG',
            product_category='maize_grains_1kg'
        )
        db.session.add(inv_1kg)
    
    # Calculate from packaging
    packaging_1kg = PackagingRecord.query.filter_by(product_type='maize_grains_1kg').all()
    inv_1kg.total_quantity = sum(p.quantity for p in packaging_1kg)
    inv_1kg.total_weight_kg = inv_1kg.total_quantity * 1
    
    # Calculate dispatched
    dispatched_1kg = DispatchItem.query.filter(
        DispatchItem.item_description.contains('Maize Grains 1KG'),
        DispatchItem.dispatch.has(status='approved')
    ).all()
    inv_1kg.total_dispatched = sum(d.quantity for d in dispatched_1kg)
    
    inv_1kg.remaining = inv_1kg.total_quantity - inv_1kg.total_dispatched
    inv_1kg.last_updated = datetime.utcnow()
    
    # Maize Grains 2KG
    inv_2kg = ProcessedInventory.query.filter_by(product_name='Maize Grains 2KG').first()
    if not inv_2kg:
        inv_2kg = ProcessedInventory(
            product_name='Maize Grains 2KG',
            product_category='maize_grains_2kg'
        )
        db.session.add(inv_2kg)
    
    packaging_2kg = PackagingRecord.query.filter_by(product_type='maize_grains_2kg').all()
    inv_2kg.total_quantity = sum(p.quantity for p in packaging_2kg)
    inv_2kg.total_weight_kg = inv_2kg.total_quantity * 2
    
    dispatched_2kg = DispatchItem.query.filter(
        DispatchItem.item_description.contains('Maize Grains 2KG'),
        DispatchItem.dispatch.has(status='approved')
    ).all()
    inv_2kg.total_dispatched = sum(d.quantity for d in dispatched_2kg)
    
    inv_2kg.remaining = inv_2kg.total_quantity - inv_2kg.total_dispatched
    inv_2kg.last_updated = datetime.utcnow()
    
    # Maize Germ (variable weight)
    inv_germ = ProcessedInventory.query.filter_by(product_name='Maize Germ').first()
    if not inv_germ:
        inv_germ = ProcessedInventory(
            product_name='Maize Germ',
            product_category='maize_germ'
        )
        db.session.add(inv_germ)
    
    packaging_germ = PackagingRecord.query.filter_by(product_type='maize_germ').all()
    inv_germ.total_weight_kg = sum(p.total_weight_kg for p in packaging_germ)
    
    dispatched_germ = DispatchItem.query.filter(
        DispatchItem.item_description.contains('Maize Germ'),
        DispatchItem.dispatch.has(status='approved')
    ).all()
    inv_germ.total_dispatched = sum(d.total_weight_kg for d in dispatched_germ)
    
    inv_germ.remaining = inv_germ.total_weight_kg - inv_germ.total_dispatched
    inv_germ.last_updated = datetime.utcnow()
    
    # Animal Feeds (variable weight)
    inv_feeds = ProcessedInventory.query.filter_by(product_name='Animal Feeds').first()
    if not inv_feeds:
        inv_feeds = ProcessedInventory(
            product_name='Animal Feeds',
            product_category='animal_feeds'
        )
        db.session.add(inv_feeds)
    
    packaging_feeds = PackagingRecord.query.filter_by(product_type='animal_feeds').all()
    inv_feeds.total_weight_kg = sum(p.total_weight_kg for p in packaging_feeds)
    
    dispatched_feeds = DispatchItem.query.filter(
        DispatchItem.item_description.contains('Animal Feeds'),
        DispatchItem.dispatch.has(status='approved')
    ).all()
    inv_feeds.total_dispatched = sum(d.total_weight_kg for d in dispatched_feeds)
    
    inv_feeds.remaining = inv_feeds.total_weight_kg - inv_feeds.total_dispatched
    inv_feeds.last_updated = datetime.utcnow()
    
    db.session.commit()


def get_inventory_alerts():
    """Get low/critical stock alerts"""
    alerts = []
    
    # Unprocessed inventory alerts
    unprocessed = UnprocessedInventory.query.all()
    for inv in unprocessed:
        if inv.is_critical_stock():
            alerts.append({
                'level': 'critical',
                'product': inv.product_type,
                'remaining': inv.remaining_bags,
                'message': f'CRITICAL: Only {inv.remaining_bags:.1f} bags remaining!'
            })
        elif inv.is_low_stock():
            alerts.append({
                'level': 'warning',
                'product': inv.product_type,
                'remaining': inv.remaining_bags,
                'message': f'Low stock: {inv.remaining_bags:.1f} bags remaining'
            })
    
    # Processed inventory alerts (example threshold: 100 units/kg)
    processed = ProcessedInventory.query.all()
    for inv in processed:
        if inv.remaining < 50:
            alerts.append({
                'level': 'critical',
                'product': inv.product_name,
                'remaining': inv.remaining,
                'message': f'CRITICAL: Only {inv.remaining:.1f} units remaining!'
            })
        elif inv.remaining < 100:
            alerts.append({
                'level': 'warning',
                'product': inv.product_name,
                'remaining': inv.remaining,
                'message': f'Low stock: {inv.remaining:.1f} units remaining'
            })
    
    return alerts


def log_audit(action, table_name=None, record_id=None, old_value=None, new_value=None):
    """Log action to audit trail"""
    log = AuditLog(
        user_id=current_user.id,
        action=action,
        table_name=table_name,
        record_id=record_id,
        old_value=str(old_value) if old_value else None,
        new_value=str(new_value) if new_value else None,
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()

# ==================== DIRECTOR DASHBOARD HELPERS ====================

def get_critical_alert():
    """Get the most critical alert for director dashboard"""
    alerts = []
    
    # Check stock levels
    unprocessed = UnprocessedInventory.query.all()
    for item in unprocessed:
        if item.remaining_bags <= 10:
            alerts.append({
                'priority': 1,
                'message': f"CRITICAL: {item.product_type} stock at {item.remaining_bags} bags - immediate restock required"
            })
        elif item.remaining_bags <= 20:
            alerts.append({
                'priority': 2,
                'message': f"LOW STOCK: {item.product_type} at {item.remaining_bags} bags"
            })
    
    # Check unpaid invoices
    unpaid_invoices = Invoice.query.filter_by(payment_status='unpaid').all()
    if unpaid_invoices:
        total_unpaid = sum(inv.grand_total for inv in unpaid_invoices)
        if len(unpaid_invoices) >= 5 or total_unpaid >= 40000:
            alerts.append({
                'priority': 1,
                'message': f"URGENT: {len(unpaid_invoices)} unpaid invoices totaling KES {total_unpaid:,.2f} outstanding"
            })
    
    # Check workers clocked in
    today = date.today()
    clocked_in = WorkerClock.query.filter_by(date=today, clock_out_time=None).count()
    alerts.append({
        'priority': 3,
        'message': f"{clocked_in} workers currently clocked in at warehouse"
    })
    
    # Return highest priority alert
    if alerts:
        alerts.sort(key=lambda x: x['priority'])
        return alerts[0]['message']
    
    return "All systems operating normally"


def get_customer_financials():
    """Get customer financial summary"""
    customers = {}
    
    invoices = Invoice.query.all()
    for invoice in invoices:
        customer = invoice.customer_name
        if customer not in customers:
            customers[customer] = {
                'total_invoiced': 0,
                'total_paid': 0,
                'outstanding': 0
            }
        
        customers[customer]['total_invoiced'] += invoice.grand_total
        
        # Calculate paid amount
        payments = Payment.query.filter_by(invoice_id=invoice.id).all()
        paid = sum(p.amount for p in payments)
        customers[customer]['total_paid'] += paid
        customers[customer]['outstanding'] += (invoice.grand_total - paid)
    
    return customers


def get_supplier_financials():
    suppliers_data = {}
    suppliers = Supplier.query.all()
    for supplier in suppliers:
        total_purchased = supplier.grand_total or 0
        total_paid = supplier.amount_paid or 0  # ← use real data
        suppliers_data[supplier.supplier_name] = {
            'total_purchased': total_purchased,
            'total_paid': total_paid,
            'outstanding': total_purchased - total_paid
        }
    return suppliers_data


def get_worker_attendance():
    """Get current worker attendance status"""
    workers = User.query.filter_by(role='worker').all()
    today = date.today()
    
    attendance_data = []
    for worker in workers:
        # Get today's clock record
        clock_record = WorkerClock.query.filter_by(
            user_id=worker.id,
            date=today
        ).order_by(WorkerClock.clock_in_time.desc()).first()
        
        # Get sheets created this week
        week_start = today - timedelta(days=today.weekday())
        sheets_this_week = IntakeSheet.query.filter(
            IntakeSheet.worker_id == worker.id,
            IntakeSheet.created_at >= datetime.combine(week_start, datetime.min.time())
        ).count()
        
        status = 'clocked_out'
        clock_in_time = None
        hours_today = 0
        
        if clock_record:
            if clock_record.clock_out_time is None:
                status = 'clocked_in'
            clock_in_time = clock_record.clock_in_time
            hours_today = clock_record.hours_worked or 0
            if status == 'clocked_in' and clock_in_time:
                hours_today = (datetime.now() - clock_in_time).total_seconds() / 3600
        
        attendance_data.append({
            'worker': worker,
            'status': status,
            'clock_in_time': clock_in_time,
            'hours_today': round(hours_today, 2),
            'sheets_this_week': sheets_this_week,
            'last_login': worker.last_login
        })
    
    return attendance_data


def get_recent_dispatches(days=30):
    """Get dispatches from last N days"""
    cutoff_date = date.today() - timedelta(days=days)
    dispatches = Dispatch.query.filter(
        Dispatch.dispatch_date >= cutoff_date
    ).order_by(Dispatch.dispatch_date.desc()).all()
    
    dispatch_data = []
    for dispatch in dispatches:
        total_bales = 0
        total_weight = 0
        
        for item in dispatch.items:
            desc_lower = item.item_description.lower()
            if '1kg' in desc_lower:
                total_bales += item.quantity / 24
                total_weight += item.quantity * 1
            elif '2kg' in desc_lower:
                total_bales += item.quantity / 12
                total_weight += item.quantity * 2
            else:
                total_weight += item.total_weight_kg
        
        dispatch_data.append({
            'dispatch': dispatch,
            'total_bales': round(total_bales, 1),
            'total_weight': round(total_weight, 2)
        })
    
    return dispatch_data


def get_financial_chart_data(period='weekly'):
    """Get revenue vs expenses for chart"""
    today = date.today()
    
    if period == 'monthly':
        days = 30
    else:
        days = 7
    
    chart_data = []
    for i in range(days):
        current_date = today - timedelta(days=(days - 1 - i))
        
        # Revenue from invoices
        revenue = db.session.query(
            db.func.sum(Invoice.grand_total)
        ).filter(
            Invoice.invoice_date == current_date
        ).scalar() or 0
        
        # Expenses from suppliers
        expenses = db.session.query(
            db.func.sum(Supplier.grand_total)
        ).filter(
            Supplier.transaction_date == current_date
        ).scalar() or 0
        
        chart_data.append({
            'date': current_date.strftime('%b %d'),
            'revenue': float(revenue),
            'expenses': float(expenses)
        })
    
    return chart_data
# ============================================================================
# DECORATORS
# ============================================================================

def manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['manager', 'admin']:
            flash('Access denied. Manager privileges required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            flash('Access denied. Administrator privileges required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# LOGIN MANAGER
# ============================================================================

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ============================================================================
# ROUTES - AUTHENTICATION
# ============================================================================

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if user.is_blocked:
                flash('Your account has been blocked. Contact administrator.', 'danger')
                return redirect(url_for('login'))
            
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            log_audit('login', 'user', user.id)
            
            if user.must_change_password:
                return redirect(url_for('change_password'))
            
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    log_audit('logout', 'user', current_user.id)
    logout_user()
    flash('You have been logged out successfully', 'success')
    return redirect(url_for('login'))


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # Validate inputs
        if not current_password or not new_password or not confirm_password:
            flash('All fields are required', 'danger')
            return redirect(url_for('change_password'))
        
        if not current_user.check_password(current_password):
            flash('Current password is incorrect', 'danger')
            return redirect(url_for('change_password'))
        
        if new_password != confirm_password:
            flash('New passwords do not match', 'danger')
            return redirect(url_for('change_password'))
        
        if len(new_password) < 8:
            flash('Password must be at least 8 characters long', 'danger')
            return redirect(url_for('change_password'))
        
        current_user.set_password(new_password)
        current_user.must_change_password = False
        db.session.commit()
        
        log_audit('password_change', 'user', current_user.id)
        
        flash('Password changed successfully', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('change_password.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username')
        user = User.query.filter_by(username=username).first()
        
        if user:
            # Create reset request
            reset_request = PasswordResetRequest(user_id=user.id)
            db.session.add(reset_request)
            db.session.commit()
            
            flash('Password reset request submitted. An administrator will contact you.', 'success')
        else:
            flash('Username not found', 'danger')
        
        return redirect(url_for('login'))
    
    return render_template('forgot_password.html')


# ============================================================================
# ROUTES - DASHBOARD ROUTING
# ============================================================================

@app.route('/dashboard')
@login_required
def dashboard():
    """Route to appropriate dashboard based on role"""
    if current_user.role == 'worker':
        return redirect(url_for('worker_dashboard'))
    elif current_user.role == 'manager':
        return redirect(url_for('manager_dashboard'))
    elif current_user.role == 'director':
        return redirect(url_for('director_dashboard'))
    elif current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    else:
        flash('Invalid role', 'danger')
        return redirect(url_for('logout'))


# ============================================================================
# ROUTES - WORKER DASHBOARD
# ============================================================================
@app.route('/worker/dashboard')
@login_required
def worker_dashboard():
    """Worker dashboard with clock-in gate"""
    if current_user.role not in ['worker', 'manager', 'admin']:
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    today = date.today()
    
    # Check clock status
    clock_record = WorkerClock.query.filter_by(
        user_id=current_user.id,
        date=today,
        clock_out_time=None
    ).first()
    
    is_clocked_in = clock_record is not None
    clock_in_time = clock_record.clock_in_time if clock_record else None
    hours_worked = 0
    
    if is_clocked_in and clock_in_time:
        hours_worked = (datetime.now() - clock_in_time).total_seconds() / 3600
    
    # Get today's stats (personal counts)
    daily_count = IntakeSheet.query.filter_by(
        worker_id=current_user.id,
        sheet_date=today,
        sheet_type='daily'
    ).count()
    
    received_count = IntakeSheet.query.filter_by(
        worker_id=current_user.id,
        sheet_date=today,
        sheet_type='received'
    ).count()
    
    outstock_count = IntakeSheet.query.filter_by(
        worker_id=current_user.id,
        sheet_date=today,
        sheet_type='outstock'
    ).count()
    
    invoice_count = Invoice.query.filter_by(
        created_by_id=current_user.id,
        invoice_date=today
    ).count()
    
    # Get today's sheets
    today_sheets = IntakeSheet.query.filter_by(
        worker_id=current_user.id,
        sheet_date=today
    ).order_by(IntakeSheet.created_at.desc()).all()
    
    # Get all worker's invoices (not just today)
    all_invoices = Invoice.query.filter_by(
        created_by_id=current_user.id
    ).order_by(Invoice.created_at.desc()).all()
    
    # Get worker's requisitions
    requisitions = InternalRequisition.query.filter_by(
        requested_by_id=current_user.id
    ).order_by(InternalRequisition.date_requested.desc()).all()
    
    # Get critical alerts only (not all low stock warnings)
    critical_alert = None
    unprocessed = UnprocessedInventory.query.all()
    for item in unprocessed:
        if item.remaining_bags == 0:
            critical_alert = f"CRITICAL: {item.product_type} stock is at ZERO"
            break
    
    # Calculate sheet data
    sheet_data = []
    for sheet in today_sheets:
        total_cages = len(sheet.entries)
        total_weight = sum(entry.weight for entry in sheet.entries)
        bag_weight = 90 if sheet.product_type in ['Maize Grains', 'Maize Germ'] else 50
        total_bags = total_weight / bag_weight if bag_weight > 0 else 0
        
        sheet_data.append({
            'sheet': sheet,
            'total_cages': total_cages,
            'total_weight': total_weight,
            'total_bags': total_bags
        })
    
    return render_template('worker_dashboard.html',
                         today=today,
                         is_clocked_in=is_clocked_in,
                         clock_in_time=clock_in_time,
                         hours_worked=round(hours_worked, 2),
                         daily_count=daily_count,
                         received_count=received_count,
                         outstock_count=outstock_count,
                         invoice_count=invoice_count,
                         sheet_data=sheet_data,
                         all_invoices=all_invoices,
                         requisitions=requisitions,
                         critical_alert=critical_alert)



@app.route('/manager/dashboard')
@login_required
@manager_required
def manager_dashboard():
    """Manager dashboard - operational command center"""
    
    # Get today's date
    today = date.today()
    
    # ========================================================================
    # TODAY'S OPERATIONAL STATS (6 cards)
    # ========================================================================
    
    # Total sheets created today
    total_sheets_today = IntakeSheet.query.filter_by(sheet_date=today).count()
    
    # Calculate bags received today (ALL closed received sheets)
    received_sheets_today = IntakeSheet.query.filter_by(
        sheet_date=today,
        sheet_type='received',
        status='Closed'
    ).all()
    
    total_received_bags_today = 0
    for sheet in received_sheets_today:
        total_weight = sum(entry.weight for entry in sheet.entries)
        bag_weight = 90 if sheet.product_type in ['Maize Grains', 'Maize Germ'] else 50
        total_received_bags_today += (total_weight / bag_weight if bag_weight > 0 else 0)
    
    # Calculate UNITS dispatched today (from processed inventory - 1kg bags, 2kg bags, etc.)
    dispatches_today = Dispatch.query.filter_by(
        dispatch_date=today,
        status='approved'
    ).all()
    
    total_dispatched_units_today = 0
    for dispatch in dispatches_today:
        for item in dispatch.items:
            total_dispatched_units_today += item.quantity
    
    # Calculate bags lost today
    lost_sheets_today = IntakeSheet.query.filter_by(
        sheet_date=today,
        sheet_type='lost',
        status='Closed'
    ).all()
    
    total_lost_bags_today = 0
    for sheet in lost_sheets_today:
        total_weight = sum(entry.weight for entry in sheet.entries)
        bag_weight = 90 if sheet.product_type in ['Maize Grains', 'Maize Germ'] else 50
        total_lost_bags_today += (total_weight / bag_weight if bag_weight > 0 else 0)
    
    # Active workers today (workers who created sheets)
    active_workers_today = len(set(sheet.worker_id for sheet in IntakeSheet.query.filter_by(sheet_date=today).all()))
    
    # Pending authorizations
    pending_auth_sheets = IntakeSheet.query.filter_by(
        sheet_type='outstock',
        authorization_status='pending'
    ).all()
    pending_auths_count = len(pending_auth_sheets)
    
    # ========================================================================
    # FINANCIAL SUMMARY (3 cards - Daily)
    # ========================================================================
    
    # Daily sales (invoices)
    daily_invoices = Invoice.query.filter_by(invoice_date=today).all()
    daily_sales = sum(inv.grand_total for inv in daily_invoices)
    
    # Daily purchases (suppliers)
    daily_suppliers = Supplier.query.filter_by(transaction_date=today).all()
    daily_purchases = sum(sup.grand_total for sup in daily_suppliers)
    
    # Daily profit
    daily_profit = daily_sales - daily_purchases
    
    # ========================================================================
    # PENDING AUTHORIZATIONS (Full sheet objects)
    # ========================================================================
    
    pending_auth_data = []
    for sheet in pending_auth_sheets:
        total_weight = sum(entry.weight for entry in sheet.entries)
        bag_weight = 90 if sheet.product_type in ['Maize Grains', 'Maize Germ'] else 50
        total_bags = total_weight / bag_weight if bag_weight > 0 else 0
        
        pending_auth_data.append({
            'sheet': sheet,
            'total_bags': total_bags,
            'time_ago': (datetime.utcnow() - sheet.created_at).seconds // 60  # minutes ago
        })
    
    # ========================================================================
    # INVENTORY OVERVIEW - REAL-TIME CALCULATION
    # ========================================================================
    
    # RAW UNPROCESSED STOCK - ONLY MAIZE GRAINS (90kg bags)
    # Calculate from ALL received sheets for Maize Grains
    maize_received_sheets = IntakeSheet.query.filter_by(
        product_type='Maize Grains',
        sheet_type='received',
        status='Closed'
    ).all()
    
    total_maize_received_kg = sum(
        sum(entry.weight for entry in sheet.entries)
        for sheet in maize_received_sheets
    )
    total_maize_received_bags = total_maize_received_kg / 90
    
    # Calculate outstock
    maize_outstock_sheets = IntakeSheet.query.filter_by(
        product_type='Maize Grains',
        sheet_type='outstock',
        status='Closed',
        authorization_status='authorized'
    ).all()
    
    total_maize_outstock_kg = sum(
        sum(entry.weight for entry in sheet.entries)
        for sheet in maize_outstock_sheets
    )
    total_maize_outstock_bags = total_maize_outstock_kg / 90
    
    # Calculate lost
    maize_lost_sheets = IntakeSheet.query.filter_by(
        product_type='Maize Grains',
        sheet_type='lost',
        status='Closed'
    ).all()
    
    total_maize_lost_kg = sum(
        sum(entry.weight for entry in sheet.entries)
        for sheet in maize_lost_sheets
    )
    total_maize_lost_bags = total_maize_lost_kg / 90
    
    # Calculate sent to processing
    maize_packaging_records = PackagingRecord.query.filter(
        PackagingRecord.product_type.in_(['maize_grains_1kg', 'maize_grains_2kg'])
    ).all()
    
    total_maize_sent_kg = sum(record.total_weight_kg for record in maize_packaging_records)
    total_maize_sent_bags = total_maize_sent_kg / 90
    
    # Remaining maize grains
    remaining_maize_bags = (
        total_maize_received_bags - 
        total_maize_outstock_bags - 
        total_maize_lost_bags - 
        total_maize_sent_bags
    )
    
    maize_inventory = {
        'product_type': 'Maize Grains (Raw)',
        'total_received_bags': total_maize_received_bags,
        'total_outstock_bags': total_maize_outstock_bags,
        'total_lost_bags': total_maize_lost_bags,
        'total_sent_bags': total_maize_sent_bags,
        'remaining_bags': remaining_maize_bags,
        'is_low': remaining_maize_bags < 50,
        'is_critical': remaining_maize_bags < 20
    }
    # ========================================================================
    # PROCESSED INVENTORY - REAL-TIME CALCULATION
    # ========================================================================
    
    # Maize Flour 1KG - Calculate from packaging records
    packaging_1kg = PackagingRecord.query.filter_by(product_type='maize_flour_1kg').all()
    total_1kg_packaged = sum(p.quantity for p in packaging_1kg)
    
    # Calculate dispatched 1kg
    dispatched_1kg_items = DispatchItem.query.filter(
        DispatchItem.item_description.contains('1KG'),
        DispatchItem.dispatch.has(status='approved')
    ).all()
    total_1kg_dispatched = sum(d.quantity for d in dispatched_1kg_items)
    
    remaining_1kg = total_1kg_packaged - total_1kg_dispatched
    bales_1kg = remaining_1kg / 24  # 24 units per bale
    weight_1kg = remaining_1kg * 1
    
    # Maize Flour 2KG
    packaging_2kg = PackagingRecord.query.filter_by(product_type='maize_flour_2kg').all()
    total_2kg_packaged = sum(p.quantity for p in packaging_2kg)
    
    dispatched_2kg_items = DispatchItem.query.filter(
        DispatchItem.item_description.contains('2KG'),
        DispatchItem.dispatch.has(status='approved')
    ).all()
    total_2kg_dispatched = sum(d.quantity for d in dispatched_2kg_items)
    
    remaining_2kg = total_2kg_packaged - total_2kg_dispatched
    bales_2kg = remaining_2kg / 12  # 12 units per bale
    weight_2kg = remaining_2kg * 2
    
    # Maize Germ
    packaging_germ = PackagingRecord.query.filter_by(product_type='maize_germ').all()
    total_germ_packaged_kg = sum(p.total_weight_kg for p in packaging_germ)
    
    dispatched_germ_items = DispatchItem.query.filter(
        DispatchItem.item_description.contains('Germ'),
        DispatchItem.dispatch.has(status='approved')
    ).all()
    total_germ_dispatched_kg = sum(d.total_weight_kg for d in dispatched_germ_items)
    
    remaining_germ_kg = total_germ_packaged_kg - total_germ_dispatched_kg
    
    # Animal Feeds
    packaging_feeds = PackagingRecord.query.filter_by(product_type='animal_feeds').all()
    total_feeds_packaged_kg = sum(p.total_weight_kg for p in packaging_feeds)
    
    dispatched_feeds_items = DispatchItem.query.filter(
        DispatchItem.item_description.contains('Feed'),
        DispatchItem.dispatch.has(status='approved')
    ).all()
    total_feeds_dispatched_kg = sum(d.total_weight_kg for d in dispatched_feeds_items)
    
    remaining_feeds_kg = total_feeds_packaged_kg - total_feeds_dispatched_kg
    
    processed_summary = {
        'maize_flour_1kg': {
            'units': remaining_1kg,
            'bales': bales_1kg,
            'weight_kg': weight_1kg,
            'is_low': remaining_1kg < 100,
            'is_critical': remaining_1kg < 50
        },
        'maize_flour_2kg': {
            'units': remaining_2kg,
            'bales': bales_2kg,
            'weight_kg': weight_2kg,
            'is_low': remaining_2kg < 100,
            'is_critical': remaining_2kg < 50
        },
        'maize_germ': {
            'weight_kg': remaining_germ_kg,
            'is_low': remaining_germ_kg < 50,
            'is_critical': remaining_germ_kg < 20
        },
        'animal_feeds': {
            'weight_kg': remaining_feeds_kg,
            'is_low': remaining_feeds_kg < 50,
            'is_critical': remaining_feeds_kg < 20
        }
    }
    
    
    
    # ========================================================================
    # OUTSTANDING INVOICES
    # ========================================================================
    
    outstanding_invoices = Invoice.query.filter(
        Invoice.payment_status.in_(['unpaid', 'partial'])
    ).order_by(Invoice.invoice_date.desc()).limit(10).all()
    
    # ========================================================================
    # RECENT ACTIVITY (Last 15 closed sheets)
    # ========================================================================
    
    recent_sheets = IntakeSheet.query.filter(
        IntakeSheet.status == 'Closed'
    ).order_by(IntakeSheet.closed_at.desc()).limit(15).all()
    
    recent_activity = []
    for sheet in recent_sheets:
        if sheet.sheet_type in ['received', 'outstock', 'lost']:
            total_weight = sum(entry.weight for entry in sheet.entries)
            bag_weight = 90 if sheet.product_type in ['Maize Grains', 'Maize Germ'] else 50
            total_bags = total_weight / bag_weight if bag_weight > 0 else 0
            
            recent_activity.append({
                'sheet': sheet,
                'total_bags': total_bags,
                'closed_at': sheet.closed_at or sheet.created_at
            })
    
    # ========================================================================
    # ALERTS
    # ========================================================================
    
    alerts = []
    
    # Check maize grains inventory
    if remaining_maize_bags < 20:
        alerts.append({
            'level': 'critical',
            'product': 'Maize Grains (Raw)',
            'remaining': remaining_maize_bags,
            'message': f'CRITICAL: Only {remaining_maize_bags:.1f} bags remaining!'
        })
    elif remaining_maize_bags < 50:
        alerts.append({
            'level': 'warning',
            'product': 'Maize Grains (Raw)',
            'remaining': remaining_maize_bags,
            'message': f'Low stock: {remaining_maize_bags:.1f} bags remaining'
        })
    
    # Check processed inventory
    if remaining_1kg < 50:
        alerts.append({
            'level': 'critical',
            'product': 'Maize Flour 1KG',
            'remaining': remaining_1kg,
            'message': f'CRITICAL: Only {remaining_1kg:.0f} units remaining!'
        })
    elif remaining_1kg < 100:
        alerts.append({
            'level': 'warning',
            'product': 'Maize Flour 1KG',
            'remaining': remaining_1kg,
            'message': f'Low stock: {remaining_1kg:.0f} units remaining'
        })
    
    if remaining_2kg < 50:
        alerts.append({
            'level': 'critical',
            'product': 'Maize Flour 2KG',
            'remaining': remaining_2kg,
            'message': f'CRITICAL: Only {remaining_2kg:.0f} units remaining!'
        })
    elif remaining_2kg < 100:
        alerts.append({
            'level': 'warning',
            'product': 'Maize Flour 2KG',
            'remaining': remaining_2kg,
            'message': f'Low stock: {remaining_2kg:.0f} units remaining'
        })
    
    # Password reset requests
    reset_requests = PasswordResetRequest.query.filter_by(status='pending').all()
    
    # Calculate urgent items count for greeting
    urgent_count = pending_auths_count + len(reset_requests) + len([a for a in alerts if a['level'] == 'critical'])
    
    # ========================================================================
    # RENDER TEMPLATE
    # ========================================================================
    
    return render_template('manager_dashboard.html',
                         today=today,
                         current_user=current_user,
                         # Operational stats
                         total_sheets_today=total_sheets_today,
                         total_received_bags_today=total_received_bags_today,
                         total_dispatched_units_today=total_dispatched_units_today,
                         total_lost_bags_today=total_lost_bags_today,
                         active_workers_today=active_workers_today,
                         pending_auths_count=pending_auths_count,
                         # Financial
                         daily_sales=daily_sales,
                         daily_purchases=daily_purchases,
                         daily_profit=daily_profit,
                         # Pending authorizations
                         pending_auth_data=pending_auth_data,
                         # Inventory
                         maize_inventory=maize_inventory,
                         processed_summary=processed_summary,
                         # Outstanding invoices
                         outstanding_invoices=outstanding_invoices,
                         # Recent activity
                         recent_activity=recent_activity,
                         # Alerts
                         alerts=alerts,
                         reset_requests=reset_requests,
                         urgent_count=urgent_count)


@app.route('/manager/worker-mode')
@login_required
@manager_required
def manager_worker_mode():
    """Manager working as worker - redirects to worker dashboard"""
    return redirect(url_for('worker_dashboard'))
# ============================================================================
# ROUTES - DIRECTOR DASHBOARD
# ============================================================================
@app.route('/director/dashboard')
@login_required
def director_dashboard():
    """Director Dashboard - Executive Command Center (Read-Only)"""
    if current_user.role != 'director':
        flash('Access denied. Director privileges required.', 'danger')
        return redirect(url_for('dashboard'))
    
    today = date.today()
    month_start = date(today.year, today.month, 1)
    
    # ========== 6 KPI CARDS ==========

    # 1. Total Revenue This Month
    total_revenue = db.session.query(
        db.func.sum(Invoice.grand_total)
    ).filter(
        Invoice.invoice_date >= month_start
    ).scalar() or 0

    # 2. Total Purchases This Month
    total_purchases = db.session.query(
        db.func.sum(Supplier.grand_total)
    ).filter(
        Supplier.transaction_date >= month_start
    ).scalar() or 0

    # 3. Gross Profit This Month
    gross_profit = total_revenue - total_purchases

    # 4. Current Raw Stock (total remaining bags across all products)
    raw_stock = db.session.query(
        db.func.sum(UnprocessedInventory.remaining_bags)
    ).scalar() or 0

    # 5. Current Packaged Stock in Bales
    packaging_1kg_total = db.session.query(
        db.func.sum(PackagingRecord.quantity)
    ).filter(
        PackagingRecord.product_type == 'maize_flour_1kg'
    ).scalar() or 0

    packaging_2kg_total = db.session.query(
        db.func.sum(PackagingRecord.quantity)
    ).filter(
        PackagingRecord.product_type == 'maize_flour_2kg'
    ).scalar() or 0

    dispatched_1kg_total = db.session.query(
        db.func.sum(DispatchItem.quantity)
    ).filter(
        DispatchItem.item_description.like('%1KG%')
    ).scalar() or 0

    dispatched_2kg_total = db.session.query(
        db.func.sum(DispatchItem.quantity)
    ).filter(
        DispatchItem.item_description.like('%2KG%')
    ).scalar() or 0

    remaining_1kg = packaging_1kg_total - dispatched_1kg_total
    remaining_2kg = packaging_2kg_total - dispatched_2kg_total
    packaged_stock_bales = (remaining_1kg / 24) + (remaining_2kg / 12)

    # 6. Workers Currently Clocked In
    workers_clocked_in = WorkerClock.query.filter_by(
        date=today,
        clock_out_time=None
    ).count()

    # ========== CRITICAL ALERT ==========
    critical_alert = get_critical_alert()

    # ========== FINANCIAL DATA ==========
    customers = get_customer_financials()
    suppliers = get_supplier_financials()
    chart_data = get_financial_chart_data('weekly')

    # ========== MONTHLY FINANCIAL SUMMARY (Daily/Weekly/Monthly tabs) ==========
    # Daily
    daily_revenue = db.session.query(
        db.func.sum(Invoice.grand_total)
    ).filter(Invoice.invoice_date == today).scalar() or 0

    daily_purchases = db.session.query(
        db.func.sum(Supplier.grand_total)
    ).filter(Supplier.transaction_date == today).scalar() or 0

    daily_profit = daily_revenue - daily_purchases

    # Weekly
    week_start = today - timedelta(days=today.weekday())
    weekly_revenue = db.session.query(
        db.func.sum(Invoice.grand_total)
    ).filter(Invoice.invoice_date >= week_start).scalar() or 0

    weekly_purchases = db.session.query(
        db.func.sum(Supplier.grand_total)
    ).filter(Supplier.transaction_date >= week_start).scalar() or 0

    weekly_profit = weekly_revenue - weekly_purchases

    financial_tabs = {
        'daily': {
            'revenue': daily_revenue,
            'purchases': daily_purchases,
            'profit': daily_profit
        },
        'weekly': {
            'revenue': weekly_revenue,
            'purchases': weekly_purchases,
            'profit': weekly_profit
        },
        'monthly': {
            'revenue': total_revenue,
            'purchases': total_purchases,
            'profit': gross_profit
        }
    }

    # ========== INVENTORY DATA ==========
    unprocessed_inventory = UnprocessedInventory.query.all()
    processed_inventory = ProcessedInventory.query.all()

    # ========== WORKER ATTENDANCE ==========
    worker_attendance = get_worker_attendance()

    # ========== RECENT DISPATCHES ==========
    recent_dispatches = get_recent_dispatches(30)

    # ========== AUDIT TRAIL ==========
    recent_audits = AuditLog.query.order_by(
        AuditLog.timestamp.desc()
    ).limit(50).all()

    # ========== NOTIFICATIONS ==========
    week_ago = datetime.now() - timedelta(days=7)
    recent_notifications = SystemNotification.query.filter(
        SystemNotification.created_at >= week_ago
    ).order_by(SystemNotification.created_at.desc()).all()

    return render_template('director_dashboard.html',
                         today=today,
                         # KPI Cards
                         total_revenue=total_revenue,
                         total_purchases=total_purchases,
                         gross_profit=gross_profit,
                         raw_stock=raw_stock,
                         packaged_stock_bales=packaged_stock_bales,
                         workers_clocked_in=workers_clocked_in,
                         # Alert
                         critical_alert=critical_alert,
                         # Financial
                         customers=customers,
                         suppliers=suppliers,
                         chart_data=chart_data,
                         financial_tabs=financial_tabs,
                         # Inventory
                         unprocessed_inventory=unprocessed_inventory,
                         processed_inventory=processed_inventory,
                         # Attendance
                         worker_attendance=worker_attendance,
                         # Dispatches
                         recent_dispatches=recent_dispatches,
                         # Audit
                         recent_audits=recent_audits,
                         # Notifications
                         recent_notifications=recent_notifications)

# ============================================================================
# ROUTES - WORKER CLOCKING SYSTEM
# ============================================================================

# Warehouse WiFi IP address
WAREHOUSE_IP = '127.0.0.1'  # Your home WiFi IP for testing

@app.route('/worker/clock-in', methods=['POST'])
@login_required
def worker_clock_in():
    """Clock in worker (IP-locked to warehouse WiFi)"""
    # Allow workers AND managers to clock in
    if current_user.role not in ['worker', 'manager', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    # Get client IP
    client_ip = request.remote_addr
    
    # Check if IP matches warehouse
    if client_ip != WAREHOUSE_IP:
        return jsonify({
            'success': False,
            'message': 'You must be connected to the warehouse WiFi to clock in. Please check your connection and try again.'
        }), 403
    
    # Check if already clocked in today
    today = date.today()
    existing_clock = WorkerClock.query.filter_by(
        user_id=current_user.id,
        date=today,
        clock_out_time=None
    ).first()
    
    if existing_clock:
        return jsonify({
            'success': False,
            'message': 'You are already clocked in today'
        }), 400
    
    # Create clock-in record
    clock_record = WorkerClock(
        user_id=current_user.id,
        clock_in_time=datetime.now(),
        clock_in_ip=client_ip,
        date=today
    )
    
    db.session.add(clock_record)
    db.session.commit()
    
    log_audit('clock_in', 'worker_clock', clock_record.id)
    
    return jsonify({
        'success': True,
        'message': 'Clocked in successfully',
        'clock_in_time': clock_record.clock_in_time.strftime('%I:%M %p')
    })


@app.route('/worker/clock-out', methods=['POST'])
@login_required
def worker_clock_out():
    """Clock out worker"""
    # Allow workers AND managers to clock out
    if current_user.role not in ['worker', 'manager', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    # Get today's clock record
    today = date.today()
    clock_record = WorkerClock.query.filter_by(
        user_id=current_user.id,
        date=today,
        clock_out_time=None
    ).first()
    
    if not clock_record:
        return jsonify({
            'success': False,
            'message': 'No active clock-in found'
        }), 400
    
    # Update clock-out
    clock_record.clock_out_time = datetime.now()
    clock_record.clock_out_ip = request.remote_addr
    clock_record.calculate_hours()
    
    db.session.commit()
    
    log_audit('clock_out', 'worker_clock', clock_record.id)
    
    return jsonify({
        'success': True,
        'message': f'Clocked out successfully. Total hours: {clock_record.hours_worked:.2f}',
        'hours_worked': clock_record.hours_worked
    })


@app.route('/worker/clock-status')
@login_required
def worker_clock_status():
    """Get current clock status"""
    # Allow workers AND managers
    if current_user.role not in ['worker', 'manager', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    today = date.today()
    clock_record = WorkerClock.query.filter_by(
        user_id=current_user.id,
        date=today,
        clock_out_time=None
    ).first()
    
    if clock_record:
        # Calculate current hours
        hours_worked = (datetime.now() - clock_record.clock_in_time).total_seconds() / 3600
        
        return jsonify({
            'clocked_in': True,
            'clock_in_time': clock_record.clock_in_time.strftime('%I:%M %p'),
            'hours_worked': round(hours_worked, 2)
        })
    
    return jsonify({'clocked_in': False})






# ============================================================================
# ROUTES - ADMIN DASHBOARD
# ============================================================================

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    """Admin dashboard for user management"""
    users = User.query.all()
    reset_requests = PasswordResetRequest.query.filter_by(status='pending').all()
    
    return render_template('admin_dashboard.html',
                         users=users,
                         reset_requests=reset_requests)


# ============================================================================
# ROUTES - SHEET MANAGEMENT
# ============================================================================

@app.route('/sheet/create', methods=['POST'])
@login_required
def create_sheet():
    """Create a new sheet"""
    if current_user.role not in ['worker', 'manager', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    product_type = request.form.get('product_type')
    sheet_type = request.form.get('sheet_type', 'daily')
    
    # Only managers can create lost/damaged sheets
    if sheet_type == 'lost' and current_user.role not in ['manager', 'admin']:
        return jsonify({'success': False, 'message': 'Only managers can create Lost/Damaged sheets'}), 403
    
    if not product_type:
        return jsonify({'success': False, 'message': 'Product type is required'}), 400
    
    # Check if sheet already exists for today
    existing_sheet = IntakeSheet.query.filter_by(
        worker_id=current_user.id,
        sheet_date=date.today(),
        product_type=product_type,
        sheet_type=sheet_type,
        status='In Progress'
    ).first()
    
    if existing_sheet:
        return jsonify({
            'success': True,
            'message': 'Sheet already exists',
            'sheet_id': existing_sheet.id
        })
    
    # Create new sheet
    sheet = IntakeSheet(
        worker_id=current_user.id,
        product_type=product_type,
        sheet_type=sheet_type,
        is_manager_created=(current_user.role in ['manager', 'admin'])
    )
    
    db.session.add(sheet)
    db.session.commit()
    
    log_audit('create_sheet', 'intake_sheet', sheet.id)
    
    return jsonify({
        'success': True,
        'message': 'Sheet created successfully',
        'sheet_id': sheet.id
    })


@app.route('/sheet/<int:sheet_id>')
@login_required
def view_sheet(sheet_id):
    """View sheet details"""
    sheet = IntakeSheet.query.get_or_404(sheet_id)
    
    # Check permissions
    if current_user.role == 'worker' and sheet.worker_id != current_user.id:
        flash('Access denied', 'danger')
        return redirect(url_for('worker_dashboard'))
    
    # Calculate totals
    total_cages = len(sheet.entries)
    total_weight = sum(entry.weight for entry in sheet.entries)
    
    if sheet.product_type in ['Maize Grains', 'Maize Germ']:
        bag_weight = 90
    else:
        bag_weight = 50
    
    total_bags = total_weight / bag_weight if bag_weight > 0 else 0
    
    # Get next cage number
    if sheet.entries:
        last_entry = sheet.entries[-1]
        try:
            last_number = int(last_entry.cage_number)
            next_cage = str(last_number + 1)
        except:
            next_cage = "1"
    else:
        next_cage = "1"
    
    # Check if editable
    is_editable = (
        sheet.status == 'In Progress' and
        (current_user.id == sheet.worker_id or current_user.role in ['manager', 'admin'])
    )
    
    # Manager can always edit (even closed sheets)
    if current_user.role in ['manager', 'admin']:
        is_editable = True
    
    # Check if can authorize
    can_authorize = (
        current_user.role in ['manager', 'admin'] and
        sheet.sheet_type == 'outstock' and
        sheet.authorization_status == 'pending'
    )
    
    return render_template('view_sheet.html',
                         sheet=sheet,
                         entries=sheet.entries,
                         total_cages=total_cages,
                         total_weight=total_weight,
                         total_bags=total_bags,
                         bag_weight=bag_weight,
                         next_cage=next_cage,
                         is_editable=is_editable,
                         can_authorize=can_authorize)


@app.route('/sheet/<int:sheet_id>/close', methods=['POST'])
@login_required
def close_sheet(sheet_id):
    """Close a sheet"""
    sheet = IntakeSheet.query.get_or_404(sheet_id)
    
    # Check permissions
    if current_user.id != sheet.worker_id and current_user.role not in ['manager', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    # Check if out stock sheet is authorized
    if sheet.sheet_type == 'outstock' and sheet.authorization_status != 'authorized':
        return jsonify({
            'success': False,
            'message': 'Out Stock sheet must be authorized before closing'
        }), 400
    
    sheet.status = 'Closed'
    sheet.closed_at = datetime.utcnow()
    db.session.commit()
    
    # Update inventory
    if sheet.sheet_type in ['received', 'outstock', 'lost']:
        update_unprocessed_inventory(sheet.product_type)
    
    log_audit('close_sheet', 'intake_sheet', sheet.id)
    
    flash('Sheet closed successfully', 'success')
    return jsonify({'success': True, 'message': 'Sheet closed successfully'})


@app.route('/sheet/<int:sheet_id>/delete', methods=['POST'])
@login_required
@manager_required
def delete_sheet(sheet_id):
    """Delete a sheet (Manager only, In Progress only)"""
    sheet = IntakeSheet.query.get_or_404(sheet_id)
    
    # Only allow deletion of In Progress sheets
    if sheet.status != 'In Progress':
        return jsonify({
            'success': False,
            'message': 'Cannot delete closed sheets. Closed sheets are permanent records.'
        }), 403
    
    # Store info for flash message
    sheet_info = f"{sheet.product_type} - {sheet.sheet_date.strftime('%Y-%m-%d')}"
    
    log_audit('delete_sheet', 'intake_sheet', sheet.id, old_value=sheet_info)
    
    # Delete sheet (cascade will delete entries)
    db.session.delete(sheet)
    db.session.commit()
    
    flash(f'Sheet deleted: {sheet_info}', 'success')
    return jsonify({'success': True, 'message': 'Sheet deleted successfully'})


@app.route('/sheet/<int:sheet_id>/authorize', methods=['POST'])
@login_required
@manager_required
def authorize_sheet(sheet_id):
    """Authorize an out stock sheet"""
    sheet = IntakeSheet.query.get_or_404(sheet_id)
    
    if sheet.sheet_type != 'outstock':
        return jsonify({'success': False, 'message': 'Only out stock sheets can be authorized'}), 400
    
    destination = request.form.get('destination')
    purpose = request.form.get('purpose')
    
    if not destination or not purpose:
        return jsonify({'success': False, 'message': 'Destination and purpose are required'}), 400
    
    sheet.authorization_status = 'authorized'
    sheet.authorized_by_id = current_user.id
    sheet.destination = destination
    sheet.purpose = purpose
    
    db.session.commit()
    
    log_audit('authorize_sheet', 'intake_sheet', sheet.id)
    
    return jsonify({'success': True, 'message': 'Sheet authorized successfully'})


@app.route('/sheet/<int:sheet_id>/reject', methods=['POST'])
@login_required
@manager_required
def reject_sheet(sheet_id):
    """Reject an out stock sheet"""
    sheet = IntakeSheet.query.get_or_404(sheet_id)
    
    if sheet.sheet_type != 'outstock':
        return jsonify({'success': False, 'message': 'Only out stock sheets can be rejected'}), 400
    
    sheet.authorization_status = 'rejected'
    db.session.commit()
    
    log_audit('reject_sheet', 'intake_sheet', sheet.id)
    
    flash('Sheet rejected', 'success')
    return jsonify({'success': True, 'message': 'Sheet rejected'})


# ============================================================================
# ROUTES - ENTRY MANAGEMENT
# ============================================================================

@app.route('/entry/add', methods=['POST'])
@login_required
def add_entry():
    """Add an entry to a sheet"""
    sheet_id = request.form.get('sheet_id')
    cage_number = request.form.get('cage_number')
    weight = request.form.get('weight')
    
    if not all([sheet_id, cage_number, weight]):
        return jsonify({'success': False, 'message': 'All fields are required'}), 400
    
    sheet = IntakeSheet.query.get_or_404(sheet_id)
    
    # Check permissions
    if current_user.id != sheet.worker_id and current_user.role not in ['manager', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    # Managers can add to closed sheets
    if sheet.status == 'Closed' and current_user.role not in ['manager', 'admin']:
        return jsonify({'success': False, 'message': 'Sheet is closed'}), 403
    
    try:
        weight_float = float(weight)
    except:
        return jsonify({'success': False, 'message': 'Invalid weight value'}), 400
    
    entry = IntakeEntry(
        sheet_id=sheet_id,
        cage_number=cage_number,
        weight=weight_float
    )
    
    db.session.add(entry)
    db.session.commit()
    
    log_audit('add_entry', 'intake_entry', entry.id)
    
    return jsonify({'success': True, 'message': 'Entry added successfully'})


@app.route('/entry/edit/<int:entry_id>', methods=['POST'])
@login_required
def edit_entry(entry_id):
    """Edit an entry"""
    entry = IntakeEntry.query.get_or_404(entry_id)
    sheet = entry.sheet
    
    # Check permissions
    if current_user.role not in ['manager', 'admin']:
        if current_user.id != sheet.worker_id or sheet.status == 'Closed':
            return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    cage_number = request.form.get('cage_number')
    weight = request.form.get('weight')
    
    if not all([cage_number, weight]):
        return jsonify({'success': False, 'message': 'All fields are required'}), 400
    
    try:
        weight_float = float(weight)
    except:
        return jsonify({'success': False, 'message': 'Invalid weight value'}), 400
    
    old_value = f"Cage: {entry.cage_number}, Weight: {entry.weight}"
    
    entry.cage_number = cage_number
    entry.weight = weight_float
    
    db.session.commit()
    
    new_value = f"Cage: {entry.cage_number}, Weight: {entry.weight}"
    log_audit('edit_entry', 'intake_entry', entry.id, old_value=old_value, new_value=new_value)
    
    return jsonify({'success': True, 'message': 'Entry updated successfully'})


@app.route('/entry/delete/<int:entry_id>', methods=['POST'])
@login_required
def delete_entry(entry_id):
    """Delete an entry (Manager only)"""
    if current_user.role not in ['manager', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    entry = IntakeEntry.query.get_or_404(entry_id)
    old_value = f"Cage: {entry.cage_number}, Weight: {entry.weight}"
    
    log_audit('delete_entry', 'intake_entry', entry.id, old_value=old_value)
    
    db.session.delete(entry)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Entry deleted successfully'})


# ============================================================================
# ROUTES - USER MANAGEMENT (ADMIN)
# ============================================================================

@app.route('/admin/user/create', methods=['POST'])
@login_required
@admin_required
def create_user():
    """Create a new user"""
    name = request.form.get('name')
    username = request.form.get('username')
    role = request.form.get('role')
    password = request.form.get('password', 'temp123')
    
    if not all([name, username, role]):
        flash('All fields are required', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Check if username exists
    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        flash('Username already exists', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    user = User(
        name=name,
        username=username,
        role=role,
        must_change_password=True
    )
    user.set_password(password)
    
    db.session.add(user)
    db.session.commit()
    
    log_audit('create_user', 'user', user.id)
    
    flash(f'User {name} created successfully', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/user/<int:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
def reset_user_password(user_id):
    """Reset a user's password"""
    user = User.query.get_or_404(user_id)
    new_password = request.form.get('new_password', 'temp123')
    
    user.set_password(new_password)
    user.must_change_password = True
    
    db.session.commit()
    
    log_audit('reset_password', 'user', user.id)
    
    return jsonify({
        'success': True,
        'message': 'Password reset successfully',
        'new_password': new_password
    })


@app.route('/admin/user/<int:user_id>/block', methods=['POST'])
@login_required
@admin_required
def toggle_user_block(user_id):
    """Block/unblock a user"""
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Cannot block yourself'}), 403
    
    user.is_blocked = not user.is_blocked
    db.session.commit()
    
    action = 'block_user' if user.is_blocked else 'unblock_user'
    log_audit(action, 'user', user.id)
    
    return jsonify({
        'success': True,
        'message': f'User {"blocked" if user.is_blocked else "unblocked"} successfully'
    })


@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Cannot delete yourself'}), 403

    # Log deletion first (before user is gone)
    log_audit('delete_user', 'user', user_id, old_value=f"{user.name} ({user.username})")

    # 1. Audit logs
    AuditLog.query.filter_by(user_id=user_id).delete()

    # 2. Password reset requests
    PasswordResetRequest.query.filter_by(user_id=user_id).delete()
    PasswordResetRequest.query.filter_by(resolved_by_id=user_id).update({'resolved_by_id': None})

    # 3. Clock records
    WorkerClock.query.filter_by(user_id=user_id).delete()

    # 4. Intake sheets (and their entries via cascade)
    sheets = IntakeSheet.query.filter_by(worker_id=user_id).all()
    for sheet in sheets:
        IntakeEntry.query.filter_by(sheet_id=sheet.id).delete()
    IntakeSheet.query.filter_by(worker_id=user_id).delete()
    IntakeSheet.query.filter_by(authorized_by_id=user_id).update({'authorized_by_id': None})

    # 5. Internal requisitions
    requisitions = InternalRequisition.query.filter_by(requested_by_id=user_id).all()
    for req in requisitions:
        RequisitionItem.query.filter_by(requisition_id=req.id).delete()
    InternalRequisition.query.filter_by(requested_by_id=user_id).delete()
    InternalRequisition.query.filter_by(approved_by_id=user_id).update({'approved_by_id': None})
    InternalRequisition.query.filter_by(received_by_id=user_id).update({'received_by_id': None})

    # 6. Dispatches created by this user
    dispatches = Dispatch.query.filter_by(created_by_id=user_id).all()
    for dispatch in dispatches:
        DispatchItem.query.filter_by(dispatch_id=dispatch.id).delete()
    Dispatch.query.filter_by(created_by_id=user_id).delete()
    Dispatch.query.filter_by(authorized_by_id=user_id).update({'authorized_by_id': None})

    # 7. Invoices created by this user
    invoices = Invoice.query.filter_by(created_by_id=user_id).all()
    for invoice in invoices:
        InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()
        Payment.query.filter_by(invoice_id=invoice.id).delete()
    Invoice.query.filter_by(created_by_id=user_id).delete()
    Invoice.query.filter_by(updated_by_id=user_id).update({'updated_by_id': None})

    # 8. Packaging records
    PackagingRecord.query.filter_by(packaged_by_id=user_id).delete()
    PackagingRecord.query.filter_by(created_by_id=user_id).delete()

    # 9. Supplier payments recorded by this user
    SupplierPayment.query.filter_by(recorded_by_id=user_id).delete()

    # 10. Payments recorded by this user
    Payment.query.filter_by(recorded_by_id=user_id).update({'recorded_by_id': None})

    # 11. Notifications
    SystemNotification.query.filter_by(created_by_id=user_id).update({'created_by_id': None})

    # Finally delete the user
    db.session.delete(user)
    db.session.commit()

    return jsonify({'success': True, 'message': f'User {user.name} deleted successfully'})


@app.route('/admin/reset-request/<int:request_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_reset_request(request_id):
    """Approve password reset request"""
    reset_request = PasswordResetRequest.query.get_or_404(request_id)
    new_password = request.form.get('new_password', 'temp123')
    
    # Reset password
    user = reset_request.user
    user.set_password(new_password)
    user.must_change_password = True
    
    # Update request status
    reset_request.status = 'approved'
    reset_request.resolved_by_id = current_user.id
    reset_request.resolved_at = datetime.utcnow()
    
    db.session.commit()
    
    log_audit('approve_reset_request', 'password_reset_request', reset_request.id)
    
    return jsonify({'success': True, 'message': f'Password reset approved for {user.name}'})


@app.route('/admin/reset-request/<int:request_id>/reject', methods=['POST'])
@login_required
@admin_required
def reject_reset_request(request_id):
    """Reject password reset request"""
    reset_request = PasswordResetRequest.query.get_or_404(request_id)
    
    reset_request.status = 'rejected'
    reset_request.resolved_by_id = current_user.id
    reset_request.resolved_at = datetime.utcnow()
    
    db.session.commit()
    
    log_audit('reject_reset_request', 'password_reset_request', reset_request.id)
    
    return jsonify({'success': True, 'message': 'Request rejected'})


# ============================================================================
# ROUTES - SUPPLIER MANAGEMENT
# ============================================================================

# ============================================================================
# ROUTES - SUPPLIER MANAGEMENT
# ============================================================================

@app.route('/suppliers')
@login_required
def suppliers_list():
    """List all suppliers - Manager, Admin, and Director can view"""
    if current_user.role not in ['manager', 'admin', 'director']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    suppliers = Supplier.query.order_by(Supplier.transaction_date.desc()).all()
    return render_template('suppliers/list.html', suppliers=suppliers)


@app.route('/supplier/create', methods=['GET', 'POST'])
@login_required
@manager_required
def create_supplier():
    """Create new supplier record with corrected calculations"""
    if request.method == 'POST':
        # Create supplier record
        supplier = Supplier(
            supplier_name=request.form.get('supplier_name'),
            kra_pin=request.form.get('kra_pin'),
            transaction_date=datetime.strptime(request.form.get('transaction_date'), '%Y-%m-%d').date(),
            address=request.form.get('address'),
            city=request.form.get('city'),
            phone_number=request.form.get('phone_number'),
            invoice_number=request.form.get('invoice_number'),
            lpo_number=request.form.get('lpo_number'),
            reference_code=request.form.get('reference_code'),
            authorized_by_id=current_user.id,
            authorization_date=date.today()
        )
        
        supplier.grand_total = 0  # Temporary; will be updated after items are processed
        db.session.add(supplier)
        db.session.flush()
        
        # Process items
        product_types = request.form.getlist('product_type[]')
        quantities = request.form.getlist('quantity[]')
        weights = request.form.getlist('weight[]')
        unit_prices = request.form.getlist('unit_price[]')
        price_bases = request.form.getlist('price_basis[]')
        
        grand_total = 0
        
        for prod_type, qty, weight, price, basis in zip(product_types, quantities, weights, unit_prices, price_bases):
            if prod_type and qty and price:
                quantity = float(qty)
                unit_price = float(price)
                weight_kg = float(weight) if weight else 0
                
                # Calculate based on product type
                if prod_type == 'Maize Grains':
                    total_bags = quantity
                    total_weight = weight_kg if weight_kg > 0 else (quantity * 90)
                    total_amount = total_bags * unit_price
                else:
                    total_bags = 0
                    total_weight = quantity
                    total_amount = quantity * unit_price
                
                item = SupplierItem(
                    supplier_id=supplier.id,
                    product_type=prod_type,
                    item_description=prod_type,
                    quantity=quantity,
                    total_bags=total_bags,
                    total_weight=total_weight,
                    unit_price=unit_price,
                    price_basis=basis,
                    total_amount=total_amount
                )
                db.session.add(item)
                grand_total += total_amount
        
        supplier.grand_total = grand_total
        
        # Check if initial payment was made
        payment_date = request.form.get('payment_date')
        payment_method = request.form.get('payment_method')
        
        if payment_date and payment_method:
            payment = SupplierPayment(
                supplier_id=supplier.id,
                payment_date=datetime.strptime(payment_date, '%Y-%m-%d').date(),
                amount=grand_total,
                payment_method=payment_method,
                reference_number=request.form.get('transaction_code'),
                recorded_by_id=current_user.id
            )
            db.session.add(payment)
            
            supplier.amount_paid = grand_total
            supplier.payment_status = 'paid'
            supplier.payment_date = datetime.strptime(payment_date, '%Y-%m-%d').date()
            supplier.payment_method = payment_method
            supplier.transaction_code = request.form.get('transaction_code')
        
        db.session.commit()
        log_audit('create_supplier', 'supplier', supplier.id)
        
        flash('Supplier record created successfully', 'success')
        return redirect(url_for('view_supplier', supplier_id=supplier.id))
    
    return render_template('suppliers/create.html')


@app.route('/supplier/<int:supplier_id>')
@login_required
def view_supplier(supplier_id):
    """View supplier details - Manager, Admin, and Director can view"""
    if current_user.role not in ['manager', 'admin', 'director']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
 
    supplier = Supplier.query.get_or_404(supplier_id)
    outstanding = supplier.grand_total - supplier.amount_paid
    payments = SupplierPayment.query.filter_by(
        supplier_id=supplier_id
    ).order_by(SupplierPayment.payment_date.desc()).all()
    
    return render_template('suppliers/view.html', 
                         supplier=supplier, 
                         outstanding=outstanding,
                         payments=payments)
 

@app.route('/supplier/<int:supplier_id>/add-payment', methods=['POST'])
@login_required
@manager_required
def add_supplier_payment(supplier_id):
    """Add payment to supplier purchase"""
    supplier = Supplier.query.get_or_404(supplier_id)
    
    # Validate amount
    amount = float(request.form.get('amount', 0))
    outstanding = supplier.grand_total - supplier.amount_paid
    
    if amount <= 0:
        return jsonify({'success': False, 'message': 'Amount must be greater than zero'}), 400
    
    if amount > outstanding:
        return jsonify({'success': False, 'message': f'Amount exceeds outstanding balance of KSh {outstanding:,.2f}'}), 400
    
    # Create payment record
    payment = SupplierPayment(
        supplier_id=supplier_id,
        payment_date=datetime.strptime(request.form.get('payment_date'), '%Y-%m-%d').date(),
        amount=amount,
        payment_method=request.form.get('payment_method'),
        reference_number=request.form.get('reference_number'),
        notes=request.form.get('notes'),
        recorded_by_id=current_user.id
    )
    
    db.session.add(payment)
    
    # Update supplier
    supplier.amount_paid += amount
    
    if supplier.amount_paid >= supplier.grand_total:
        supplier.payment_status = 'paid'
    elif supplier.amount_paid > 0:
        supplier.payment_status = 'partially_paid'
    
    db.session.commit()
    
    log_audit('add_supplier_payment', 'supplier', supplier_id)
    
    return jsonify({
        'success': True,
        'message': 'Payment added successfully',
        'new_status': supplier.payment_status,
        'amount_paid': supplier.amount_paid,
        'outstanding': supplier.grand_total - supplier.amount_paid
    })


@app.route('/supplier/<int:supplier_id>/edit', methods=['GET', 'POST'])
@login_required
@manager_required  # ← This already blocks directors!
def edit_supplier(supplier_id):
    """Edit supplier record (MANAGER ONLY - Directors cannot edit)"""
    supplier = Supplier.query.get_or_404(supplier_id)
    
    if request.method == 'POST':
        supplier.supplier_name = request.form.get('supplier_name')
        supplier.kra_pin = request.form.get('kra_pin')
        supplier.transaction_date = datetime.strptime(request.form.get('transaction_date'), '%Y-%m-%d').date()
        supplier.address = request.form.get('address')
        supplier.city = request.form.get('city')
        supplier.phone_number = request.form.get('phone_number')
        supplier.invoice_number = request.form.get('invoice_number')
        supplier.lpo_number = request.form.get('lpo_number')
        supplier.reference_code = request.form.get('reference_code')
        
        db.session.commit()
        
        log_audit('edit_supplier', 'supplier', supplier.id)
        
        flash('Supplier record updated successfully', 'success')
        return redirect(url_for('view_supplier', supplier_id=supplier.id))
    
    return render_template('suppliers/edit.html', supplier=supplier)

@app.route('/supplier/item/<int:item_id>/delete', methods=['POST'])
@login_required
@manager_required
def delete_supplier_item(item_id):
    """Delete supplier item (Manager only) - Only for correcting errors"""
    item = SupplierItem.query.get_or_404(item_id)
    supplier = item.supplier
    
    # Store info for logging
    item_info = f"{item.product_type} - {item.quantity} - KSh {item.total_amount}"
    
    # Delete the item
    db.session.delete(item)
    
    # Recalculate supplier grand total
    remaining_items = SupplierItem.query.filter_by(supplier_id=supplier.id).all()
    new_grand_total = sum(i.total_amount for i in remaining_items if i.id != item_id)
    
    supplier.grand_total = new_grand_total
    
    # Recalculate payment status
    if supplier.amount_paid >= supplier.grand_total:
        supplier.payment_status = 'paid'
    elif supplier.amount_paid > 0:
        supplier.payment_status = 'partially_paid'
    else:
        supplier.payment_status = 'unpaid'
    
    db.session.commit()
    
    log_audit('delete_supplier_item', 'supplier_item', item_id, old_value=item_info)
    
    flash(f'Item deleted: {item_info}', 'success')
    return jsonify({'success': True, 'message': 'Item deleted successfully', 'new_total': new_grand_total})



@app.route('/supplier/<int:supplier_id>/pdf')
@login_required
@manager_required
def download_supplier_pdf(supplier_id):
    """Download supplier record as PDF"""
    supplier = Supplier.query.get_or_404(supplier_id)
    
    # Calculate outstanding
    outstanding = supplier.grand_total - supplier.amount_paid
    
    # Get payments
    payments = SupplierPayment.query.filter_by(
        supplier_id=supplier_id
    ).order_by(SupplierPayment.payment_date.desc()).all()
    
    # Render HTML template
    html_content = render_template('suppliers/pdf_template.html', 
                                  supplier=supplier,
                                  outstanding=outstanding,
                                  payments=payments)
    
    # Convert to PDF using xhtml2pdf
    pdf_buffer = BytesIO()
    pisa_status = pisa.CreatePDF(html_content, dest=pdf_buffer)
    
    if pisa_status.err:
        flash('Error generating PDF', 'danger')
        return redirect(url_for('view_supplier', supplier_id=supplier_id))
    
    pdf_buffer.seek(0)
    pdf = pdf_buffer.read()
    
    # Create response
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=Supplier_{supplier.supplier_name.replace(" ", "_")}_{supplier.transaction_date.isoformat()}.pdf'
    
    log_audit('download_supplier_pdf', 'supplier', supplier_id)
    
    return response

# ============================================================================
# ROUTES - PACKAGING MANAGEMENT
# ============================================================================

@app.route('/packaging')
@login_required
def packaging_list():
    """List all packaging records"""
    # Allow workers to VIEW, but not managers/directors/admins can create/delete
    if current_user.role not in ['worker', 'manager', 'director', 'admin']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get date filter parameters
    filter_type = request.args.get('filter', 'month')  # Default to current month
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    # Calculate date range based on filter type
    today = date.today()
    
    if filter_type == 'today':
        start_date = today
        end_date = today
    elif filter_type == 'week':
        # Current week (Monday to Sunday)
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif filter_type == 'month':
        # Current month
        start_date = date(today.year, today.month, 1)
        from calendar import monthrange
        _, last_day = monthrange(today.year, today.month)
        end_date = date(today.year, today.month, last_day)
    elif filter_type == 'custom':
        # Custom date range
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else today
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else today
        except:
            start_date = today
            end_date = today
    elif filter_type == 'all':
        # All records (no date filter)
        start_date = None
        end_date = None
    else:
        # Default to current month
        start_date = date(today.year, today.month, 1)
        from calendar import monthrange
        _, last_day = monthrange(today.year, today.month)
        end_date = date(today.year, today.month, last_day)
    
    # Query records with date filter
    query = PackagingRecord.query
    
    if start_date and end_date:
        query = query.filter(
            PackagingRecord.date_packaged >= start_date,
            PackagingRecord.date_packaged <= end_date
        )
    
    records = query.order_by(PackagingRecord.date_packaged.desc()).all()
    
    # Calculate summary for filtered records
    summary = {
        'maize_flour_1kg': 0,
        'maize_flour_2kg': 0,
        'maize_germ': 0,
        'animal_feeds': 0
    }
    
    for record in records:
        if record.product_type in summary:
            if 'maize_flour' in record.product_type:
                summary[record.product_type] += record.quantity
            else:
                summary[record.product_type] += record.total_weight_kg
    
    return render_template('packaging/list.html', 
                         records=records, 
                         summary=summary,
                         filter_type=filter_type,
                         start_date=start_date,
                         end_date=end_date,
                         today=today)

@app.route('/packaging/create', methods=['GET', 'POST'])
@login_required
@manager_required
def create_packaging():
    """Create new packaging record (Manager only)"""
    if request.method == 'POST':
        batch_number = request.form.get('batch_number')
        packaged_by_id = request.form.get('packaged_by_id')
        product_type = request.form.get('product_type')
        date_packaged = datetime.strptime(request.form.get('date_packaged'), '%Y-%m-%d').date()
        
        # Calculate based on product type
        if product_type in ['maize_flour_1kg', 'maize_flour_2kg']:
            # Manager enters BALES, we calculate units
            bales = float(request.form.get('bales'))
            
            if product_type == 'maize_flour_1kg':
                quantity = bales * 24  # 24 units per bale
                total_weight_kg = quantity * 1  # 1kg per unit
            else:  # 2kg
                quantity = bales * 12  # 12 units per bale
                total_weight_kg = quantity * 2  # 2kg per unit
        else:
            # Maize Germ and Animal Feeds - FLEXIBLE INPUT
            input_type = request.form.get('input_type')  # 'bags' or 'weight'
            
            if input_type == 'bags':
                # Manager entered number of bags
                bags = float(request.form.get('bags'))
                quantity = bags  # Store number of bags
                total_weight_kg = bags * 50  # 50kg per bag
            else:  # input_type == 'weight'
                # Manager entered total weight in KG
                weight_kg = float(request.form.get('weight_kg'))
                total_weight_kg = weight_kg
                quantity = weight_kg / 50  # Calculate number of bags
        
        record = PackagingRecord(
            date_packaged=date_packaged,
            batch_number=batch_number,
            packaged_by_id=packaged_by_id,
            product_type=product_type,
            quantity=quantity,
            total_weight_kg=total_weight_kg,
            created_by_id=current_user.id
        )
        
        db.session.add(record)
        db.session.commit()
        
        # Update processed inventory
        update_processed_inventory()
        
        # Deduct from unprocessed inventory
        if 'maize_flour' in product_type:
            update_unprocessed_inventory('Maize Grains')
        elif 'maize_germ' in product_type:
            update_unprocessed_inventory('Maize Germ')
        elif 'animal_feeds' in product_type:
            update_unprocessed_inventory('Animal Feeds')
        
        log_audit('create_packaging', 'packaging_record', record.id)
        
        flash('Packaging record created successfully', 'success')
        return redirect(url_for('packaging_list'))
    
    # Get workers for dropdown
    workers = User.query.filter_by(role='worker').all()
    return render_template('packaging/create.html', workers=workers)

@app.route('/packaging/<int:record_id>/delete', methods=['POST'])
@login_required
@manager_required
def delete_packaging(record_id):
    """Delete packaging record (Manager only)"""
    record = PackagingRecord.query.get_or_404(record_id)
    
    # Store info for flash message
    record_info = f"{record.batch_number} - {record.product_type}"
    
    log_audit('delete_packaging', 'packaging_record', record.id, old_value=record_info)
    
    # Delete record
    db.session.delete(record)
    db.session.commit()
    
    # Update inventories
    update_processed_inventory()
    if 'maize_flour' in record.product_type:
        update_unprocessed_inventory('Maize Grains')
    elif 'maize_germ' in record.product_type:
        update_unprocessed_inventory('Maize Germ')
    elif 'animal_feeds' in record.product_type:
        update_unprocessed_inventory('Animal Feeds')
    
    flash(f'Packaging record deleted: {record_info}', 'success')
    return jsonify({'success': True, 'message': 'Packaging record deleted successfully'})


# ============================================================================
# ROUTES - INTERNAL REQUISITION
# ============================================================================

@app.route('/requisitions')
@login_required
def requisitions_list():
    """List requisitions"""
    if current_user.role == 'worker':
        requisitions = InternalRequisition.query.filter_by(requested_by_id=current_user.id).order_by(InternalRequisition.date_requested.desc()).all()
    else:
        requisitions = InternalRequisition.query.order_by(InternalRequisition.date_requested.desc()).all()
    
    return render_template('requisitions/list.html', requisitions=requisitions)

@app.route('/requisition/create', methods=['GET', 'POST'])
@login_required
def create_requisition():
    """Create new internal requisition (Worker)"""
    # ENFORCE CLOCK-IN FOR WORKERS
    if current_user.role == 'worker':
        today = date.today()
        clock_record = WorkerClock.query.filter_by(
            user_id=current_user.id,
            date=today,
            clock_out_time=None
        ).first()
        
        if not clock_record:
            flash('You must clock in before creating requisitions', 'warning')
            return redirect(url_for('worker_dashboard'))
    
    if request.method == 'POST':
        # Generate requisition number
        last_req = InternalRequisition.query.order_by(InternalRequisition.id.desc()).first()
        if last_req:
            try:
                last_num = int(last_req.requisition_number.split('-')[1])
                req_number = f'REQ-{last_num + 1:05d}'
            except:
                req_number = 'REQ-00001'
        else:
            req_number = 'REQ-00001'
        
        requisition = InternalRequisition(
            requisition_number=req_number,
            requested_by_id=current_user.id,
            date_requested=datetime.strptime(request.form.get('date_requested'), '%Y-%m-%d').date(),
            department=request.form.get('department'),
            purpose=request.form.get('purpose')
        )
        
        db.session.add(requisition)
        db.session.flush()
        
        # Add items
        item_descriptions = request.form.getlist('item_description[]')
        item_quantities = request.form.getlist('quantity[]')
        item_reasons = request.form.getlist('reason[]')
        
        total_items = 0
        
        for desc, qty, reason in zip(item_descriptions, item_quantities, item_reasons):
            if desc and qty:
                quantity = float(qty)
                
                item = RequisitionItem(
                    requisition_id=requisition.id,
                    item_description=desc,
                    quantity=quantity,
                    reason=reason
                )
                db.session.add(item)
                total_items += 1
        
        requisition.total_items = total_items
        
        db.session.commit()
        
        log_audit('create_requisition', 'internal_requisition', requisition.id)
        
        flash('Internal requisition created successfully', 'success')
        return redirect(url_for('requisitions_list'))
    
    return render_template('requisitions/create.html')



@app.route('/requisition/<int:requisition_id>')
@login_required
def view_requisition(requisition_id):
    """View requisition details"""
    requisition = InternalRequisition.query.get_or_404(requisition_id)
    
    # Check permissions
    if current_user.role == 'worker' and requisition.requested_by_id != current_user.id:
        flash('Access denied', 'danger')
        return redirect(url_for('requisitions_list'))
    
    return render_template('requisitions/view.html', requisition=requisition)


@app.route('/requisition/<int:requisition_id>/approve', methods=['POST'])
@login_required
@manager_required
def approve_requisition(requisition_id):
    """Approve internal requisition (Manager)"""
    requisition = InternalRequisition.query.get_or_404(requisition_id)
    
    requisition.status = 'approved'
    requisition.approved_by_id = current_user.id
    requisition.approval_date = date.today()
    
    db.session.commit()
    
    log_audit('approve_requisition', 'internal_requisition', requisition.id)
    
    flash('Requisition approved. You can now create a dispatch.', 'success')
    return redirect(url_for('view_requisition', requisition_id=requisition.id))


@app.route('/requisition/<int:requisition_id>/reject', methods=['POST'])
@login_required
@manager_required
def reject_requisition(requisition_id):
    """Reject internal requisition (Manager)"""
    requisition = InternalRequisition.query.get_or_404(requisition_id)
    rejection_reason = request.form.get('reason')
    
    requisition.status = 'rejected'
    requisition.approved_by_id = current_user.id
    requisition.approval_date = date.today()
    requisition.rejection_reason = rejection_reason
    
    db.session.commit()
    
    log_audit('reject_requisition', 'internal_requisition', requisition.id)
    
    flash('Requisition rejected', 'success')
    return redirect(url_for('view_requisition', requisition_id=requisition.id))


# ============================================================================
# ROUTES - DISPATCH MANAGEMENT
# ============================================================================

@app.route('/dispatches')
@login_required
def dispatches_list():
    """List all dispatches"""
    if current_user.role == 'worker':
        # Worker sees dispatches for their requisitions
        dispatches = Dispatch.query.join(InternalRequisition).filter(
            InternalRequisition.requested_by_id == current_user.id
        ).order_by(Dispatch.dispatch_date.desc()).all()
    else:
        dispatches = Dispatch.query.order_by(Dispatch.dispatch_date.desc()).all()
    
    return render_template('dispatches/list.html', dispatches=dispatches)


@app.route('/dispatch/create', methods=['GET', 'POST'])
@login_required
@manager_required
def create_dispatch():
    """Create new dispatch (Manager)"""
    if request.method == 'POST':
        # Generate tracking number
        last_dispatch = Dispatch.query.order_by(Dispatch.id.desc()).first()
        if last_dispatch:
            last_num = int(last_dispatch.tracking_number.split('-')[1])
            tracking_number = f'DSP-{last_num + 1:05d}'
        else:
            tracking_number = 'DSP-00001'
        
        dispatch = Dispatch(
            tracking_number=tracking_number,
            batch_number=request.form.get('batch_number'),
            dispatch_date=datetime.strptime(request.form.get('dispatch_date'), '%Y-%m-%d').date(),
            dispatch_type=request.form.get('dispatch_type', 'sale'),
            driver_name=request.form.get('driver_name'),
            driver_phone=request.form.get('driver_phone'),
            vehicle_registration=request.form.get('vehicle_registration'),
            reason=request.form.get('reason'),
            created_by_id=current_user.id,
            requisition_id=request.form.get('requisition_id') if request.form.get('requisition_id') else None
        )
        
        db.session.add(dispatch)
        db.session.flush()
        
        # Add items
        item_descriptions = request.form.getlist('item_description[]')
        item_quantities = request.form.getlist('quantity[]')
        item_weights = request.form.getlist('unit_weight[]')
        
        total_units = 0
        total_weight = 0
        
        for desc, qty, weight in zip(item_descriptions, item_quantities, item_weights):
            if desc and qty and weight:
                quantity = float(qty)
                unit_weight = float(weight)
                item_total_weight = quantity * unit_weight
                
                item = DispatchItem(
                    dispatch_id=dispatch.id,
                    item_description=desc,
                    quantity=quantity,
                    unit_weight_kg=unit_weight,
                    total_units=quantity,
                    total_weight_kg=item_total_weight
                )
                db.session.add(item)
                
                total_units += quantity
                total_weight += item_total_weight
        
        dispatch.total_units = total_units
        dispatch.total_weight_kg = total_weight
        
        # Auto-approve if created by manager
        dispatch.status = 'approved'
        dispatch.authorized_by_id = current_user.id
        dispatch.authorization_date = date.today()
        
        db.session.commit()
        
        # Update processed inventory (reduce stock)
        update_processed_inventory()
        
        log_audit('create_dispatch', 'dispatch', dispatch.id)
        
        flash('Dispatch created and approved successfully', 'success')
        return redirect(url_for('dispatches_list'))
    
    # Get approved requisitions for dropdown
    requisitions = InternalRequisition.query.filter_by(status='approved').all()
    
    return render_template('dispatches/create.html', requisitions=requisitions)


@app.route('/dispatch/<int:dispatch_id>')
@login_required
def view_dispatch(dispatch_id):
    """View dispatch details"""
    dispatch = Dispatch.query.get_or_404(dispatch_id)
    return render_template('dispatches/view.html', dispatch=dispatch)


# ============================================================================
# ROUTES - INVOICE MANAGEMENT
# ============================================================================
@app.route('/invoices')
@login_required
def invoices_list():
    """List all invoices with role-based filtering"""
    if current_user.role == 'worker':
        # Workers see ONLY their own invoices
        invoices = Invoice.query.filter_by(created_by_id=current_user.id).order_by(Invoice.invoice_date.desc()).all()
    else:
        # Manager, Director, Admin see ALL invoices
        invoices = Invoice.query.order_by(Invoice.invoice_date.desc()).all()
    
    return render_template('invoices/list.html', invoices=invoices)


@app.route('/invoice/create', methods=['GET', 'POST'])
@login_required
def create_invoice():
    """Create new invoice - Worker/Manager/Admin only"""
    # Block Director
    if current_user.role == 'director':
        flash('Directors cannot create invoices', 'warning')
        return redirect(url_for('director_dashboard'))
    
    # ENFORCE CLOCK-IN FOR WORKERS
    if current_user.role == 'worker':
        today = date.today()
        clock_record = WorkerClock.query.filter_by(
            user_id=current_user.id,
            date=today,
            clock_out_time=None
        ).first()
        
        if not clock_record:
            flash('You must clock in before creating invoices', 'warning')
            return redirect(url_for('worker_dashboard'))
    
    if request.method == 'POST':
        # Generate or use manual invoice number
        is_auto = request.form.get('is_auto_generated') == 'true'
        
        if is_auto:
            last_invoice = Invoice.query.order_by(Invoice.id.desc()).first()
            if last_invoice:
                try:
                    last_num = int(last_invoice.invoice_number.split('-')[1])
                    invoice_number = f'INV-{last_num + 1:05d}'
                except:
                    invoice_number = 'INV-00001'
            else:
                invoice_number = 'INV-00001'
        else:
            invoice_number = request.form.get('invoice_number')
            
            # Check for duplicates
            existing = Invoice.query.filter_by(invoice_number=invoice_number).first()
            if existing:
                return jsonify({'success': False, 'message': 'Invoice number already exists'}), 400
        
        # Create invoice
        invoice = Invoice(
            invoice_number=invoice_number,
            is_auto_generated=is_auto,
            invoice_date=datetime.strptime(request.form.get('invoice_date'), '%Y-%m-%d').date(),
            customer_name=request.form.get('customer_name'),
            phone_number=request.form.get('phone_number'),
            email=request.form.get('email'),
            city=request.form.get('city'),
            state=request.form.get('state'),
            zip_code=request.form.get('zip_code'),
            kra_pin=request.form.get('kra_pin'),
            reference_code=request.form.get('reference_code'),
            subtotal=float(request.form.get('subtotal', 0)),
            tax=float(request.form.get('tax', 0)),
            grand_total=float(request.form.get('grand_total', 0)),
            payment_status='unpaid',
            created_by_id=current_user.id
        )
        
        db.session.add(invoice)
        db.session.flush()
        
        # Add items
        item_descriptions = request.form.getlist('item_description[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        
        for desc, qty, price in zip(item_descriptions, quantities, unit_prices):
            if desc and qty and price:
                item = InvoiceItem(
                    invoice_id=invoice.id,
                    item_description=desc,
                    quantity=float(qty),
                    unit_price=float(price),
                    total_amount=float(qty) * float(price)
                )
                db.session.add(item)
        
        db.session.commit()
        
        log_audit('create_invoice', 'invoice', invoice.id)
        
        flash('Invoice created successfully', 'success')
        return redirect(url_for('view_invoice', invoice_id=invoice.id))
    
    return render_template('invoices/create.html')


@app.route('/invoice/<int:invoice_id>')
@login_required
def view_invoice(invoice_id):
    """View invoice details - All roles can view"""
    invoice = Invoice.query.get_or_404(invoice_id)
    
    # Workers can only view their own invoices
    if current_user.role == 'worker' and invoice.created_by_id != current_user.id:
        flash('You can only view your own invoices', 'warning')
        return redirect(url_for('invoices_list'))
    
    # Get payment history
    payments = Payment.query.filter_by(invoice_id=invoice_id).order_by(Payment.payment_date.desc()).all()
    
    return render_template('invoices/view.html', invoice=invoice, payments=payments)


@app.route('/invoice/<int:invoice_id>/add-payment', methods=['POST'])
@login_required
def add_invoice_payment(invoice_id):
    """Add payment to invoice.
    - Workers: Can add initial payment to their own invoices only (must be clocked in)
    - Managers/Admin: Can update any invoice payment
    - Directors: CANNOT add or record any payments (read-only role)
    """
    invoice = Invoice.query.get_or_404(invoice_id)
 
    # ── DIRECTOR BLOCK ──────────────────────────────────────────────────────
    if current_user.role == 'director':
        return jsonify({
            'success': False,
            'message': 'Directors cannot record payments. Please contact the manager.'
        }), 403
 
    # ── WORKER RESTRICTIONS ─────────────────────────────────────────────────
    if current_user.role == 'worker':
        if invoice.created_by_id != current_user.id:
            return jsonify({'success': False, 'message': 'You can only add payments to your own invoices'}), 403
        
        today = date.today()
        clock_record = WorkerClock.query.filter_by(
            user_id=current_user.id,
            date=today,
            clock_out_time=None
        ).first()
        
        if not clock_record:
            return jsonify({'success': False, 'message': 'You must clock in to add payments'}), 403
        
        existing_payments = Payment.query.filter_by(invoice_id=invoice_id).count()
        if existing_payments > 0:
            return jsonify({'success': False, 'message': 'Only managers can update existing payments'}), 403
 
    # ── VALIDATE AMOUNT ─────────────────────────────────────────────────────
    amount = float(request.form.get('amount', 0))
    remaining_balance = invoice.grand_total - invoice.amount_paid
    
    if amount <= 0:
        return jsonify({'success': False, 'message': 'Amount must be greater than zero'}), 400
    
    if amount > remaining_balance:
        return jsonify({'success': False, 'message': f'Amount exceeds remaining balance of KSh {remaining_balance:,.2f}'}), 400
    
    payment = Payment(
        invoice_id=invoice_id,
        amount=amount,
        payment_date=datetime.strptime(request.form.get('payment_date'), '%Y-%m-%d').date(),
        payment_method=request.form.get('payment_method'),
        reference_number=request.form.get('reference_number'),
        notes=request.form.get('notes'),
        recorded_by_id=current_user.id
    )
    
    db.session.add(payment)
    
    invoice.amount_paid += amount
    invoice.updated_by_id = current_user.id
    invoice.updated_at = datetime.now()
    
    if invoice.amount_paid >= invoice.grand_total:
        invoice.payment_status = 'paid'
    elif invoice.amount_paid > 0:
        invoice.payment_status = 'partial'
    
    db.session.commit()
    
    log_audit('add_payment', 'invoice', invoice_id)
    
    return jsonify({
        'success': True,
        'message': 'Payment added successfully',
        'new_status': invoice.payment_status,
        'amount_paid': invoice.amount_paid,
        'remaining_balance': invoice.grand_total - invoice.amount_paid
    })

@app.route('/invoice/<int:invoice_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_invoice(invoice_id):
    """Edit invoice - Manager/Admin only"""
    if current_user.role not in ['manager', 'admin']:
        flash('Only managers can edit invoices', 'warning')
        return redirect(url_for('invoices_list'))
    
    invoice = Invoice.query.get_or_404(invoice_id)
    
    if request.method == 'POST':
        # Update basic info
        invoice.customer_name = request.form.get('customer_name')
        invoice.phone_number = request.form.get('phone_number')
        invoice.email = request.form.get('email')
        invoice.city = request.form.get('city')
        invoice.state = request.form.get('state')
        invoice.zip_code = request.form.get('zip_code')
        invoice.kra_pin = request.form.get('kra_pin')
        invoice.reference_code = request.form.get('reference_code')
        invoice.updated_by_id = current_user.id
        invoice.updated_at = datetime.now()
        
        db.session.commit()
        
        log_audit('edit_invoice', 'invoice', invoice_id)
        
        flash('Invoice updated successfully', 'success')
        return redirect(url_for('view_invoice', invoice_id=invoice_id))
    
    return render_template('invoices/edit.html', invoice=invoice)


@app.route('/invoice/<int:invoice_id>/pdf')
@login_required
def download_invoice_pdf(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    
    if current_user.role == 'worker' and invoice.created_by_id != current_user.id:
        flash('You can only download your own invoices', 'warning')
        return redirect(url_for('invoices_list'))
    
    html_content = render_template('invoices/pdf_template.html', invoice=invoice)
    
    pdf_buffer = BytesIO()
    pisa_status = pisa.CreatePDF(html_content, dest=pdf_buffer)
    
    if pisa_status.err:
        flash('Error generating PDF', 'danger')
        return redirect(url_for('view_invoice', invoice_id=invoice_id))
    
    pdf_buffer.seek(0)
    response = make_response(pdf_buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=Invoice_{invoice.invoice_number}.pdf'
    
    log_audit('download_invoice_pdf', 'invoice', invoice_id)
    return response


@app.route('/admin/user/<int:user_id>/details')
@login_required
@admin_required
def admin_user_details(user_id):
    user = User.query.get_or_404(user_id)
    return jsonify({
        'id': user.id,
        'name': user.name,
        'username': user.username,
        'role': user.role,
        'is_blocked': user.is_blocked,
        'created_at': user.created_at.strftime('%Y-%m-%d'),
        'last_login': user.last_login.strftime('%Y-%m-%d %H:%M') if user.last_login else 'Never',
        'must_change_password': user.must_change_password
    })

@app.route('/customer/<customer_name>/folder/pdf')
@login_required
def download_customer_folder_pdf(customer_name):
    """Download complete customer folder as PDF - All roles"""
    # TODO: Generate comprehensive PDF with all invoices and payments
    flash('Customer folder PDF generation coming soon!', 'info')
    return redirect(url_for('customer_folder', customer_name=customer_name))


@app.route('/invoice/check-number/<invoice_number>')
@login_required
def check_invoice_number(invoice_number):
    """Check if invoice number already exists (AJAX endpoint)"""
    existing = Invoice.query.filter_by(invoice_number=invoice_number).first()
    
    if existing:
        return jsonify({
            'exists': True,
            'message': f'Invoice number {invoice_number} already exists',
            'invoice_id': existing.id,
            'customer': existing.customer_name
        })
    
    return jsonify({'exists': False})

# ============================================================================
# ROUTES - CUSTOMER FOLDERS
# ============================================================================

@app.route('/customers')
@login_required
def customers_list():
    """List all customers with invoice folders"""
    # Get all unique customers
    customers_data = {}
    
    invoices = Invoice.query.order_by(Invoice.invoice_date.desc()).all()
    
    for invoice in invoices:
        customer = invoice.customer_name
        if customer not in customers_data:
            customers_data[customer] = {
                'name': customer,
                'phone': invoice.phone_number,
                'email': invoice.email,
                'city': invoice.city,
                'kra_pin': invoice.kra_pin,
                'total_invoices': 0,
                'total_invoiced': 0,
                'total_paid': 0,
                'outstanding': 0,
                'last_invoice_date': None
            }
        
        customers_data[customer]['total_invoices'] += 1
        customers_data[customer]['total_invoiced'] += invoice.grand_total
        customers_data[customer]['total_paid'] += invoice.amount_paid
        customers_data[customer]['outstanding'] += (invoice.grand_total - invoice.amount_paid)
        
        if not customers_data[customer]['last_invoice_date'] or invoice.invoice_date > customers_data[customer]['last_invoice_date']:
            customers_data[customer]['last_invoice_date'] = invoice.invoice_date
    
    # Convert to list and sort
    customers_list = sorted(customers_data.values(), key=lambda x: x['last_invoice_date'] if x['last_invoice_date'] else date.min, reverse=True)
    
    return render_template('customers/list.html', customers=customers_list)


@app.route('/customer/<customer_name>/folder')
@login_required
def customer_folder(customer_name):
    """View customer's complete invoice folder"""
    # Get all invoices for this customer
    invoices = Invoice.query.filter_by(customer_name=customer_name).order_by(Invoice.invoice_date.desc()).all()
    
    if not invoices:
        flash('Customer not found', 'danger')
        return redirect(url_for('customers_list'))
    
    # Calculate totals
    total_invoiced = sum(inv.grand_total for inv in invoices)
    total_paid = sum(inv.amount_paid for inv in invoices)
    outstanding = total_invoiced - total_paid
    
    # Get all payments across all invoices
    all_payments = []
    for invoice in invoices:
        for payment in invoice.payments:
            all_payments.append({
                'payment': payment,
                'invoice': invoice
            })
    
    all_payments.sort(key=lambda x: x['payment'].payment_date, reverse=True)
    
    # Customer info from most recent invoice
    customer_info = {
        'name': customer_name,
        'phone': invoices[0].phone_number,
        'email': invoices[0].email,
        'city': invoices[0].city,
        'state': invoices[0].state,
        'kra_pin': invoices[0].kra_pin
    }
    
    return render_template('customers/folder.html',
                         customer=customer_info,
                         invoices=invoices,
                         total_invoiced=total_invoiced,
                         total_paid=total_paid,
                         outstanding=outstanding,
                         all_payments=all_payments)


# ============================================================================
# ROUTES - SUPPLIER FOLDERS
# ============================================================================

@app.route('/suppliers/folders')
@login_required
def suppliers_folders():
    """List all suppliers with transaction folders - Manager, Admin, and Director can view"""
    if current_user.role not in ['manager', 'admin', 'director']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
 
    suppliers_data = {}
    
    suppliers = Supplier.query.order_by(Supplier.transaction_date.desc()).all()
    
    for supplier in suppliers:
        name = supplier.supplier_name
        if name not in suppliers_data:
            suppliers_data[name] = {
                'name': name,
                'phone': supplier.phone_number,
                'city': supplier.city,
                'total_transactions': 0,
                'total_purchased': 0,
                'total_paid': 0,
                'outstanding': 0,
                'last_transaction_date': None
            }
        
        suppliers_data[name]['total_transactions'] += 1
        suppliers_data[name]['total_purchased'] += supplier.grand_total
        suppliers_data[name]['total_paid'] += (supplier.amount_paid if supplier.amount_paid else 0)
        suppliers_data[name]['outstanding'] = suppliers_data[name]['total_purchased'] - suppliers_data[name]['total_paid']
        
        if not suppliers_data[name]['last_transaction_date'] or supplier.transaction_date > suppliers_data[name]['last_transaction_date']:
            suppliers_data[name]['last_transaction_date'] = supplier.transaction_date
    
    suppliers_list = sorted(suppliers_data.values(), key=lambda x: x['last_transaction_date'] if x['last_transaction_date'] else date.min, reverse=True)
    
    return render_template('suppliers/folders.html', suppliers=suppliers_list)


@app.route('/supplier/<supplier_name>/folder')
@login_required
def supplier_folder(supplier_name):
    """View supplier's complete transaction folder - Manager, Admin, and Director can view"""
    if current_user.role not in ['manager', 'admin', 'director']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
 
    suppliers = Supplier.query.filter_by(supplier_name=supplier_name).order_by(Supplier.transaction_date.desc()).all()
    
    if not suppliers:
        flash('Supplier not found', 'danger')
        return redirect(url_for('suppliers_folders'))
    
    total_purchased = sum(sup.grand_total for sup in suppliers)
    total_paid = sum(sup.amount_paid for sup in suppliers if sup.amount_paid)
    outstanding = total_purchased - total_paid
 
    supplier_info = {
        'name': supplier_name,
        'phone': suppliers[0].phone_number,
        'city': suppliers[0].city,
        'address': suppliers[0].address
    }
    
    return render_template('suppliers/folder.html',
                         supplier=supplier_info,
                         suppliers=suppliers,
                         total_purchased=total_purchased,
                         total_paid=total_paid,
                         outstanding=outstanding)







# ============================================================================
# ROUTES - REPORTS & EXPORT
# ============================================================================

@app.route('/export/csv')
@login_required
def export_csv():
    """Export data as CSV"""
    from io import StringIO
    import csv
    
    # Get parameters
    date_str = request.args.get('date')
    sheet_type = request.args.get('type', 'all')
    
    # Parse date
    if date_str:
        try:
            export_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except:
            export_date = date.today()
    else:
        export_date = date.today()
    
    # Get sheets based on role
    if current_user.role == 'worker':
        query = IntakeSheet.query.filter_by(
            worker_id=current_user.id,
            sheet_date=export_date
        )
    else:
        query = IntakeSheet.query.filter_by(sheet_date=export_date)
    
    # Apply sheet type filter
    if sheet_type != 'all':
        query = query.filter_by(sheet_type=sheet_type)
    
    sheets = query.all()
    
    # Create CSV
    output = StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        'Date', 
        'Sheet Type', 
        'Worker', 
        'Product Type', 
        'Sheet Status', 
        'Cage Number', 
        'Weight (KG)', 
        'Timestamp', 
        'Authorization Status',
        'Destination',
        'Purpose'
    ])
    
    # Data
    for sheet in sheets:
        for entry in sheet.entries:
            writer.writerow([
                sheet.sheet_date.isoformat(),
                sheet.sheet_type.upper(),
                sheet.worker.name,
                sheet.product_type,
                sheet.status,
                entry.cage_number,
                entry.weight,
                entry.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                sheet.authorization_status if sheet.sheet_type == 'outstock' else 'N/A',
                sheet.destination if sheet.destination else 'N/A',
                sheet.purpose if sheet.purpose else 'N/A'
            ])
    
    # Return CSV
    from flask import Response
    output.seek(0)
    
    # Create filename with date and type
    filename = f'dunedeck_sheets_{export_date.isoformat()}'
    if sheet_type != 'all':
        filename += f'_{sheet_type}'
    filename += '.csv'
    
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@app.route('/reports/daily')
@login_required
def daily_report():
    """Daily report for managers and directors"""
    if current_user.role not in ['manager', 'director', 'admin']:
        flash('Access denied. Manager or Director privileges required.', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get selected date
    date_str = request.args.get('date', date.today().isoformat())
    try:
        report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        report_date = date.today()
    
    # Get all sheets for this date
    sheets = IntakeSheet.query.filter_by(sheet_date=report_date).all()
    
    # Calculate statistics by sheet type
    stats = {
        'daily': {'count': 0, 'cages': 0, 'weight': 0, 'bags': 0},
        'received': {'count': 0, 'cages': 0, 'weight': 0, 'bags': 0},
        'outstock': {'count': 0, 'cages': 0, 'weight': 0, 'bags': 0},
        'lost': {'count': 0, 'cages': 0, 'weight': 0, 'bags': 0}
    }
    
    # Calculate by product
    product_stats = {}
    
    for sheet in sheets:
        sheet_type = sheet.sheet_type
        product = sheet.product_type
        
        # Get bag weight
        if product in ['Maize Grains', 'Maize Germ']:
            bag_weight = 90
        else:
            bag_weight = 50
        
        # Calculate totals
        total_cages = len(sheet.entries)
        total_weight = sum(entry.weight for entry in sheet.entries)
        total_bags = total_weight / bag_weight if bag_weight > 0 else 0
        
        # Update sheet type stats
        stats[sheet_type]['count'] += 1
        stats[sheet_type]['cages'] += total_cages
        stats[sheet_type]['weight'] += total_weight
        stats[sheet_type]['bags'] += total_bags
        
        # Update product stats
        if product not in product_stats:
            product_stats[product] = {'received': 0, 'outstock': 0, 'lost': 0, 'net': 0}
        
        if sheet_type == 'received':
            product_stats[product]['received'] += total_bags
        elif sheet_type == 'outstock':
            product_stats[product]['outstock'] += total_bags
        elif sheet_type == 'lost':
            product_stats[product]['lost'] += total_bags
        
        product_stats[product]['net'] = (
            product_stats[product]['received'] - 
            product_stats[product]['outstock'] - 
            product_stats[product]['lost']
        )
    
    # Get inventory summaries
    unprocessed_inventories = UnprocessedInventory.query.all()
    processed_inventories = ProcessedInventory.query.all()
    
    # Prepare summary cards
    summary_cards = [
        {'title': 'DAILY WEIGHT SHEETS', 'value': stats['daily']['count'], 
         'subtitle': f"{stats['daily']['bags']:.1f} bags", 'color': 'primary'},
        {'title': 'RECEIVED BATCHES', 'value': stats['received']['count'], 
         'subtitle': f"{stats['received']['bags']:.1f} bags", 'color': 'success'},
        {'title': 'OUT STOCK', 'value': stats['outstock']['count'], 
         'subtitle': f"{stats['outstock']['bags']:.1f} bags", 'color': 'danger'},
        {'title': 'LOST/DAMAGED', 'value': stats['lost']['count'], 
         'subtitle': f"{stats['lost']['bags']:.1f} bags", 'color': 'warning'}
    ]
    
    return render_template('reports/universal_report.html',
                         report_type='daily',
                         report_title='Daily Report',
                         report_subtitle=report_date.strftime('%A, %B %d, %Y'),
                         report_icon='calendar-day',
                         report_date=report_date,
                         summary_cards=summary_cards,
                         product_stats=product_stats,
                         unprocessed_inventories=unprocessed_inventories,
                         processed_inventories=processed_inventories,
                         export_url=url_for('export_csv', date=report_date.strftime('%Y-%m-%d')))


@app.route('/reports/weekly')
@login_required
def weekly_report():
    """Weekly report for managers and directors"""
    if current_user.role not in ['manager', 'director', 'admin']:
        flash('Access denied. Manager or Director privileges required.', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get week (default to current week)
    date_str = request.args.get('date', date.today().isoformat())
    try:
        end_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        end_date = date.today()
    
    start_date = end_date - timedelta(days=6)
    
    # Get all sheets for this week
    sheets = IntakeSheet.query.filter(
        IntakeSheet.sheet_date >= start_date,
        IntakeSheet.sheet_date <= end_date
    ).all()
    
    # Calculate daily breakdown
    daily_breakdown = {}
    current_date = start_date
    while current_date <= end_date:
        daily_breakdown[current_date] = {'received': 0, 'outstock': 0, 'lost': 0, 'daily': 0}
        current_date += timedelta(days=1)
    
    # Calculate product breakdown
    product_breakdown = {}
    total_received = 0
    total_outstock = 0
    total_lost = 0
    
    for sheet in sheets:
        sheet_date = sheet.sheet_date
        product = sheet.product_type
        
        # Get bag weight
        if product in ['Maize Grains', 'Maize Germ']:
            bag_weight = 90
        else:
            bag_weight = 50
        
        # Calculate totals
        total_weight = sum(entry.weight for entry in sheet.entries)
        total_bags = total_weight / bag_weight if bag_weight > 0 else 0
        
        # Update daily breakdown
        if sheet.sheet_type in daily_breakdown[sheet_date]:
            daily_breakdown[sheet_date][sheet.sheet_type] += total_bags
        
        # Update totals
        if sheet.sheet_type == 'received':
            total_received += total_bags
        elif sheet.sheet_type == 'outstock':
            total_outstock += total_bags
        elif sheet.sheet_type == 'lost':
            total_lost += total_bags
        
        # Update product breakdown
        if product not in product_breakdown:
            product_breakdown[product] = {'received': 0, 'outstock': 0, 'lost': 0}
        
        if sheet.sheet_type == 'received':
            product_breakdown[product]['received'] += total_bags
        elif sheet.sheet_type == 'outstock':
            product_breakdown[product]['outstock'] += total_bags
        elif sheet.sheet_type == 'lost':
            product_breakdown[product]['lost'] += total_bags
    
    # Get inventory summaries
    unprocessed_inventories = UnprocessedInventory.query.all()
    processed_inventories = ProcessedInventory.query.all()
    
    # Prepare summary cards
    summary_cards = [
        {'title': 'TOTAL RECEIVED', 'value': f"{total_received:.1f}", 
         'subtitle': 'bags this week', 'color': 'success'},
        {'title': 'TOTAL OUT STOCK', 'value': f"{total_outstock:.1f}", 
         'subtitle': 'bags this week', 'color': 'danger'},
        {'title': 'TOTAL LOST', 'value': f"{total_lost:.1f}", 
         'subtitle': 'bags this week', 'color': 'warning'},
        {'title': 'NET CHANGE', 'value': f"{total_received - total_outstock - total_lost:+.1f}", 
         'subtitle': 'bags this week', 'color': 'info'}
    ]
    
    return render_template('reports/universal_report.html',
                         report_type='weekly',
                         report_title='Weekly Report',
                         report_subtitle=f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')}",
                         report_icon='calendar-week',
                         end_date=end_date,
                         summary_cards=summary_cards,
                         daily_breakdown=daily_breakdown,
                         product_breakdown=product_breakdown,
                         unprocessed_inventories=unprocessed_inventories,
                         processed_inventories=processed_inventories,
                         export_url=url_for('export_csv', date=end_date.strftime('%Y-%m-%d')))


@app.route('/reports/monthly')
@login_required
def monthly_report():
    """Monthly report for managers and directors"""
    if current_user.role not in ['manager', 'director', 'admin']:
        flash('Access denied. Manager or Director privileges required.', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get month (default to current month)
    year = int(request.args.get('year', date.today().year))
    month = int(request.args.get('month', date.today().month))
    
    from calendar import monthrange
    _, last_day = monthrange(year, month)
    
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)
    
    # Get all sheets for this month
    sheets = IntakeSheet.query.filter(
        IntakeSheet.sheet_date >= start_date,
        IntakeSheet.sheet_date <= end_date
    ).all()
    
    # Calculate weekly breakdown
    weekly_breakdown = []
    
    current_week_start = start_date
    week_num = 1
    
    while current_week_start <= end_date:
        current_week_end = min(current_week_start + timedelta(days=6), end_date)
        
        week_data = {
            'week': week_num,
            'start': current_week_start,
            'end': current_week_end,
            'received': 0,
            'outstock': 0,
            'lost': 0
        }
        
        # Get sheets for this week
        week_sheets = [s for s in sheets if current_week_start <= s.sheet_date <= current_week_end]
        
        for sheet in week_sheets:
            product = sheet.product_type
            if product in ['Maize Grains', 'Maize Germ']:
                bag_weight = 90
            else:
                bag_weight = 50
            
            total_weight = sum(entry.weight for entry in sheet.entries)
            total_bags = total_weight / bag_weight if bag_weight > 0 else 0
            
            if sheet.sheet_type == 'received':
                week_data['received'] += total_bags
            elif sheet.sheet_type == 'outstock':
                week_data['outstock'] += total_bags
            elif sheet.sheet_type == 'lost':
                week_data['lost'] += total_bags
        
        weekly_breakdown.append(week_data)
        current_week_start = current_week_end + timedelta(days=1)
        week_num += 1
    
    # Calculate product breakdown
    product_breakdown = {}
    total_received = 0
    total_outstock = 0
    total_lost = 0
    
    for sheet in sheets:
        product = sheet.product_type
        
        if product in ['Maize Grains', 'Maize Germ']:
            bag_weight = 90
        else:
            bag_weight = 50
        
        total_weight = sum(entry.weight for entry in sheet.entries)
        total_bags = total_weight / bag_weight if bag_weight > 0 else 0
        
        if product not in product_breakdown:
            product_breakdown[product] = {'received': 0, 'outstock': 0, 'lost': 0}
        
        if sheet.sheet_type == 'received':
            product_breakdown[product]['received'] += total_bags
            total_received += total_bags
        elif sheet.sheet_type == 'outstock':
            product_breakdown[product]['outstock'] += total_bags
            total_outstock += total_bags
        elif sheet.sheet_type == 'lost':
            product_breakdown[product]['lost'] += total_bags
            total_lost += total_bags
    
    # Get inventory summaries
    unprocessed_inventories = UnprocessedInventory.query.all()
    processed_inventories = ProcessedInventory.query.all()
    
    # Prepare summary cards
    month_names = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']
    
    summary_cards = [
        {'title': 'TOTAL RECEIVED', 'value': f"{total_received:.1f}", 
         'subtitle': 'bags this month', 'color': 'success'},
        {'title': 'TOTAL OUT STOCK', 'value': f"{total_outstock:.1f}", 
         'subtitle': 'bags this month', 'color': 'danger'},
        {'title': 'TOTAL LOST', 'value': f"{total_lost:.1f}", 
         'subtitle': 'bags this month', 'color': 'warning'},
        {'title': 'NET CHANGE', 'value': f"{total_received - total_outstock - total_lost:+.1f}", 
         'subtitle': 'bags this month', 'color': 'info'}
    ]
    
    return render_template('reports/universal_report.html',
                         report_type='monthly',
                         report_title='Monthly Report',
                         report_subtitle=f"{month_names[month]} {year}",
                         report_icon='calendar-month',
                         year=year,
                         month=month,
                         start_date=start_date,
                         end_date=end_date,
                         summary_cards=summary_cards,
                         weekly_breakdown=weekly_breakdown,
                         product_breakdown=product_breakdown,
                         unprocessed_inventories=unprocessed_inventories,
                         processed_inventories=processed_inventories,
                         export_url=url_for('export_csv', date=end_date.strftime('%Y-%m-%d')))


@app.route('/reports/product-movement')
@login_required
def product_movement_report():
    """Product movement report for managers and directors"""
    if current_user.role not in ['manager', 'director', 'admin']:
        flash('Access denied. Manager or Director privileges required.', 'danger')
        return redirect(url_for('dashboard'))
    
    # Get date range (default to last 30 days)
    end_date_str = request.args.get('end_date', date.today().isoformat())
    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except:
        end_date = date.today()
    
    start_date = end_date - timedelta(days=29)
    
    # Get all sheets in date range
    sheets = IntakeSheet.query.filter(
        IntakeSheet.sheet_date >= start_date,
        IntakeSheet.sheet_date <= end_date
    ).all()
    
    # Calculate movement by product
    movement_data = {}
    total_received = 0
    total_outstock = 0
    total_lost = 0
    
    for sheet in sheets:
        product = sheet.product_type
        
        if product in ['Maize Grains', 'Maize Germ']:
            bag_weight = 90
        else:
            bag_weight = 50
        
        total_weight = sum(entry.weight for entry in sheet.entries)
        total_bags = total_weight / bag_weight if bag_weight > 0 else 0
        
        if product not in movement_data:
            movement_data[product] = []
        
        movement_data[product].append({
            'date': sheet.sheet_date,
            'type': sheet.sheet_type,
            'bags': total_bags,
            'worker': sheet.worker.name
        })
        
        if sheet.sheet_type == 'received':
            total_received += total_bags
        elif sheet.sheet_type == 'outstock':
            total_outstock += total_bags
        elif sheet.sheet_type == 'lost':
            total_lost += total_bags
    
    # Sort movements by date (most recent first)
    for product in movement_data:
        movement_data[product].sort(key=lambda x: x['date'], reverse=True)
    
    # Get current inventory
    unprocessed_inventories = UnprocessedInventory.query.all()
    processed_inventories = ProcessedInventory.query.all()
    
    # Prepare summary cards
    summary_cards = [
        {'title': 'PERIOD RECEIVED', 'value': f"{total_received:.1f}", 
         'subtitle': 'bags (30 days)', 'color': 'success'},
        {'title': 'PERIOD OUT STOCK', 'value': f"{total_outstock:.1f}", 
         'subtitle': 'bags (30 days)', 'color': 'danger'},
        {'title': 'PERIOD LOST', 'value': f"{total_lost:.1f}", 
         'subtitle': 'bags (30 days)', 'color': 'warning'},
        {'title': 'NET MOVEMENT', 'value': f"{total_received - total_outstock - total_lost:+.1f}", 
         'subtitle': 'bags (30 days)', 'color': 'info'}
    ]
    
    return render_template('reports/universal_report.html',
                         report_type='movement',
                         report_title='Product Movement Report',
                         report_subtitle=f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')} (30 days)",
                         report_icon='arrow-left-right',
                         start_date=start_date,
                         end_date=end_date,
                         summary_cards=summary_cards,
                         movement_data=movement_data,
                         unprocessed_inventories=unprocessed_inventories,
                         processed_inventories=processed_inventories,
                         export_url=url_for('export_csv', date=end_date.strftime('%Y-%m-%d')))


# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================
def init_database():
    """Initialize database with tables and default admin user"""
    with app.app_context():
        db.create_all()
        
        if User.query.filter_by(username='admin').first():
            return
        
        admin = User(
            username='admin',
            name='System Administrator',
            role='admin',
            must_change_password=False
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()

# ============================================================================
# RUN APPLICATION
# ============================================================================
if __name__ == '__main__':
    # Always call init — it's now safe to call every time
    init_database()
    
    app.run(debug=True, host='0.0.0.0', port=5000)