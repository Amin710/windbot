#!/usr/bin/env python3
"""
Wind Reseller Bot - Web Admin Panel
"""

import os
import sys
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Add parent directory to path to import bot modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_conn
from bot import decrypt_secret, encrypt

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')

# Login Manager Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'لطفاً برای دسترسی به این صفحه وارد شوید.'

class AdminUser(UserMixin):
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, username FROM admin_users WHERE id = %s", (user_id,))
                user = cur.fetchone()
                if user:
                    return AdminUser(user['id'], user['username'])
    except:
        pass
    return None

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT id, username, password_hash FROM admin_users WHERE username = %s", (username,))
                    user = cur.fetchone()
                    
                    if user and check_password_hash(user['password_hash'], password):
                        admin_user = AdminUser(user['id'], user['username'])
                        login_user(admin_user)
                        return redirect(url_for('dashboard'))
                    else:
                        flash('نام کاربری یا رمز عبور اشتباه است.', 'error')
        except Exception as e:
            flash(f'خطا در اتصال به پایگاه داده: {e}', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('با موفقیت خارج شدید.', 'success')
    return redirect(url_for('login'))

@app.route('/')
@admin_required
def dashboard():
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get statistics
                stats = {}
                
                # Users count
                cur.execute("SELECT COUNT(*) as count FROM users")
                stats['users_count'] = cur.fetchone()['count']
                
                # Total orders
                cur.execute("SELECT COUNT(*) as count FROM orders")
                stats['orders_count'] = cur.fetchone()['count']
                
                # Approved orders
                cur.execute("SELECT COUNT(*) as count FROM orders WHERE status = 'approved'")
                stats['approved_orders'] = cur.fetchone()['count']
                
                # Active seats
                cur.execute("SELECT COUNT(*) as count FROM seats WHERE status = 'active'")
                stats['active_seats'] = cur.fetchone()['count']
                
                # Available slots
                cur.execute("SELECT SUM(max_slots - sold) as available FROM seats WHERE status = 'active'")
                result = cur.fetchone()
                stats['available_slots'] = result['available'] or 0
                
                # Recent orders
                cur.execute("""
                    SELECT o.id, o.amount, o.status, o.created_at, u.username, u.first_name 
                    FROM orders o 
                    JOIN users u ON o.user_id = u.id 
                    ORDER BY o.created_at DESC 
                    LIMIT 10
                """)
                recent_orders = cur.fetchall()
                
                return render_template('dashboard.html', stats=stats, recent_orders=recent_orders)
                
    except Exception as e:
        flash(f'خطا در بارگذاری داشبورد: {e}', 'error')
        return render_template('dashboard.html', stats={}, recent_orders=[])

@app.route('/seats')
@admin_required
def seats():
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, email, max_slots, sold, status, created_at 
                    FROM seats 
                    ORDER BY created_at DESC
                """)
                seats_list = cur.fetchall()
                return render_template('seats.html', seats=seats_list)
    except Exception as e:
        flash(f'خطا در بارگذاری لیست اکانت‌ها: {e}', 'error')
        return render_template('seats.html', seats=[])

@app.route('/seats/add', methods=['GET', 'POST'])
@admin_required
def add_seat():
    if request.method == 'POST':
        try:
            email = request.form['email']
            password = request.form['password']
            secret = request.form['secret']
            max_slots = int(request.form['max_slots'])
            
            # Encrypt sensitive data
            password_enc = encrypt(password.encode())
            secret_enc = encrypt(secret.encode())
            
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO seats (email, pass_enc, secret_enc, max_slots, sold, status)
                        VALUES (%s, %s, %s, %s, 0, 'active')
                    """, (email, password_enc, secret_enc, max_slots))
                    conn.commit()
            
            flash('اکانت جدید با موفقیت اضافه شد.', 'success')
            return redirect(url_for('seats'))
            
        except Exception as e:
            flash(f'خطا در افزودن اکانت: {e}', 'error')
    
    return render_template('add_seat.html')

