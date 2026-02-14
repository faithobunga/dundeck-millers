"""
Dunedeck Millers - Daily Weight Record System
Flask Application with Sheet-Based Tracking
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from functools import wraps
import os

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///dunedeck.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ============================================================================
# DATABASE MODELS
# ============================================================================

class User(UserMixin, db.Model):
    """User model for workers, managers, directors, and admins"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'worker', 'manager', 'director', 'admin'
    must_change_password = db.Column(db.Boolean, default=False)
    is_blocked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Relationships
    sheets = db.relationship('IntakeSheet', backref='worker', lazy=True, foreign_keys='IntakeSheet.worker_id')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class IntakeSheet(db.Model):
    """Daily intake sheet for a specific product"""
    id = db.Column(db.Integer, primary_key=True)
    sheet_date = db.Column(db.Date, nullable=False, default=date.today)
    product_type = db.Column(db.String(50), nullable=False)  # Maize Grains, Maize Germ, Animal Feeds, Other
    worker_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='In Progress')  # 'In Progress', 'Closed'
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime)
    
    # Relationships
    entries = db.relationship('IntakeEntry', backref='sheet', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'sheet_date': self.sheet_date.isoformat(),
            'product_type': self.product_type,
            'worker_name': self.worker.name,
            'status': self.status,
            'created_at': self.created_at.isoformat(),
            'entry_count': len(self.entries)
        }


