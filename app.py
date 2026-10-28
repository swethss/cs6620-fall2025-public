import os
import re
import csv
from io import StringIO
from flask import Flask, render_template, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from pydub import AudioSegment
import tempfile
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Global variables for playlist management
current_directory = None
current_playlist = [] # Stores full paths on server
audio_file_map = {}  # Maps filename to full path for nested directories
SUPPORTED_AUDIO_EXTENSIONS = ('.mp3', '.wav', '.ogg')

# Global variable to store parsed log data
parsed_transcription_data = {}

# Global variables for CSV error labeling
csv_error_data = []
csv_file_loaded = False

def time_to_seconds(time_str):
    """Convert HH:MM:SS format to seconds"""
    try:
        if ':' in time_str:
            parts = time_str.split(':')
            if len(parts) == 3:
                hours, minutes, seconds = map(float, parts)
                return hours * 3600 + minutes * 60 + seconds
            elif len(parts) == 2:
                minutes, seconds = map(float, parts)
                return minutes * 60 + seconds
        return float(time_str)  # Try direct conversion as fallback
    except (ValueError, AttributeError):
        return 0.0

def parse_log_content(log_content):
    """
    Parses the log content to extract error segments for each audio file.
    Only supports the new CSV/TSV format as in sample_log.txt.
    Returns a dict mapping audio file basenames to a list of error segments.
    """
    data = {}
    # Auto-detect delimiter: use tab if more tabs than commas, else comma
    tab_count = log_content.count('\t')
    comma_count = log_content.count(',')
    delimiter = '\t' if tab_count > comma_count else ','
    reader = csv.reader(StringIO(log_content), delimiter=delimiter)
    header = next(reader, None)
    # Find relevant column indices
    def col(name):
        try:
            return header.index(name)
        except ValueError:
            return None
    col_audio = col('transcriptFile')
    col_long_start = col('longFormStart')
    col_long_end = col('longFormEnd')
    col_long_error = col('longFormError')
    col_short_error = col('shortFormError')
    col_short_start = col('shortFormStart')
    col_short_end = col('shortFormEnd')
    for row in reader:
        if not row or len(row) <= max(filter(None, [col_audio, col_long_start, col_long_end, col_long_error, col_short_error, col_short_start, col_short_end])):
            continue
        audio_path = row[col_audio]
        filename = os.path.basename(audio_path)
        try:
            long_start = float(row[col_long_start]) if col_long_start is not None else None
            long_end = float(row[col_long_end]) if col_long_end is not None else None
            short_start = float(row[col_short_start]) if col_short_start is not None and row[col_short_start] else None
            short_end = float(row[col_short_end]) if col_short_end is not None and row[col_short_end] else None
        except (ValueError, IndexError):
            long_start = long_end = short_start = short_end = None
        segment = {
            'longFormStart': long_start,
            'longFormEnd': long_end,
            'longFormError': row[col_long_error] if col_long_error is not None else '',
            'shortFormError': row[col_short_error] if col_short_error is not None else '',
            'shortFormStart': short_start,
            'shortFormEnd': short_end
        }
        if filename not in data:
            data[filename] = []
        data[filename].append(segment)
    return data