@app.route('/seats/<int:seat_id>/view')
@admin_required
def view_seat(seat_id):
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM seats WHERE id = %s", (seat_id,))
                seat = cur.fetchone()
                
                if seat:
                    # Decrypt sensitive data for display
                    seat['password'] = decrypt_secret(seat['pass_enc'])
                    seat['secret'] = decrypt_secret(seat['secret_enc'])
                    return render_template('view_seat.html', seat=seat)
                else:
                    flash('اکانت یافت نشد.', 'error')
                    return redirect(url_for('seats'))
                    
    except Exception as e:
        flash(f'خطا در نمایش اکانت: {e}', 'error')
        return redirect(url_for('seats'))

@app.route('/cards')
@admin_required
def cards():
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, card_number, created_at, updated_at 
                    FROM cards 
                    ORDER BY created_at DESC
                """)
                cards_list = cur.fetchall()
                return render_template('cards.html', cards=cards_list)
    except Exception as e:
        flash(f'خطا در بارگذاری لیست کارت‌ها: {e}', 'error')
        return render_template('cards.html', cards=[])

@app.route('/orders')
@admin_required
def orders():
    status_filter = request.args.get('status', 'all')
    
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if status_filter == 'all':
                    cur.execute("""
                        SELECT o.id, o.amount, o.status, o.created_at, o.approved_at,
                               u.username, u.first_name, u.tg_id
                        FROM orders o 
                        JOIN users u ON o.user_id = u.id 
                        ORDER BY o.created_at DESC
                    """)
                else:
                    cur.execute("""
                        SELECT o.id, o.amount, o.status, o.created_at, o.approved_at,
                               u.username, u.first_name, u.tg_id
                        FROM orders o 
                        JOIN users u ON o.user_id = u.id 
                        WHERE o.status = %s
                        ORDER BY o.created_at DESC
                    """, (status_filter,))
                
                orders_list = cur.fetchall()
                return render_template('orders.html', orders=orders_list, status_filter=status_filter)
                
    except Exception as e:
        flash(f'خطا در بارگذاری لیست سفارشات: {e}', 'error')
        return render_template('orders.html', orders=[], status_filter=status_filter)

@app.route('/users')
@admin_required
def users():
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT u.id, u.tg_id, u.username, u.first_name, u.joined_at,
                           COUNT(o.id) as orders_count
                    FROM users u 
                    LEFT JOIN orders o ON u.id = o.user_id
                    GROUP BY u.id, u.tg_id, u.username, u.first_name, u.joined_at
                    ORDER BY u.joined_at DESC
                """)
                users_list = cur.fetchall()
                return render_template('users.html', users=users_list)
                
    except Exception as e:
        flash(f'خطا در بارگذاری لیست کاربران: {e}', 'error')
        return render_template('users.html', users=[])

# API Endpoints

