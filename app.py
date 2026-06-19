from flask import Flask, render_template, Response, request, redirect, url_for, session, jsonify
import cv2, os, json
import numpy as np
import time
import logging
from datetime import datetime

import sys
sys.path.insert(0, '.')
from models.proctoring_systems import ProctoringSystem
from database.database import create_user, get_user_by_email, get_connection, delete_user, update_user, get_user_by_id, get_all_labels, get_labels_by_session, create_label

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
proctoring = None
camera = None
proctoring_status = {}  # {f"{student_id}_{exam_id}": {"status": "starting|active|error", "start_time": ts}}
live_metrics_cache = {}  # {f"{student_id}_{exam_id}": {"metrics": dict, "last_updated": ts, "status": "active|completed"}}

app.secret_key = "change-me"

# Exam configuration
CORRECT_ANSWERS = [2, 2, 1, 1, 0, 2, 0, 2, 0, 2]
MARKS_PER_QUESTION = 1
PASSING_PERCENTAGE = 40

@app.route("/proctoring_status/<student_id>/<exam_id>")
def proctoring_status_api(student_id, exam_id):
    session_key = f"{student_id}_{exam_id}"
    status = proctoring_status.get(session_key, {"status": "inactive"})
    return jsonify(status)

def generate_frames():
    """Video generator for /video_feed."""
    global camera, proctoring
    
    # Safety check
    if proctoring is None:
        logger.error("Proctoring is None in generate_frames")
        while True:
            yield b'--frame\r\nContent-Type: text/plain\r\n\r\nProctoring not ready\r\n'
    
    if camera is None:
        logger.error("Camera is None in generate_frames")
        camera = cv2.VideoCapture(0)
        if not camera.isOpened():
            while True:
                yield b'--frame\r\nContent-Type: text/plain\r\n\r\nCamera error\r\n'
    
    if camera is None or proctoring is None:
        logger.warning("Camera or proctoring not initialized")
        return

    consecutive_errors = 0
    max_consecutive_errors = 10
    
    while True:
        try:
            if camera is None or not camera.isOpened():
                logger.info("Camera not available, attempting to reopen...")
                camera = cv2.VideoCapture(0)
                if not camera.isOpened():
                    logger.error("Failed to reopen camera")
                    break
            
            ret, frame = camera.read()
            if not ret:
                consecutive_errors += 1
                logger.warning(f"Failed to read frame, error count: {consecutive_errors}")
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error("Too many consecutive frame read errors, stopping")
                    break
                
                camera.release()
                camera = cv2.VideoCapture(0)
                if not camera.isOpened():
                    logger.error("Failed to reopen camera after error")
                    break
                continue
            
            consecutive_errors = 0
            
            results = proctoring.process_frame(frame)

            # Update current warnings for the API endpoint
            if hasattr(proctoring, 'current_warnings'):
                proctoring.current_warnings = results.get("warnings", [])

            if proctoring.audio_analyzer:
                try:
                    audio_res = proctoring.process_audio()
                    results["audio"] = audio_res
                    proctoring._handle_audio_violations(audio_res)
                except Exception as e:
                    logger.debug(f"Audio error: {e}")

            # Update live metrics cache for admin monitoring
            try:
                session_key = f"{proctoring.student_id}_{proctoring.exam_id}"
                live_metrics_cache[session_key] = {
                    "metrics": proctoring.get_metrics(),
                    "last_updated": time.time(),
                    "status": "active"
                }
            except Exception as cache_err:
                logger.debug(f"Live metrics cache update error: {cache_err}")

            display = proctoring.draw_combined_results(frame.copy(), results)

            ret, buffer = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ret:
                continue
                
            jpg_bytes = buffer.tobytes()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpg_bytes)).encode() + b"\r\n"
                b"\r\n" + jpg_bytes + b"\r\n"
            )
            
        except Exception as e:
            logger.error(f"Error in generate_frames: {e}")
            consecutive_errors += 1
            
            if consecutive_errors < max_consecutive_errors:
                try:
                    if camera is not None:
                        camera.release()
                    camera = cv2.VideoCapture(0)
                    time.sleep(0.5)
                except:
                    pass
            else:
                logger.error("Too many consecutive errors, stopping video stream")
                break