class IntakeEntry(db.Model):
    """Individual cage entry within a sheet"""
    id = db.Column(db.Integer, primary_key=True)
    sheet_id = db.Column(db.Integer, db.ForeignKey('intake_sheet.id'), nullable=False)
    cage_number = db.Column(db.String(20), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    audit_logs = db.relationship('AuditLog', backref='entry', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'cage_number': self.cage_number,
            'weight': self.weight,
            'timestamp': self.timestamp.isoformat()
        }


class AuditLog(db.Model):
    """Audit log for tracking all changes"""
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey('intake_entry.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False)  # 'create', 'edit', 'delete'
    previous_value = db.Column(db.String(200))
    new_value = db.Column(db.String(200))
    editor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    editor = db.relationship('User', foreign_keys=[editor_id])


# ============================================================================
# LOGIN MANAGER
# ============================================================================

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ============================================================================
# DECORATORS
# ============================================================================

def manager_required(f):
    """Decorator to require manager or admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['manager', 'admin']:
            flash('Access denied. Manager privileges required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# ROUTES - AUTHENTICATION
# ============================================================================

@app.route('/')
def index():
    """Redirect to login or dashboard"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            # Check if user is blocked
            if user.is_blocked:
                flash('Your account has been blocked. Please contact the administrator.', 'danger')
                return redirect(url_for('login'))
            
            # Update last login
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            login_user(user)
            
            # Check if password change required
            if user.must_change_password:
                flash('You must change your password before continuing.', 'warning')
                return redirect(url_for('change_password'))
            
            flash(f'Welcome back, {user.name}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    """Logout current user"""
    logout_user()
    session.clear()
    flash('You have been logged out successfully', 'info')
    return redirect(url_for('login'))


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Change password page"""
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            flash('Passwords do not match', 'danger')
        elif len(new_password) < 6:
            flash('Password must be at least 6 characters', 'danger')
        else:
            current_user.set_password(new_password)
            current_user.must_change_password = False
            db.session.commit()
            flash('Password changed successfully!', 'success')
            return redirect(url_for('dashboard'))
    
    return render_template('change_password.html')


# ============================================================================
# ROUTES - DASHBOARD
# ============================================================================

@app.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard - redirects based on role"""
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif current_user.role == 'manager':
        return redirect(url_for('manager_dashboard'))
    elif current_user.role == 'director':
        return redirect(url_for('director_dashboard'))
    else:
        return redirect(url_for('worker_dashboard'))


@app.route('/worker/dashboard')
@login_required
def worker_dashboard():
    """Worker dashboard - shows only In Progress and Closed sheets"""
    if current_user.role not in ['worker', 'manager', 'admin']:
        return redirect(url_for('dashboard'))
    
    # Get only In Progress and Closed sheets for this worker
    sheets = IntakeSheet.query.filter_by(worker_id=current_user.id)\
                               .filter(IntakeSheet.status.in_(['In Progress', 'Closed']))\
                               .order_by(IntakeSheet.created_at.desc()).all()
    
    # Calculate totals for each sheet
    sheet_data = []
    total_all_cages = 0
    total_all_weight = 0
    
    for sheet in sheets:
        entries = sheet.entries
        total_cages = len(entries)
        total_weight = sum(entry.weight for entry in entries)
        
        # Calculate bags based on product type
        if sheet.product_type in ['Maize Grains', 'Maize Germ']:
            bag_weight = 90
        else:  # Animal Feeds or Other
            bag_weight = 50
        
        total_bags = total_weight / bag_weight if bag_weight > 0 else 0
        
        sheet_data.append({
            'sheet': sheet,
            'total_cages': total_cages,
            'total_weight': total_weight,
            'total_bags': total_bags,
            'bag_weight': bag_weight
        })
        
        total_all_cages += total_cages
        total_all_weight += total_weight
    
    today = date.today()
    
    return render_template('worker_dashboard.html', 
                         sheet_data=sheet_data,
                         total_all_cages=total_all_cages,
                         total_all_weight=total_all_weight,
                         today=today)


@app.route('/manager/dashboard')
@login_required
@manager_required
def manager_dashboard():
    """Manager dashboard - view all sheets from all workers"""
    # Get selected date (default to today)
    selected_date_str = request.args.get('date', date.today().isoformat())
    selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
    
    # Get filter parameters
    worker_filter = request.args.get('worker', 'all')
    
    # Base query
    query = IntakeSheet.query.filter_by(sheet_date=selected_date)
    
    # Apply worker filter
    if worker_filter != 'all':
        query = query.filter_by(worker_id=int(worker_filter))
    
    sheets = query.order_by(IntakeSheet.created_at.desc()).all()
    
    # Calculate totals
    sheet_data = []
    grand_total_cages = 0
    grand_total_weight = 0
    
    for sheet in sheets:
        entries = sheet.entries
        total_cages = len(entries)
        total_weight = sum(entry.weight for entry in entries)
        
        if sheet.product_type in ['Maize Grains', 'Maize Germ']:
            bag_weight = 90
        else:
            bag_weight = 50
        
        total_bags = total_weight / bag_weight if bag_weight > 0 else 0
        
        sheet_data.append({
            'sheet': sheet,
            'total_cages': total_cages,
            'total_weight': total_weight,
            'total_bags': total_bags,
            'bag_weight': bag_weight
        })
        
        grand_total_cages += total_cages
        grand_total_weight += total_weight
    
    # Get all workers for filter
    workers = User.query.filter_by(role='worker').all()
    
    return render_template('manager_dashboard.html',
                         sheet_data=sheet_data,
                         selected_date=selected_date,
                         worker_filter=worker_filter,
                         workers=workers,
                         grand_total_cages=grand_total_cages,
                         grand_total_weight=grand_total_weight)


@app.route('/director/dashboard')
@login_required
def director_dashboard():
    """Director dashboard - view-only access to all sheets"""
    if current_user.role not in ['director', 'admin']:
        return redirect(url_for('dashboard'))
    
    # Get selected date (default to today)
    selected_date_str = request.args.get('date', date.today().isoformat())
    selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
    
    sheets = IntakeSheet.query.filter_by(sheet_date=selected_date)\
                               .order_by(IntakeSheet.created_at.desc()).all()
    
    # Calculate totals
    sheet_data = []
    grand_total_cages = 0
    grand_total_weight = 0
    
    for sheet in sheets:
        entries = sheet.entries
        total_cages = len(entries)
        total_weight = sum(entry.weight for entry in entries)
        
        if sheet.product_type in ['Maize Grains', 'Maize Germ']:
            bag_weight = 90
        else:
            bag_weight = 50
        
        total_bags = total_weight / bag_weight if bag_weight > 0 else 0
        
        sheet_data.append({
            'sheet': sheet,
            'total_cages': total_cages,
            'total_weight': total_weight,
            'total_bags': total_bags,
            'bag_weight': bag_weight
        })
        
        grand_total_cages += total_cages
        grand_total_weight += total_weight
    
    return render_template('director_dashboard.html',
                         sheet_data=sheet_data,
                         selected_date=selected_date,
                         grand_total_cages=grand_total_cages,
                         grand_total_weight=grand_total_weight)


@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    """Admin dashboard - user management"""
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin_dashboard.html', users=users)


# ============================================================================
# ROUTES - SHEET MANAGEMENT
# ============================================================================

@app.route('/sheet/create', methods=['POST'])
@login_required
def create_sheet():
    """Create a new intake sheet"""
    if current_user.role not in ['worker', 'manager', 'admin']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    product_type = request.form.get('product_type')
    
    if not product_type:
        return jsonify({'success': False, 'message': 'Product type is required'}), 400
    
    # Create new sheet
    sheet = IntakeSheet(
        product_type=product_type,
        worker_id=current_user.id,
        status='In Progress'
    )
    
    db.session.add(sheet)
    db.session.commit()
    
    flash(f'New sheet created for {product_type}', 'success')
    return redirect(url_for('view_sheet', sheet_id=sheet.id))


@app.route('/sheet/<int:sheet_id>')
@login_required
def view_sheet(sheet_id):
    """View a specific sheet"""
    sheet = IntakeSheet.query.get_or_404(sheet_id)
    
    # Check permissions
    if current_user.role == 'worker' and sheet.worker_id != current_user.id:
        flash('You can only view your own sheets', 'danger')
        return redirect(url_for('worker_dashboard'))
    
    entries = sheet.entries
    
    # Calculate totals
    total_cages = len(entries)
    total_weight = sum(entry.weight for entry in entries)
    
    if sheet.product_type in ['Maize Grains', 'Maize Germ']:
        bag_weight = 90
    else:
        bag_weight = 50
    
    total_bags = total_weight / bag_weight if bag_weight > 0 else 0
    
    # Get next cage number
    if entries:
        last_cage = max(int(e.cage_number) for e in entries if e.cage_number.isdigit())
        next_cage = last_cage + 1
    else:
        next_cage = 1
    
    # Check if sheet is editable
    is_editable = (current_user.role in ['manager', 'admin']) or \
                  (current_user.id == sheet.worker_id and sheet.status == 'In Progress')
    
    return render_template('view_sheet.html',
                         sheet=sheet,
                         entries=entries,
                         total_cages=total_cages,
                         total_weight=total_weight,
                         total_bags=total_bags,
                         bag_weight=bag_weight,
                         next_cage=next_cage,
                         is_editable=is_editable)


@app.route('/sheet/<int:sheet_id>/close', methods=['POST'])
@login_required
def close_sheet(sheet_id):
    """Close a sheet (lock from further editing)"""
    sheet = IntakeSheet.query.get_or_404(sheet_id)
    
    # Check permissions
    if current_user.role == 'worker' and sheet.worker_id != current_user.id:
        return jsonify({'success': False, 'message': 'You can only close your own sheets'}), 403
    
    if sheet.status == 'Closed':
        return jsonify({'success': False, 'message': 'Sheet is already closed'}), 400
    
    sheet.status = 'Closed'
    sheet.closed_at = datetime.utcnow()
    db.session.commit()
    
    flash('Sheet closed successfully', 'success')
    return jsonify({'success': True, 'message': 'Sheet closed successfully'})


# ============================================================================
# ROUTES - ENTRY MANAGEMENT
# ============================================================================

@app.route('/entry/add', methods=['POST'])
@login_required
def add_entry():
    """Add new entry to a sheet"""
    sheet_id = request.form.get('sheet_id')
    cage_number = request.form.get('cage_number')
    weight = request.form.get('weight')
    
    sheet = IntakeSheet.query.get_or_404(sheet_id)
    
    # Check permissions
    if current_user.role == 'worker':
        if sheet.worker_id != current_user.id:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        if sheet.status == 'Closed':
            return jsonify({'success': False, 'message': 'Cannot add to closed sheet'}), 403
    
    # Validate inputs
    if not cage_number or not weight:
        return jsonify({'success': False, 'message': 'Cage number and weight are required'}), 400
    
    try:
        weight = float(weight)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid weight value'}), 400
    
    if weight <= 0:
        return jsonify({'success': False, 'message': 'Weight must be greater than 0'}), 400
    
    if weight > 1000:
        return jsonify({'success': False, 'message': 'Weight seems unusually high (max 1000 kg)'}), 400
    
    # Check for duplicate cage number in this sheet
    existing = IntakeEntry.query.filter_by(
        sheet_id=sheet_id,
        cage_number=cage_number
    ).first()
    
    if existing:
        return jsonify({'success': False, 'message': 'Cage number already exists in this sheet'}), 400
    
    # Create new entry
    entry = IntakeEntry(
        sheet_id=sheet_id,
        cage_number=cage_number,
        weight=weight
    )
    
    db.session.add(entry)
    db.session.flush()
    
    # Create audit log
    audit = AuditLog(
        entry_id=entry.id,
        action='create',
        new_value=f'Cage: {cage_number}, Weight: {weight}kg',
        editor_id=current_user.id
    )
    db.session.add(audit)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Entry added successfully', 'entry': entry.to_dict()})


@app.route('/entry/edit/<int:entry_id>', methods=['POST'])
@login_required
def edit_entry(entry_id):
    """Edit an existing entry"""
    entry = IntakeEntry.query.get_or_404(entry_id)
    sheet = entry.sheet
    
    # Check permissions
    if current_user.role == 'worker':
        if sheet.worker_id != current_user.id:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        if sheet.status == 'Closed':
            return jsonify({'success': False, 'message': 'Cannot edit closed sheet'}), 403
    
    # Get new values
    new_cage = request.form.get('cage_number')
    new_weight = request.form.get('weight')
    
    if not new_cage or not new_weight:
        return jsonify({'success': False, 'message': 'Cage number and weight are required'}), 400
    
    try:
        new_weight = float(new_weight)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid weight value'}), 400
    
    if new_weight <= 0:
        return jsonify({'success': False, 'message': 'Weight must be greater than 0'}), 400
    
    if new_weight > 1000:
        return jsonify({'success': False, 'message': 'Weight seems unusually high'}), 400
    
    # Check for duplicate cage number
    if new_cage != entry.cage_number:
        existing = IntakeEntry.query.filter_by(
            sheet_id=sheet.id,
            cage_number=new_cage
        ).filter(IntakeEntry.id != entry_id).first()
        
        if existing:
            return jsonify({'success': False, 'message': 'Cage number already exists'}), 400
    
    # Store old values for audit
    old_value = f'Cage: {entry.cage_number}, Weight: {entry.weight}kg'
    new_value = f'Cage: {new_cage}, Weight: {new_weight}kg'
    
    # Update entry
    entry.cage_number = new_cage
    entry.weight = new_weight
    
    # Create audit log
    audit = AuditLog(
        entry_id=entry.id,
        action='edit',
        previous_value=old_value,
        new_value=new_value,
        editor_id=current_user.id
    )
    db.session.add(audit)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Entry updated successfully', 'entry': entry.to_dict()})


@app.route('/entry/delete/<int:entry_id>', methods=['POST'])
@login_required
def delete_entry(entry_id):
    """Delete an entry"""
    entry = IntakeEntry.query.get_or_404(entry_id)
    
    # Only managers and admins can delete
    if current_user.role not in ['manager', 'admin']:
        return jsonify({'success': False, 'message': 'Only managers can delete entries'}), 403
    
    # Store value for audit
    old_value = f'Cage: {entry.cage_number}, Weight: {entry.weight}kg'
    
    # Create audit log
    audit = AuditLog(
        entry_id=entry.id,
        action='delete',
        previous_value=old_value,
        editor_id=current_user.id
    )
    db.session.add(audit)
    
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
    username = request.form.get('username')
    name = request.form.get('name')
    role = request.form.get('role')
    temp_password = request.form.get('password', 'temp123')
    
    if not username or not name or not role:
        flash('All fields are required', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Check if username exists
    existing = User.query.filter_by(username=username).first()
    if existing:
        flash('Username already exists', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Create user
    user = User(
        username=username,
        name=name,
        role=role,
        must_change_password=True
    )
    user.set_password(temp_password)
    
    db.session.add(user)
    db.session.commit()
    
    flash(f'User {name} created successfully with temporary password: {temp_password}', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Delete a user"""
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Cannot delete yourself'}), 400
    
    username = user.username
    db.session.delete(user)
    db.session.commit()
    
    flash(f'User {username} deleted successfully', 'success')
    return jsonify({'success': True, 'message': 'User deleted successfully'})


@app.route('/admin/user/<int:user_id>/block', methods=['POST'])
@login_required
@admin_required
def block_user(user_id):
    """Block/Unblock a user"""
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Cannot block yourself'}), 400
    
    user.is_blocked = not user.is_blocked
    db.session.commit()
    
    status = "blocked" if user.is_blocked else "unblocked"
    flash(f'User {user.name} has been {status}', 'success')
    return jsonify({'success': True, 'message': f'User {status}', 'is_blocked': user.is_blocked})


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
    
    flash(f'Password reset for {user.name}. New password: {new_password}', 'success')
    return jsonify({'success': True, 'message': 'Password reset successfully', 'new_password': new_password})


@app.route('/admin/user/<int:user_id>/details')
@login_required
@admin_required
def user_details(user_id):
    """View user details"""
    user = User.query.get_or_404(user_id)
    
    # Get user's sheets if worker
    sheets = []
    total_entries = 0
    if user.role == 'worker':
        sheets = IntakeSheet.query.filter_by(worker_id=user.id).all()
        for sheet in sheets:
            total_entries += len(sheet.entries)
    
    return render_template('user_details.html', 
                         user=user, 
                         sheets=sheets, 
                         total_entries=total_entries)


# ============================================================================
# ROUTES - REPORTS & EXPORT
# ============================================================================

@app.route('/export/csv')
@login_required
def export_csv():
    """Export data as CSV"""
    from io import StringIO
    import csv
    
    date_str = request.args.get('date', date.today().isoformat())
    export_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    # Get sheets
    if current_user.role == 'worker':
        sheets = IntakeSheet.query.filter_by(
            worker_id=current_user.id,
            sheet_date=export_date
        ).all()
    else:
        sheets = IntakeSheet.query.filter_by(sheet_date=export_date).all()
    
    # Create CSV
    output = StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(['Date', 'Worker', 'Product Type', 'Sheet Status', 'Cage Number', 'Weight (KG)', 'Timestamp'])
    
    # Data
    for sheet in sheets:
        for entry in sheet.entries:
            writer.writerow([
                sheet.sheet_date.isoformat(),
                sheet.worker.name,
                sheet.product_type,
                sheet.status,
                entry.cage_number,
                entry.weight,
                entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            ])
    
    # Return CSV
    from flask import Response
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=dunedeck_sheets_{export_date.isoformat()}.csv'}
    )


# ============================================================================
# INITIALIZATION & DATABASE SETUP
# ============================================================================

def init_database():
    """Initialize database with ONLY ONE ADMIN user - add real users yourself!"""
    with app.app_context():
        db.create_all()
        
        # Check if any users exist
        if User.query.count() == 0:
            print("\n" + "="*70)
            print(" "*15 + "DUNEDECK MILLERS - INITIAL SETUP")
            print("="*70)
            print("\n🔐 Creating default administrator account...\n")
            
            # Create ONLY ONE admin user
            admin = User(username='admin', name='Administrator', role='admin')
            admin.set_password('admin123')
            
            db.session.add(admin)
            db.session.commit()
            
            print("✅ Database initialized successfully!\n")
            print("📋 DEFAULT ADMIN LOGIN:")
            print("-"*70)
            print(f"  Username: admin")
            print(f"  Password: admin123")
            print("-"*70)
            print("\n🎯 NEXT STEPS:")
            print("  1. Login with the admin credentials above")
            print("  2. Go to Admin Dashboard")
            print("  3. Click 'Create User' to add your real workers, managers, etc.")
            print("  4. Use real names and usernames for your actual team")
            print("="*70 + "\n")


# ============================================================================
# RUN APPLICATION
# ============================================================================

if __name__ == '__main__':
    init_database()
    app.run(debug=True, host='0.0.0.0', port=5000)