@app.route('/api/stats')
@admin_required
def api_stats():
    """API endpoint for dashboard statistics"""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Sales data for charts
                cur.execute("""
                    SELECT DATE(created_at) as date, COUNT(*) as count
                    FROM orders 
                    WHERE status = 'approved' AND created_at >= CURRENT_DATE - INTERVAL '30 days'
                    GROUP BY DATE(created_at)
                    ORDER BY date
                """)
                sales_data = cur.fetchall()
                
                return jsonify({
                    'sales_data': [{'date': row['date'].strftime('%Y-%m-%d'), 'count': row['count']} for row in sales_data]
                })
                
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/<int:order_id>')
@admin_required
def api_order_details(order_id):
    """Get order details"""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT o.*, u.username, u.first_name, u.tg_id
                    FROM orders o 
                    JOIN users u ON o.user_id = u.id 
                    WHERE o.id = %s
                """, (order_id,))
                order = cur.fetchone()
                
                if order:
                    return jsonify({
                        'id': order['id'],
                        'amount': order['amount'],
                        'status': order['status'],
                        'created_at': order['created_at'].strftime('%Y/%m/%d %H:%M'),
                        'user': {
                            'first_name': order['first_name'],
                            'username': order['username'],
                            'tg_id': order['tg_id']
                        }
                    })
                else:
                    return jsonify({'error': 'Order not found'}), 404
                    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/<int:order_id>/approve', methods=['POST'])
@admin_required
def api_approve_order(order_id):
    """Approve an order"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE orders 
                    SET status = 'approved', approved_at = NOW()
                    WHERE id = %s AND status IN ('pending', 'receipt')
                """, (order_id,))
                
                if cur.rowcount > 0:
                    conn.commit()
                    return jsonify({'success': True})
                else:
                    return jsonify({'error': 'Order not found or cannot be approved'}), 400
                    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/<int:order_id>/reject', methods=['POST'])
@admin_required
def api_reject_order(order_id):
    """Reject an order"""
    try:
        data = request.get_json()
        reason = data.get('reason', '') if data else ''
        
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE orders 
                    SET status = 'rejected'
                    WHERE id = %s AND status IN ('pending', 'receipt')
                """, (order_id,))
                
                if cur.rowcount > 0:
                    conn.commit()
                    return jsonify({'success': True})
                else:
                    return jsonify({'error': 'Order not found or cannot be rejected'}), 400
                    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cards', methods=['POST'])
@admin_required
def api_add_card():
    """Add a new card"""
    try:
        data = request.get_json()
        card_number = data.get('card_number')
        
        if not card_number:
            return jsonify({'error': 'Card number is required'}), 400
            
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cards (card_number, created_at)
                    VALUES (%s, NOW())
                """, (card_number,))
                conn.commit()
                
                return jsonify({'success': True})
                
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cards/<int:card_id>', methods=['PUT'])
@admin_required
def api_update_card(card_id):
    """Update a card"""
    try:
        data = request.get_json()
        card_number = data.get('card_number')
        
        if not card_number:
            return jsonify({'error': 'Card number is required'}), 400
            
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE cards 
                    SET card_number = %s, updated_at = NOW()
                    WHERE id = %s
                """, (card_number, card_id))
                
                if cur.rowcount > 0:
                    conn.commit()
                    return jsonify({'success': True})
                else:
                    return jsonify({'error': 'Card not found'}), 404
                    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cards/<int:card_id>', methods=['DELETE'])
@admin_required
def api_delete_card(card_id):
    """Delete a card"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM cards WHERE id = %s", (card_id,))
                
                if cur.rowcount > 0:
                    conn.commit()
                    return jsonify({'success': True})
                else:
                    return jsonify({'error': 'Card not found'}), 404
                    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/send-message', methods=['POST'])
@admin_required
def api_send_message():
    """Send message to user (placeholder - requires bot integration)"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        message = data.get('message')
        
        if not user_id or not message:
            return jsonify({'error': 'User ID and message are required'}), 400
        
        # TODO: Integrate with bot to actually send message
        # For now, just return success
        # This would typically send a message via the bot's send_message function
        
        return jsonify({'success': True, 'message': 'Message queued for sending'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Create admin user table if not exists
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS admin_users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(50) UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                
                # Create default admin user (admin/admin123)
                cur.execute("SELECT COUNT(*) FROM admin_users WHERE username = 'admin'")
                if cur.fetchone()[0] == 0:
                    password_hash = generate_password_hash('admin123')
                    cur.execute("""
                        INSERT INTO admin_users (username, password_hash)
                        VALUES ('admin', %s)
                    """, (password_hash,))
                
                conn.commit()
                print("Admin user table created and default admin user added.")
                
    except Exception as e:
        print(f"Error setting up admin users: {e}")
    
    app.run(debug=True, host='0.0.0.0', port=5000) 