@app.route("/video_feed")
def video_feed():
    """MJPEG video stream for the exam page."""
    global camera, proctoring
    
    global proctoring
    if proctoring is None:
        logger.warning("Proctoring not initialized for video_feed")
        return Response("Proctoring not started", status=503)
    
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

def load_session_log(session_dir):
    log_path = os.path.join(session_dir, "session_log.json")
    if not os.path.exists(log_path):
        return None
    try:
        with open(log_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading session log: {e}")
        return None

@app.route("/")
def index():
    return redirect(url_for("auth_login"))

@app.route("/admin")
def admin_dashboard():
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    logs_root = os.path.join("logs")
    total_sessions = 0
    students = set()
    exams = set()
    sessions = []

    if os.path.isdir(logs_root):
        for name in os.listdir(logs_root):
            if not name.startswith("session_"):
                continue
            total_sessions += 1
            parts = name[len("session_"):].split("_", 1)
            if len(parts) == 2:
                exam_id, student_id = parts
                students.add(student_id)
                exams.add(exam_id)

            session_dir = os.path.join(logs_root, name)
            data = load_session_log(session_dir)
            if data:
                sessions.append({
                    "student_id": data["student_id"],
                    "exam_id": data["exam_id"],
                    "session_start": data["session_start"],
                    "session_end": data["session_end"],
                    "summary": data.get("violation_summary", {}),
                })

    sessions.sort(key=lambda s: s["session_start"], reverse=True)
    recent_sessions = sessions[:5]

    stats = {
        "total_sessions": total_sessions,
        "unique_students": len(students),
        "unique_exams": len(exams),
    }
    return render_template("admin/dashboard.html", stats=stats, recent_sessions=recent_sessions)

@app.route("/admin/sessions")
def admin_sessions():
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    logs_root = os.path.join("logs")
    sessions = []

    if os.path.isdir(logs_root):
        for name in os.listdir(logs_root):
            if not name.startswith("session_"):
                continue

            session_dir = os.path.join(logs_root, name)
            data = load_session_log(session_dir)
            if not data:
                continue

            sessions.append({
                "student_id": data["student_id"],
                "exam_id": data["exam_id"],
                "session_start": data["session_start"],
                "session_end": data["session_end"],
                "summary": data["violation_summary"],
            })

    sessions.sort(key=lambda s: s["session_start"], reverse=True)

    return render_template("admin/sessions.html", sessions=sessions)

@app.route("/admin/reports")
@app.route("/admin/reports/<exam_id>/<student_id>")
def admin_reports(exam_id=None, student_id=None):
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    if not exam_id or not student_id:
        return render_template(
            "admin/reports.html",
            exam_id="N/A",
            student_id="N/A",
            data={"violation_summary": {"total_violations": 0,
                                        "by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
                                        "violation_rate": 0.0},
                  "violation_history": [],
                  "session_start": "",
                  "session_end": ""}
        )

    session_dir = os.path.join("logs", f"session_{exam_id}_{student_id}")
    data = load_session_log(session_dir)
    if not data:
        data = {"violation_summary": {"total_violations": 0,
                                      "by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
                                      "violation_rate": 0.0},
                "violation_history": [],
                "session_start": "",
                "session_end": ""}

    return render_template(
        "admin/reports.html",
        exam_id=exam_id,
        student_id=student_id,
        data=data
    )

@app.route("/admin/exam_results/<exam_id>/<student_id>")
def admin_exam_results(exam_id, student_id):
    """Admin-only exam results page - evaluates answers and shows violations."""
    
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    answers = session.get(f'answers_{exam_id}_{student_id}', [])
    
    questions = [
        {"text": "What is the capital of France?", "options": ["London", "Berlin", "Paris", "Madrid"]},
        {"text": "Which programming language is primarily used for web development?", "options": ["Python", "Java", "JavaScript", "C++"]},
        {"text": "What is 25 + 17?", "options": ["40", "42", "44", "46"]},
        {"text": "Which data structure uses LIFO (Last In, First Out)?", "options": ["Queue", "Stack", "Array", "Linked List"]},
        {"text": "What is the binary representation of decimal number 13?", "options": ["1101", "1011", "1110", "1001"]},
        {"text": "Which company developed the Python programming language?", "options": ["Microsoft", "Google", "Guido van Rossum", "Apple"]},
        {"text": "What does HTML stand for?", "options": ["Hyper Text Markup Language", "High Tech Modern Language", "Home Tool Markup Language", "Hyperlinks Text Mark Language"]},
        {"text": "Which sorting algorithm has the best average-case time complexity?", "options": ["Bubble Sort", "Selection Sort", "Quick Sort", "Insertion Sort"]},
        {"text": "What is the main function of an operating system?", "options": ["Manage hardware and software resources", "Compile code", "Run games", "Create documents"]},
        {"text": "Which protocol is used for secure web browsing?", "options": ["HTTP", "FTP", "HTTPS", "SMTP"]}
    ]
    
    results = []
    correct = 0
    incorrect = 0
    unanswered = 0
    
    for i, question in enumerate(questions):
        user_answer = answers[i] if i < len(answers) else None
        correct_answer = CORRECT_ANSWERS[i]
        
        if user_answer is None:
            is_correct = None
            unanswered += 1
        elif user_answer == correct_answer:
            is_correct = True
            correct += 1
        else:
            is_correct = False
            incorrect += 1
        
        results.append({
            "your_answer": user_answer,
            "correct_answer": correct_answer,
            "is_correct": is_correct
        })
    
    total_questions = len(questions)
    marks_obtained = correct * MARKS_PER_QUESTION
    total_marks = total_questions * MARKS_PER_QUESTION
    raw_percentage = (correct / total_questions) * 100
    
    violations = {"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0, "history": [], "penalty": 0}
    
    session_dir = os.path.join("logs", f"session_{exam_id}_{student_id}")
    session_data = load_session_log(session_dir)
    
    if session_data and "violation_summary" in session_data:
        summary = session_data["violation_summary"]
        violations["total"] = summary.get("total_violations", 0)
        violations["critical"] = summary.get("by_severity", {}).get("CRITICAL", 0)
        violations["high"] = summary.get("by_severity", {}).get("HIGH", 0)
        violations["medium"] = summary.get("by_severity", {}).get("MEDIUM", 0)
        violations["low"] = summary.get("by_severity", {}).get("LOW", 0)
        violations["history"] = session_data.get("violation_history", [])
        
        violations["penalty"] = (
            violations["critical"] * 10 +
            violations["high"] * 5 +
            violations["medium"] * 2 +
            violations["low"] * 1
        )
    

    final_percentage = raw_percentage - violations["penalty"]
    passed = final_percentage >= PASSING_PERCENTAGE

    
    if violations["total"] > 0:
        if violations["critical"] > 0:
            evaluation_status = "FLAGGED"
        elif violations["high"] > 0:
            evaluation_status = "WARNING"
        else:
            evaluation_status = "WARNING"
    else:
        evaluation_status = "CLEAR"
    
    score = {
        "correct": correct,
        "incorrect": incorrect,
        "unanswered": unanswered,
        "total": total_questions,
        "marks_obtained": marks_obtained,
        "total_marks": total_marks,
        "raw_percentage": round(raw_percentage, 1),
        "percentage": round(final_percentage, 1),
        "passed": passed,
        "evaluation_status": evaluation_status,
        "violation_penalty": violations["penalty"]
    }
    
    return render_template(
        "admin/exam_results.html",
        student_id=student_id,
        exam_id=exam_id,
        questions=questions,
        results=results,
        score=score,
        violations=violations
    )

@app.route("/start_exam/<student_id>/<exam_id>")
def start_exam(student_id, exam_id):
    return render_template("student/exam.html", student_id=student_id, exam_id=exam_id)

@app.route("/start_proctoring/<student_id>/<exam_id>", methods=["POST"])
def start_proctoring(student_id, exam_id):
    global proctoring, camera
    session_key = f"{student_id}_{exam_id}"
    proctoring_status[session_key] = {"status": "starting", "start_time": time.time()}
    
    try:
        if camera is not None:
            try:
                camera.release()
            except:
                pass
            camera = None
        
        if proctoring is not None:
            try:
                proctoring.finalize_session()
            except:
                pass
            proctoring = None
        
        time.sleep(0.5)
        
        # Optimized config for faster detection
        config = {
            "frame_skip": 2,
            "object_detect_interval": 3,
        }
        proctoring = ProctoringSystem(student_id=student_id, exam_id=exam_id, config=config)
        
        camera = cv2.VideoCapture(0)
        
        if not camera.isOpened():
            proctoring_status[session_key]["status"] = "error"
            if session_key in proctoring_status:
                del proctoring_status[session_key]
            return jsonify({"status": "error", "message": "Failed to access camera"}), 500
        
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        camera.set(cv2.CAP_PROP_FPS, 30)
        
        proctoring_status[session_key]["status"] = "active"
        
        # Initialize live metrics cache entry
        live_metrics_cache[session_key] = {
            "metrics": proctoring.get_metrics(),
            "last_updated": time.time(),
            "status": "active"
        }
        
        logger.info(f"Proctoring started for {student_id} - {exam_id}")
        
        return jsonify({"status": "success", "message": "Proctoring started"})
        
    except Exception as e:
        if session_key in proctoring_status:
            proctoring_status[session_key]["status"] = "error"
        logger.error(f"Error starting proctoring: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/submit_exam/<student_id>/<exam_id>", methods=["POST"])
def submit_exam(student_id, exam_id):
    global proctoring, camera

    data = request.get_json() or {}
    answers = data.get("answers", [])

    session[f'answers_{exam_id}_{student_id}'] = answers

    if proctoring is not None:
        try:
            proctoring.finalize_session()
            logger.info(f"Session finalized for {student_id}")
        except Exception as e:
            logger.error(f"Error finalizing session: {e}")

    if camera is not None:
        camera.release()
        camera = None
    
    # Mark session as completed in live metrics cache
    session_key = f"{student_id}_{exam_id}"
    if session_key in live_metrics_cache:
        live_metrics_cache[session_key]["status"] = "completed"
        live_metrics_cache[session_key]["last_updated"] = time.time()

    proctoring = None

    return jsonify({"status": "ok"})

@app.route("/exam_completed/<student_id>/<exam_id>")
def exam_completed(student_id, exam_id):
    return render_template("student/completed.html", student_id=student_id, exam_id=exam_id)

@app.route("/admin/students")
def admin_students():
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    from database.database import get_all_users
    users = get_all_users()
    return render_template("admin/students.html", users=users)

@app.route("/admin/delete_user/<int:user_id>", methods=["POST"])
def delete_user_route(user_id):
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    
    if user.get("id") == user_id:
        return jsonify({"status": "error", "message": "Cannot delete your own account"}), 400
    
    try:
        delete_user(user_id)
        return jsonify({"status": "success", "message": "User deleted successfully"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/admin/edit_user/<int:user_id>", methods=["GET", "POST"])
def edit_user_route(user_id):
    admin_user = session.get("user")
    if not admin_user or admin_user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    from database.database import get_user_by_id
    
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        role = request.form["role"]
        password = request.form.get("password", "").strip() or None
        
        try:
            update_user(user_id, name, email, role, password)
            return jsonify({"status": "success", "message": "User updated successfully"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    
    target_user = get_user_by_id(user_id)
    if not target_user:
        return "User not found", 404
    
    return render_template("admin/edit_user.html", user=target_user)

@app.route("/login", methods=["GET", "POST"])
def auth_login():
    error = None
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        if email == "admin@example.com" and password == "admin":
            session["user"] = {"email": email, "role": "admin"}
            return redirect(url_for("admin_dashboard"))
        
        user = get_user_by_email(email)
        if user and user["password"] == password:
            session["user"] = {
                "id": user["id"],
                "email": user["email"],
                "role": user["role"],
                "name": user["name"],
            }
            return redirect(url_for("student_profile"))
        
        error = "Invalid credentials"
    
    return render_template("auth/login.html", error=error)

@app.route("/register", methods=["GET", "POST"])
def auth_register():
    error = None
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        role = request.form["role"]
        password = request.form["password"]

        try:
            create_user(name, email, password, role)
        except Exception as e:
            error = "Registration failed: email already exists"
            return render_template("auth/register.html", error=error)
        
        return redirect(url_for("auth_login"))
    
    return render_template("auth/register.html", error=error)

@app.route("/student/profile")
def student_profile():
    user = session.get("user")
    if not user or user.get("role") != "student":
        return redirect(url_for("auth_login"))
    return render_template("student/profile.html", user=user)

@app.route("/log_tab_switch/<student_id>/<exam_id>", methods=["POST"])
def log_tab_switch(student_id, exam_id):
    global proctoring
    
    data = request.get_json() or {}
    switch_count = data.get("switch_count", 0)
    
    if proctoring is not None:
        for i in range(switch_count):
            proctoring.log_violation(
                violation_type="TAB_SWITCH",
                severity="HIGH",
                frame=None,
                details={"switch_number": i + 1, "total_switches": switch_count}
            )
    
    return jsonify({"status": "ok", "logged": switch_count})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth_login"))

@app.route("/register_face/<student_id>", methods=["GET", "POST"])
def register_face_page(student_id):
    user = session.get("user")
    if not user:
        return redirect(url_for("auth_login"))
    
    if user.get("role") != "student" or str(user.get("id")) != str(student_id):
        return redirect(url_for("auth_login"))
    
    if request.method == "POST":
        import base64
        data = request.get_json()
        if not data or "image" not in data:
            return jsonify({"status": "error", "message": "No image provided"})
        
        try:
            image_data = base64.b64decode(data["image"].split(",")[1])
            nparr = np.frombuffer(image_data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            debug_path = os.path.join("known_faces", f"{student_id}_debug.jpg")
            cv2.imwrite(debug_path, frame)
            print(f"Debug image saved to {debug_path}")
            
            from models.face_registration import register_face as do_register_face
            result = do_register_face(student_id, frame)
            
            if result.get("status") == "error" and "No face detected" in result.get("message", ""):
                print("Trying with saved image file...")
                result = do_register_face(student_id, None)
                if result.get("status") == "success":
                    img_path = os.path.join("known_faces", f"{student_id}.jpg")
                    cv2.imwrite(img_path, frame)
            
            return jsonify(result)
        except Exception as e:
            import traceback
            print(f"Registration error: {e}")
            traceback.print_exc()
            return jsonify({"status": "error", "message": str(e)})
    
    from models.face_registration import is_student_registered
    already_registered = is_student_registered(student_id)
    
    return render_template("student/register_face.html", 
                           student_id=student_id, 
                           already_registered=already_registered)

@app.route("/verify_identity/<student_id>", methods=["POST"])
def verify_identity_api(student_id):
    user = session.get("user")
    if not user:
        return jsonify({"verified": False, "status": "NOT_LOGGED_IN"})
    
    if user.get("role") != "student" or str(user.get("id")) != str(student_id):
        return jsonify({"verified": False, "status": "UNAUTHORIZED"})
    
    try:
        import base64
        data = request.get_json()
        if not data or "image" not in data:
            return jsonify({"verified": False, "status": "NO_IMAGE"})
        
        image_data = base64.b64decode(data["image"].split(",")[1])
        nparr = np.frombuffer(image_data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        from models.face_registration import verify_identity
        result = verify_identity(frame, student_id)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"verified": False, "status": "ERROR", "message": str(e)})

@app.route("/check_registration/<student_id>")
def check_registration(student_id):
    from models.face_registration import is_student_registered
    registered = is_student_registered(student_id)
    return jsonify({"registered": registered})

@app.route("/health")
def health_check():
    """Health check endpoint for proctoring."""
    return jsonify({"status": "ok", "proctoring": "active" if proctoring else "inactive"})

@app.route("/admin/metrics")
def admin_metrics():
    """Display evaluation metrics for all detectors."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    # If proctoring is active, get live metrics
    if proctoring is not None:
        metrics = proctoring.get_metrics()
    else:
        # Return default metrics when no active session
        metrics = {
            "session_info": {
                "student_id": "N/A",
                "exam_id": "N/A",
                "start_time": "N/A",
                "duration_seconds": 0,
                "total_frames": 0,
            },
            "detectors": {
                "face_detector": {
                    "enabled": True,
                    "violations_count": 0,
                    "violations": [],
                    "accuracy_estimate": 95,
                    "estimated_latency_ms": 200,
                },
                "gaze_tracker": {
                    "enabled": True,
                    "violations_count": 0,
                    "violations": [],
                    "accuracy_estimate": 90,
                    "estimated_latency_ms": 10,
                },
                "object_detector": {
                    "enabled": True,
                    "violations_count": 0,
                    "violations": [],
                    "accuracy_estimate": 85,
                    "estimated_latency_ms": 20,
                },
                "audio_analyzer": {
                    "enabled": True,
                    "violations_count": 0,
                    "violations": [],
                    "accuracy_estimate": 70,
                    "estimated_latency_ms": 1500,
                }
            },
            "violations": {
                "total": 0,
                "by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
                "by_type": {"face": 0, "gaze": 0, "object": 0, "audio": 0, "tab_switch": 0}
            },
            "performance": {
                "avg_frame_latency_ms": 0,
                "frames_per_second": 0,
                "violation_rate": 0,
            },
            "config": {}
        }
    
    return render_template("admin/metrics.html", metrics=metrics)

@app.route("/admin/metrics/json")
def admin_metrics_json():
    """JSON endpoint for metrics data."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401
    
    if proctoring is not None:
        return jsonify(proctoring.get_metrics())
    else:
        return jsonify({"status": "no_active_session", "message": "No proctoring session active"})

@app.route("/get_warnings")
def get_warnings():
    """JSON endpoint for getting current warnings (gaze tracking) - for student popup warnings."""
    global proctoring
    
    if proctoring is not None:
        # Get current warnings from the last processed frame
        # The warnings are returned in real-time from process_frame
        return jsonify({"status": "ok", "warnings": proctoring.current_warnings if hasattr(proctoring, 'current_warnings') else []})
    else:
        return jsonify({"status": "no_session", "warnings": []})

@app.route("/admin/label_sessions")
def admin_label_sessions():
    """List sessions for labeling."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    logs_root = os.path.join("logs")
    sessions = []
    
    if os.path.isdir(logs_root):
        for name in os.listdir(logs_root):
            if name.startswith("session_"):
                session_dir = os.path.join(logs_root, name)
                data = load_session_log(session_dir)
                if data:
                    # Count labeled
                    labels = get_labels_by_session(name)
                    sessions.append({
                        "dir": name,
                        "data": data,
                        "labeled_count": len(labels),
                        "total_violations": len(data.get("violation_history", []))
                    })
    
    sessions.sort(key=lambda s: s["data"]["session_start"], reverse=True)
    return render_template("admin/label_sessions.html", sessions=sessions)

@app.route("/admin/label_session/<session_dir>")
def admin_label_session(session_dir):
    """Labeling UI for single session."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    data = load_session_log(os.path.join("logs", session_dir))
    labels = get_labels_by_session(session_dir)
    label_dict = {l["violation_timestamp"]: dict(l) for l in labels}
    
    violations = []
    for v in data.get("violation_history", []):
        v["labeled"] = v["timestamp"] in label_dict
        v["human_true"] = label_dict.get(v["timestamp"], {}).get("human_true")
        violations.append(v)
    

    # Unlabeled first
    unlabeled = [v for v in violations if not v["labeled"]]
    labeled = [v for v in violations if v["labeled"]]
    violations = unlabeled + labeled
    
    return render_template("admin/label_session.html", session_dir=session_dir, data=data, violations=violations)
    


@app.route("/admin/save_label", methods=["POST"])
def admin_save_label():
    """Enhanced save human label with confidence/notes."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    
    data = request.get_json()
    session_dir = data["session_dir"]
    timestamp = data["violation_timestamp"]
    violation_type = data["violation_type"]
    human_true = data["human_true"]
    confidence = data.get("confidence")
    notes = data.get("notes", "").strip()
    
    try:
        create_label(
            session_dir=session_dir,
            violation_timestamp=timestamp,
            violation_type=violation_type,
            human_true=human_true,
            confidence=str(int(confidence)) if confidence else None,
            notes=notes,
            labeler_id=int(user["id"])
        )
        return jsonify({"status": "success", "message": "Label saved! FP reduction active."})
    except Exception as e:
        logger.error(f"Label save error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/admin/unlabel/<timestamp>", methods=["POST"])
def admin_unlabel(timestamp):
    """Remove human label."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM labels WHERE violation_timestamp = ?", (timestamp,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"status": "success", "message": "Label removed"})
    except Exception as e:
        logger.error(f"Unlabel error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/admin/bulk_label/<session_dir>", methods=["POST"])
def admin_bulk_label(session_dir):
    """Bulk label all unlabeled as FALSE (low confidence)."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return jsonify({"status": "error"}), 401
    
    data = request.get_json()
    human_true = data.get("human_true", False)
    confidence = data.get("confidence", 20)
    notes = data.get("notes", "Bulk FP rejection - no image evidence/low confidence")
    
    try:
        logs_path = os.path.join("logs", session_dir)
        log_data = load_session_log(logs_path)
        violations = log_data.get("violation_history", [])
        
        labeled_count = 0
        for v in violations:
            if not get_labels_by_session_timestamp(session_dir, v["timestamp"]):  # if unlabeled
                create_label(
                    session_dir=session_dir,
                    violation_timestamp=v["timestamp"],
                    violation_type=v.get("violation_type") or v.get("violation"),
                    human_true=human_true,
                    confidence=str(confidence),
                    notes=notes,
                    labeler_id=int(user["id"])
                )
                labeled_count += 1
        
        return jsonify({"status": "success", "labeled_count": labeled_count})
    except Exception as e:
        logger.error(f"Bulk label error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/admin/metrics_advanced")
def admin_metrics_advanced():
    """Advanced ML metrics from all violation logs and human labels."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))
    
    import pandas as pd
    from sklearn.metrics import confusion_matrix, precision_recall_fscore_support, accuracy_score
    
    # Step 1: Load all violation logs from logs folder
    logs_root = os.path.join("logs")
    all_violations = []
    session_stats = []
    
    if os.path.isdir(logs_root):
        for name in os.listdir(logs_root):
            if not name.startswith("session_"):
                continue
            session_dir = os.path.join(logs_root, name)
            data = load_session_log(session_dir)
            if data and "violation_history" in data:
                violations = data.get("violation_history", [])
                all_violations.extend(violations)
                session_stats.append({
                    "session": name,
                    "total_violations": len(violations),
                    "by_severity": data.get("violation_summary", {}).get("by_severity", {})
                })
    
    # Step 2: Aggregate violation counts from logs
    violation_counts = {}
    severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    
    for v in all_violations:
        vtype = v.get("violation", v.get("violation_type", "UNKNOWN"))
        severity = v.get("severity", "UNKNOWN")
        violation_counts[vtype] = violation_counts.get(vtype, 0) + 1
        if severity in severity_counts:
            severity_counts[severity] += 1
    
    # Step 3: Load human labels from database
    labels = [dict(l) for l in get_all_labels()]
    
    # Step 4: Compute ML metrics from labels
    ml_metrics = {}
    overall_precision = 0.0
    overall_accuracy = 0.0
    
    if labels:
        df = pd.DataFrame(labels)
        if not df.empty and 'violation_type' in df.columns and 'human_true' in df.columns:
            # Overall precision and accuracy
            overall_precision = df['human_true'].mean()
            y_true_all = df['human_true'].astype(int)
            y_pred_all = [1] * len(y_true_all)
            overall_accuracy = accuracy_score(y_true_all, y_pred_all)
            
            for violation_type in df['violation_type'].unique():
                type_labels = df[df['violation_type'] == violation_type]
                if len(type_labels) > 0:
                    y_true = type_labels['human_true'].astype(int)
                    y_pred = [1] * len(y_true)  # All logged as violations
                    
                    cm = confusion_matrix(y_true, y_pred)
                    precision, recall, f1, _ = precision_recall_fscore_support(
                        y_true, y_pred, average='binary', zero_division=0
                    )
                    acc = accuracy_score(y_true, y_pred)
                    
                    # Extract confusion matrix values safely
                    tn = int(cm[0][0]) if len(cm) > 0 and len(cm[0]) > 0 else 0
                    fp = int(cm[0][1]) if len(cm) > 0 and len(cm[0]) > 1 else 0
                    fn = int(cm[1][0]) if len(cm) > 1 and len(cm[1]) > 0 else 0
                    tp = int(cm[1][1]) if len(cm) > 1 and len(cm[1]) > 1 else 0
                    
                    ml_metrics[violation_type] = {
                        'total_labeled': len(type_labels),
                        'true_positives': tp,
                        'false_positives': fp,
                        'true_negatives': tn,
                        'false_negatives': fn,
                        'accuracy': acc,
                        'precision': precision,
                        'recall': recall,
                        'f1': f1,
                        'confusion_matrix': cm.tolist()
                    }
    
    # Step 5: Build comprehensive report
    report = {
        'total_sessions_analyzed': len(session_stats),
        'total_violations_logged': len(all_violations),
        'violation_counts': violation_counts,
        'severity_counts': severity_counts,
        'session_stats': session_stats,
        'ml_metrics': ml_metrics,
        'overall_precision': overall_precision,
        'overall_accuracy': overall_accuracy,
        'total_labels': len(labels),
        'labeled_violation_types': list(ml_metrics.keys())
    }
    
    return render_template("admin/metrics_advanced.html", report=report, metrics=ml_metrics, overall_precision=overall_precision, overall_accuracy=overall_accuracy)

@app.route("/admin/live_monitor")
def admin_live_monitor():
    """Live monitoring dashboard showing all students' exam metrics."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return redirect(url_for("auth_login"))

    # Gather active sessions from cache
    active_sessions = []
    for session_key, cache_entry in live_metrics_cache.items():
        parts = session_key.rsplit("_", 1)
        if len(parts) == 2:
            student_id, exam_id = parts
        else:
            student_id, exam_id = session_key, "unknown"
        active_sessions.append({
            "session_key": session_key,
            "student_id": student_id,
            "exam_id": exam_id,
            "status": cache_entry.get("status", "unknown"),
            "last_updated": cache_entry.get("last_updated", 0),
            "metrics": cache_entry.get("metrics", {})
        })

    # Sort: active first, then by last updated
    active_sessions.sort(key=lambda s: (0 if s["status"] == "active" else 1, -s["last_updated"]))

    # Gather recent completed sessions from logs for context
    recent_logs = []
    logs_root = os.path.join("logs")
    if os.path.isdir(logs_root):
        for name in os.listdir(logs_root):
            if not name.startswith("session_"):
                continue
            session_dir = os.path.join(logs_root, name)
            data = load_session_log(session_dir)
            if data:
                recent_logs.append({
                    "student_id": data.get("student_id"),
                    "exam_id": data.get("exam_id"),
                    "session_start": data.get("session_start"),
                    "session_end": data.get("session_end"),
                    "summary": data.get("violation_summary", {})
                })
    recent_logs.sort(key=lambda s: s.get("session_start", ""), reverse=True)
    recent_logs = recent_logs[:10]

    return render_template("admin/live_monitor.html",
                           active_sessions=active_sessions,
                           recent_logs=recent_logs)

@app.route("/admin/live_monitor/json")
def admin_live_monitor_json():
    """JSON endpoint for live monitor AJAX polling."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 401

    sessions = []
    for session_key, cache_entry in live_metrics_cache.items():
        parts = session_key.rsplit("_", 1)
        if len(parts) == 2:
            student_id, exam_id = parts
        else:
            student_id, exam_id = session_key, "unknown"
        sessions.append({
            "session_key": session_key,
            "student_id": student_id,
            "exam_id": exam_id,
            "status": cache_entry.get("status", "unknown"),
            "last_updated": cache_entry.get("last_updated", 0),
            "metrics": cache_entry.get("metrics", {})
        })

    sessions.sort(key=lambda s: (0 if s["status"] == "active" else 1, -s["last_updated"]))
    return jsonify({"sessions": sessions, "count": len(sessions)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