@app.route('/audio_files/<path:filename>')
def serve_audio_file(filename):
    """
    Serves audio files directly to the client's web browser.
    @app.after_request
def add_deployment_banner(response):
    """Add a visible deployment banner without replacing the existing UI."""
    if request.path == '/' and response.status_code == 200:
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' in content_type:
            try:
                html = response.get_data(as_text=True)
                build_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                banner = (
                    '<div id="deployment-assignment-banner" '
                    'style="font-family:Arial,sans-serif;background:#eef6ff;'
                    'color:#12345a;border:1px solid #8ab4e8;padding:12px 16px;'
                    'margin:12px;border-radius:8px;">'
                    '<h2 style="margin:0 0 8px 0;">'
                    'Hello from Automated CI/CD Pipeline!</h2>'
                    '<p><strong>Version:</strong> 2.0 - Automated Deployment</p>'
                    '<p><strong>Deployed via:</strong> GitHub Actions + AWS SSM</p>'
                    f'<p><strong>Build Date:</strong> {build_date}</p>'
                    '<p><strong>Assignment:</strong> Automated EC2 Deployment</p>'
                    '</div>'
                )
                import re
                if re.search(r'</body>', html, flags=re.IGNORECASE):
                    html = re.sub(r'</body>', banner + '</body>', html,
                                  count=1, flags=re.IGNORECASE)
                else:
                    html = banner + html
                response.set_data(html)
            except Exception:
                app.logger.exception('Could not add deployment banner')
    return response


@app.route('/select_directory', methods=['POST'])
def select_directory():
    """
    Handles the selection of an audio directory and populates the playlist.
    Now also includes transcription data if a log file has been uploaded/loaded.
    """
    global current_directory, current_playlist, audio_file_map

    directory_path = request.form.get('directory_path')

    if not directory_path:
        return jsonify({"success": False, "message": "Please enter a directory path."})

    if not os.path.isdir(directory_path):
        return jsonify({"success": False, "message": "Invalid directory path."})

    current_directory = directory_path
    current_playlist = []
    audio_file_map = {}
    
    # Recursively load all audio files from the directory and subdirectories
    for root, _, files in os.walk(directory_path):
        for filename in files:
            if filename.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
                full_path = os.path.join(root, filename)
                current_playlist.append(full_path)
                # Map just the filename to the full path for CSV lookups
                audio_file_map[filename] = full_path
                # Also map filename without extension for CSV compatibility
                # (CSV has "ac083_2008-04-06" but file is "ac083_2008-04-06.mp3")
                name_without_ext = os.path.splitext(filename)[0]
                audio_file_map[name_without_ext] = full_path
    
    current_playlist.sort()
    
    files_with_transcription_info = []

    for f_path in current_playlist:
        f_name = os.path.basename(f_path)
        file_url = f'/audio_files/{f_name}'
        error_segments = parsed_transcription_data.get(f_name, [])
        files_with_transcription_info.append({
            "name": f_name,
            "url": file_url,
            "error_segments": error_segments
        })

    return jsonify({
        "success": True,
        "message": f"Directory selected: {directory_path}",
        "files_with_info": files_with_transcription_info
    })

@app.route('/upload_log', methods=['POST'])
def upload_log():
    """
        if current_directory:
        for f_path in current_playlist:
            f_name = os.path.basename(f_path)
            file_url = f'/audio_files/{f_name}'
            error_segments = parsed_transcription_data.get(f_name, [])
            files_with_transcription_info.append({
                "name": f_name,
                "url": file_url,
                "error_segments": error_segments
            })

    return jsonify({
        "currentDirectory": current_directory,
        "files_with_info": files_with_transcription_info,
        "logLoaded": bool(parsed_transcription_data),
        "csvLoaded": csv_file_loaded,
        "csvRecordCount": len(csv_error_data)
    })


# Error labeling routes

@app.route('/labeling')
def labeling():
    """Serve the labeling interface"""
    return render_template('labeling.html')


