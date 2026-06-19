# Script to add face registration routes to app.py
import re

# Read app.py
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Check if routes already exist
if 'register_face' in content:
    print("Routes already exist!")
else:
    # Add the routes before the last route (or at end)
    routes = '''

# ===============================
# Face Registration & Verification Routes
# ===============================

@app.route('/register_face/<student_id>', methods=['POST'])
def register_face(student_id):
    """Register a student's face from browser capture"""
    try:
        data = request.get_json()
        if not data or 'image' not in data:
            return jsonify({"status": "error", "message": "No image provided"}), 400
        
        # Decode base64 image
        import base64
        image_data = data['image'].split(',')[1]  # Remove data:image/jpeg;base64, prefix
        img_bytes = base64.b64decode(image_data)
        
        # Convert to numpy array
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            return jsonify({"status": "error", "message": "Failed to decode image"}), 400
        
        # Get face encoding using DeepFace
        from models.face_registration import get_face_encoding, load_encodings, save_encodings
        
        face_encoding = get_face_encoding(frame)
        
        if face_encoding is None:
            return jsonify({"status": "error", "message": "No face detected in image"}), 400
        
        # Save encoding
        encodings_db = load_encodings()
        encodings_db[student_id] = {
            "encoding": face_encoding,
            "registered_at": datetime.now().isoformat()
        }
        save_encodings(encodings_db)
        
        # Save face image
        img_path = os.path.join("known_faces", f"{student_id}.jpg")
        cv2.imwrite(img_path, frame)
        
        return jsonify({"status": "success", "message": f"Face registered for {student_id}"})
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/verify_identity', methods=['POST'])
def verify_identity():
    """Verify student identity from browser capture"""
    try:
        data = request.get_json()
        if not data or 'image' not in data or 'student_id' not in data:
            return jsonify({"status": "error", "message": "Missing image or student_id"}), 400
        
        student_id = data['student_id']
        
        # Decode base64 image
        import base64
        image_data = data['image'].split(',')[1]
        img_bytes = base64.b64decode(image_data)
        
        # Convert to numpy array
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            return jsonify({"verified": False, "status": "DECODE_ERROR", "message": "Failed to decode image"})
        
        # Verify identity
        from models.face_registration import verify_identity
        result = verify_identity(frame, student_id)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"verified": False, "status": "ERROR", "message": str(e)}), 500


@app.route('/check_registration/<student_id>', methods=['GET'])
def check_registration(student_id):
    """Check if a student has registered their face"""
    try:
        from models.face_registration import is_student_registered
        registered = is_student_registered(student_id)
        return jsonify({"registered": registered})
    except Exception as e:
        return jsonify({"registered": False, "error": str(e)}), 500


# ===============================
# End Face Registration Routes
# ===============================
'''
    
    # Find a good place to insert - before the last few lines or after imports
    # Let's add after the last @app.route but before if __name__ == '__main__':
    if "__name__" in content:
        content = content.replace("if __name__", routes + "\nif __name__")
    else:
        content = content + routes
    
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("Routes added successfully!")
