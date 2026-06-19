from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, jsonify
import os
from datetime import datetime
import cv2
import threading
import time
from models.face_registration import register_face, verify_identity, is_student_registered
from models.proctoring_systems import ProctoringSystem
from database.database import init_db, get_connection
# Mock models for sqlite3

app = Flask(__name__)
app.secret_key = 'proctoring_secret_key_change_in_prod'
app.config['UPLOAD_FOLDER'] = 'uploads'

# Init DB
init_db()

# Global proctoring system
proctoring_system = None

def init_proctoring():
    global proctoring_system
    proctoring_system = ProctoringSystem()

@app.route('/')
def index():
    if 'user_id' in session:
        role = session.get('role')
        if role == 'student':
            return redirect(url_for('student_dashboard'))
        elif role == 'admin':
            return redirect(url_for('admin_dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role = request.form['role']
        username = request.form['username']
        # Simple auth - replace with DB
        if username == 'student1' and role == 'student':
            session['user_id'] = username
            session['role'] = 'student'
            flash('Logged in as student')
            return redirect(url_for('student_dashboard'))
        elif username == 'admin' and role == 'admin':
            session['user_id'] = username
            session['role'] = 'admin'
            flash('Logged in as admin')
            return redirect(url_for('admin_dashboard'))
        flash('Invalid credentials')
    return render_template('auth/student_login_page.html')  # Reuse or create

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        student_id = request.form['student_id']
        # Assume frame from JS webcam - for demo, mock
        frame = None  # Get from request.files or JS
        result = register_face(student_id, frame)
        # Mock user save (use create_user if email/password)
        print(f"User {student_id} registered (mock)")
        flash('Registered successfully - mock success for demo')
        return redirect(url_for('login'))
        flash(result['message'])
    return render_template('auth/student_register_page.html')

@app.route('/student/dashboard')
def student_dashboard():
    if session.get('role') != 'student':
        return redirect(url_for('login'))
    return render_template('student/student_profile_proctor_view.html')

@app.route('/student/exam/<exam_id>')
def student_exam(exam_id):
    if session.get('role') != 'student':
        return redirect(url_for('login'))
    # Verify identity
    student_id = session['user_id']
    if not is_student_registered(student_id):
        flash('Face registration required')
        return redirect(url_for('register'))
    # Mock exam data
    questions = [
        {"id":1, "question": "What is proctoring?", "options": ["Monitoring", "B", "C", "D"]},
        {"id":2, "question": "AI detector?", "options": ["A", "Face", "C", "D"]},
        {"id":3, "question": "Gaze tracking?", "options": ["A", "B", "Eyes", "D"]},
        {"id":4, "question": "YOLO for?", "options": ["A", "B", "C", "Phone"]},
        {"id":5, "question": "Admin role?", "options": ["Label", "B", "C", "D"]},
        {"id":6, "question": "Violation types?", "options": ["A", "B", "Multiple", "All"]},
        {"id":7, "question": "EAR metric?", "options": ["A", "Eyes", "C", "D"]},
        {"id":8, "question": "Human loop?", "options": ["A", "B", "Label", "D"]},
        {"id":9, "question": "Precision?", "options": ["TP/(TP+FP)", "B", "C", "D"]},
        {"id":10, "question": "F1 score?", "options": ["A", "B", "C", "2PR/(P+R)"]}
    ]
    return render_template('student/student_exam_page.html', exam_id=exam_id, questions=questions)

@app.route('/student/submit_exam', methods=['POST'])
def submit_exam():
    answers = request.json
    student_id = session['user_id']
    # Save to DB
    # Mock exam save
    print(f"Exam submitted for {student_id}: {answers}")

    flash('Exam submitted')
    return jsonify({'status': 'success'})

@app.route('/video_feed')
def video_feed():
    def generate_frames():
        cap = cv2.VideoCapture(0)
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            # Proctoring analysis
            if proctoring_system:
                frame = proctoring_system.process_frame(frame)
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        cap.release()
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    # Get sessions/metrics
    sessions = []  # Mock
    return render_template('admin/online_exam_proctoring_dashboard.html', sessions=sessions)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out')
    return redirect(url_for('index'))

if __name__ == '__main__':
    init_proctoring()
    app.run(debug=True, host='0.0.0.0', port=5000)
