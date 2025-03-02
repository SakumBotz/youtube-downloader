from flask import Flask, render_template, request, send_file, jsonify
from flask_socketio import SocketIO
import yt_dlp
import os
import threading
import time
import uuid

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

progress_status = {}
downloaded_files = {}
progress_lock = threading.Lock()

def generate_client_id():
    """Membuat client ID unik"""
    return str(uuid.uuid4())

def clean_old_files():
    """Menghapus file setelah 10 detik"""
    while True:
        now = time.time()
        with progress_lock:
            for client_id, filename in list(downloaded_files.items()):
                file_path = os.path.join(DOWNLOAD_FOLDER, filename)
                if os.path.isfile(file_path) and now - os.path.getctime(file_path) > 30:  # Ubah 30 ke 10
                    os.remove(file_path)
                    del downloaded_files[client_id]
                    print(f"File {filename} dihapus otomatis.")

        time.sleep(10)  # Cek setiap 10 detik

def update_progress(data, client_id):
    """Update progress berdasarkan informasi dari yt_dlp."""
    if data['status'] == 'downloading':
        downloaded = data.get('downloaded_bytes', 0)
        total = data.get('total_bytes', data.get('total_bytes_estimate', 1))
        progress = (downloaded / total) * 100 if total > 0 else 0
        progress = min(progress, 100)
        
        with progress_lock:
            progress_status[client_id] = round(progress, 2)

        socketio.emit('progress_update', {'client_id': client_id, 'progress': round(progress, 2)})
        socketio.sleep(0.1)
    elif data['status'] == 'finished':
        with progress_lock:
            progress_status[client_id] = 100
        socketio.emit('progress_update', {'client_id': client_id, 'progress': 100})

def download_video(url, format_option, quality, client_id):
    """Proses download video/audio dengan yt_dlp."""
    quality_map = {
        "144": "bestvideo[height<=144]+bestaudio/best",
        "240": "bestvideo[height<=240]+bestaudio/best",
        "360": "bestvideo[height<=360]+bestaudio/best",
        "480": "bestvideo[height<=480]+bestaudio/best",
        "720": "bestvideo[height<=720]+bestaudio/best",
        "1080": "bestvideo[height<=1080]+bestaudio/best",
        "1440": "bestvideo[height<=1440]+bestaudio/best",
        "2160": "bestvideo[height<=2160]+bestaudio/best",
        "4320": "bestvideo[height<=4320]+bestaudio/best",
    }
    selected_format = quality_map.get(quality, "bestvideo+bestaudio/best")

    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s'),
        'progress_hooks': [lambda d: update_progress(d, client_id)]
    }

    if format_option == "video":
        ydl_opts.update({
            'format': selected_format,
            'merge_output_format': 'mp4'
        })
    else:  # Audio
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '192'
            }]
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if format_option == "audio":
                filename = filename.rsplit('.', 1)[0] + ".m4a"

            if filename and os.path.exists(filename):
                with progress_lock:
                    progress_status[client_id] = 100
                    downloaded_files[client_id] = os.path.basename(filename)
                
                socketio.emit('download_complete', {'client_id': client_id, 'filename': os.path.basename(filename)})
                return filename
            else:
                print(f"File tidak ditemukan setelah download: {filename}")
    except Exception as e:
        print(f"Error saat mendownload: {e}")
    
    with progress_lock:
        progress_status[client_id] = -1
    socketio.emit('download_failed', {'client_id': client_id})
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    url = request.form['url']
    format_option = request.form['format']
    quality = request.form.get('quality', 'best')
    client_id = generate_client_id()
    
    thread = threading.Thread(target=download_video, args=(url, format_option, quality, client_id))
    thread.start()

    return jsonify({"status": "download started", "client_id": client_id})

@app.route('/get_filename', methods=['GET'])
def get_filename():
    client_id = request.args.get("client_id")
    if not client_id:
        return jsonify({"error": "Client ID is required"}), 400
    
    with progress_lock:
        filename = downloaded_files.get(client_id)
    
    if filename:
        return jsonify({"filename": filename})
    return jsonify({"error": "File not found"}), 404

@app.route('/download_file')
def download_file():
    filename = request.args.get('filename')
    if not filename:
        return "Filename is required", 400
    
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "File not found", 404

@app.route('/get_available_qualities', methods=['GET'])
def get_available_qualities():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "URL is required"}), 400

    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            available_qualities = []

            if 'formats' in info:
                for fmt in info['formats']:
                    if 'height' in fmt and fmt['height']:
                        available_qualities.append(str(fmt['height']))

            return jsonify({"qualities": sorted(set(available_qualities))})
    except Exception as e:
        print(f"Error saat mengambil kualitas video: {e}")
        return jsonify({"error": "Failed to fetch qualities"}), 500

if __name__ == '__main__':
    # Jalankan thread untuk auto clean file lama
    threading.Thread(target=clean_old_files, daemon=True).start()

    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False)