@app.route('/load_csv', methods=['POST'])
def load_csv():
    """Load CSV error data from file upload or path"""
    global csv_error_data, csv_file_loaded
    
    try:
        csv_content = None
        
        # Check if file was uploaded
        if 'csv_file' in request.files:
            file = request.files['csv_file']
            if file and file.filename:
                csv_content = file.read().decode('utf-8')
        
        # Check if path was provided
        elif 'csv_path' in request.form:
            csv_path = request.form['csv_path'].strip()
            if csv_path and os.path.exists(csv_path):
                with open(csv_path, 'r', encoding='utf-8') as f:
                    csv_content = f.read()
            else:
                return jsonify({"success": False, "message": f"CSV file not found at path: {csv_path}"})
        
        if not csv_content:
            return jsonify({"success": False, "message": "No CSV file provided"})
        
        # Parse CSV content
        csv_error_data = []
        reader = csv.DictReader(StringIO(csv_content))
        
        for row in reader:
            # Map actual CSV columns to expected format
            if all(col in row for col in ['recordErrorID', 'recordFile', 'exampleExample', 'recordTime']):
                # Skip rows where recordTime is empty or invalid
                try:
                    record_time = time_to_seconds(row['recordTime']) if row['recordTime'].strip() else 0.0
                    record_file = row['recordFile'].strip()
                    
                    # Debug logging
                    app.logger.info(f"Processing CSV row: ID={row['recordErrorID']}, File='{record_file}', Time={record_time}")
                    
                    # Skip rows with empty filenames
                    if not record_file:
                        app.logger.warning(f"Skipping row {row['recordErrorID']} - empty filename")
                        continue
                    
                    csv_error_data.append({
                        'record_id': row['recordErrorID'],
                        'record_file': record_file,
                        'example_phrase': row['exampleExample'],
                        'record_time': record_time
                    })
                        'audio_file': data['audio_file'],
            'error_phrase': error_phrase,
            'start_time': round(data['start_time'], 3),
            'end_time': round(data['end_time'], 3),
            'duration': round(data['end_time'] - data['start_time'], 3),
            'labeled_at': __import__('datetime').datetime.now().isoformat()
        }
        
        # Save to CSV file
        output_file = "/opt/data/labeled-segments.csv"
        
        # Check if file exists to determine if we need to write headers
        file_exists = os.path.exists(output_file)
        
        # Ensure the directory exists
        os.makedirs("/opt/data", exist_ok=True)
        
        with open(output_file, 'a', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['record_id', 'audio_file', 'error_phrase', 'start_time', 'end_time', 'duration', 'labeled_at']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # Write header if file is new
            if not file_exists:
                writer.writeheader()
            
            # Write the labeled data
            writer.writerow(label_data)
        
        app.logger.info(f"Label saved to {output_file}: Record {data['record_id']}, "
                       f"Time: {data['start_time']:.3f}-{data['end_time']:.3f}s, "
                       f"File: {data['audio_file']}")
        
        return jsonify({
            "success": True,
            "message": f"Label saved for record {data['record_id']} to {output_file}"
        })
        
    except Exception as e:
        app.logger.error(f"Error saving label: {e}")
        return jsonify({"success": False, "message": f"Error saving label: {str(e)}"})


@app.route('/download_labels')
def download_labels():
    """Download the labeled segments CSV file"""
    output_file = "/opt/data/labeled-segments.csv"
    
    if os.path.exists(output_file):
        return send_file(output_file, as_attachment=True, download_name="labeled-segments.csv")
    else:
        return "No labeled segments file found", 404


@app.route('/view_labels')
def view_labels():
    """View the labeled segments as JSON for display in the interface"""
    output_file = "/opt/data/labeled-segments.csv"
    
    if not os.path.exists(output_file):
        return jsonify({"success": False, "message": "No labeled segments file found", "data": []})
    
    try:
        labeled_segments = []
        with open(output_file, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                # Ensure all expected fields exist with defaults
                segment = {
                    'record_id': row.get('record_id', ''),
                    'audio_file': row.get('audio_file', ''),
                    'error_phrase': row.get('error_phrase', ''),  # Handle missing column
                    'start_time': row.get('start_time', '0'),
                    'end_time': row.get('end_time', '0'),
                    'duration': row.get('duration', '0'),
                    'labeled_at': row.get('labeled_at', '')
                }
                labeled_segments.append(segment)
        
        return jsonify({
            "success": True,
            "message": f"Found {len(labeled_segments)} labeled segments",
            "data": labeled_segments
        })
except Exception as e:
        return jsonify({"success": False, "message": f"Error reading labels file: {str(e)}", "data": []})


@app.route('/delete_labels', methods=['POST'])
def delete_labels():
    """Delete the labeled segments file"""
    output_file = "/opt/data/labeled-segments.csv"
    
    try:
        if os.path.exists(output_file):
            os.remove(output_file)
            return jsonify({"success": True, "message": "All labeled segments deleted successfully"})
        else:
            return jsonify({"success": False, "message": "No labeled segments file found to delete"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error deleting labels file: {str(e)}"})


# Auto-load CSV and audio files on startup
def auto_load_data():
    """Try to auto-load CSV and audio files from default locations"""
    global csv_error_data, csv_file_loaded, current_directory, current_playlist, audio_file_map
    
    # Auto-load audio directory
    audio_path = "/opt/audio"
    if os.path.exists(audio_path) and os.path.isdir(audio_path):
        try:
            current_directory = audio_path
            current_playlist = []
            audio_file_map = {}
            
            # Recursively load all audio files
            for root, _, files in os.walk(audio_path):
                for filename in files:
                    if filename.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
                        full_path = os.path.join(root, filename)
                        current_playlist.append(full_path)
                        # Map full filename and filename without extension
                        audio_file_map[filename] = full_path
                        name_without_ext = os.path.splitext(filename)[0]
                        audio_file_map[name_without_ext] = full_path
            
            current_playlist.sort()
            app.logger.info(f"Auto-loaded {len(current_playlist)} audio files from {audio_path}")
            
        except Exception as e:
            app.logger.error(f"Failed to auto-load audio files: {e}")
    
    # Auto-load CSV file - try both possible names
    csv_path = "/opt/data/err-dataset-orig.csv"
    if not os.path.exists(csv_path):
        csv_path = "/opt/data/err-dataset.csv"
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                csv_content = f.read()
            
            csv_error_data = []
            reader = csv.DictReader(StringIO(csv_content))
            
            for row in reader:
                if all(col in row for col in ['recordErrorID', 'recordFile', 'exampleExample', 'recordTime']):
                    try:
                        record_time = time_to_seconds(row['recordTime']) if row['recordTime'].strip() else 0.0
                        record_file = row['recordFile'].strip()
                        
                        if record_file:  # Skip empty filenames
                            csv_error_data.append({
                                'record_id': row['recordErrorID'],
                                'record_file': record_file,
                                'example_phrase': row['exampleExample'],
                                'record_time': record_time
                            })
                    except (ValueError, AttributeError):
                        continue  # Skip rows with invalid time values
            
            csv_file_loaded = True
            app.logger.info(f"Auto-loaded CSV with {len(csv_error_data)} error records")
            
        except Exception as e:
            app.logger.error(f"Failed to auto-load CSV: {e}")


if __name__ == '__main__':
    # Auto-load CSV and audio files on startup
    auto_load_data()
    app.run(debug=True, host='0.0.0.0', port=5000)
