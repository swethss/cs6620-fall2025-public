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
current_playlist = []
audio_file_map = {}
SUPPORTED_AUDIO_EXTENSIONS = ('.mp3', '.wav', '.ogg')

# Global variable to store parsed log data
parsed_transcription_data = {}

# Global variables for CSV error labeling
csv_error_data = []
csv_file_loaded = False


def time_to_seconds(time_str):
    """Convert HH:MM:SS or MM:SS format to seconds."""
    try:
        if ':' in time_str:
            parts = time_str.split(':')
            if len(parts) == 3:
                hours, minutes, seconds = map(float, parts)
                return hours * 3600 + minutes * 60 + seconds
            elif len(parts) == 2:
                minutes, seconds = map(float, parts)
                return minutes * 60 + seconds
        return float(time_str)
    except (ValueError, AttributeError):
        return 0.0


def parse_log_content(log_content):
    """Parse a CSV/TSV transcription log into audio-file error segments."""
    data = {}
    tab_count = log_content.count('\t')
    comma_count = log_content.count(',')
    delimiter = '\t' if tab_count > comma_count else ','
    reader = csv.reader(StringIO(log_content), delimiter=delimiter)
    header = next(reader, None)

    if not header:
        return data

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

    required_indices = [
        col_audio, col_long_start, col_long_end, col_long_error,
        col_short_error, col_short_start, col_short_end
    ]
    valid_indices = [i for i in required_indices if i is not None]

    if col_audio is None or not valid_indices:
        return data

    for row in reader:
        if not row or len(row) <= max(valid_indices):
            continue

        audio_path = row[col_audio]
        filename = os.path.basename(audio_path)

        try:
            long_start = float(row[col_long_start]) if col_long_start is not None and row[col_long_start] else None
            long_end = float(row[col_long_end]) if col_long_end is not None and row[col_long_end] else None
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
    """Serve audio files, including files in nested directories."""
    if filename in audio_file_map:
        file_path = audio_file_map[filename]
        return send_from_directory(
            os.path.dirname(file_path),
            os.path.basename(file_path)
        )

    if current_directory and os.path.exists(os.path.join(current_directory, filename)):
        return send_from_directory(current_directory, filename)

    return "File not found", 404


@app.route('/audio_segment')
def serve_audio_segment():
    """Serve an audio segment with five seconds of padding on each side."""
    filename = request.args.get('file')
    start = request.args.get('start', type=float)
    end = request.args.get('end', type=float)

    if not filename or start is None or end is None:
        return "Missing parameters", 400

    if not current_directory:
        return "No directory selected", 400

    audio_path = audio_file_map.get(
        filename, os.path.join(current_directory, filename)
    )

    if not os.path.exists(audio_path):
        return "Audio file not found", 404

    try:
        audio = AudioSegment.from_file(audio_path)
        duration = len(audio) / 1000.0
        seg_start = max(0, start - 5)
        seg_end = min(duration, end + 5)
        seg_audio = audio[int(seg_start * 1000):int(seg_end * 1000)]

        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmpf:
            temp_path = tmpf.name

        seg_audio.export(temp_path, format='wav')
        return send_file(
            temp_path,
            mimetype='audio/wav',
            as_attachment=False,
            download_name=f'{filename}_segment.wav'
        )

    except Exception as e:
        app.logger.error(f"Error extracting audio segment: {e}")
        return f"Error extracting audio segment: {str(e)}", 500


@app.route('/')
def index():
    """Render the main audio-player page."""
    return render_template('index.html')


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for automated deployment verification."""
    return jsonify({
        "status": "healthy",
        "version": "2.0",
        "deployment_method": "GitHub Actions + AWS SSM",
        "timestamp": datetime.now().isoformat()
    })

@app.after_request
def add_deployment_banner(response):
    """Add a deployment banner to the main HTML page."""
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

                if re.search(r'</body>', html, flags=re.IGNORECASE):
                    html = re.sub(
                        r'</body>',
                        banner + '</body>',
                        html,
                        count=1,
                        flags=re.IGNORECASE
                    )
                else:
                    html = banner + html

                response.set_data(html)

            except Exception:
                app.logger.exception('Could not add deployment banner')

    return response


@app.route('/select_directory', methods=['POST'])
def select_directory():
    """Select an audio directory and build the playlist."""
    global current_directory, current_playlist, audio_file_map

    directory_path = request.form.get('directory_path')

    if not directory_path:
        return jsonify({
            "success": False,
            "message": "Please enter a directory path."
        })

    if not os.path.isdir(directory_path):
        return jsonify({
            "success": False,
            "message": "Invalid directory path."
        })

    current_directory = directory_path
    current_playlist = []
    audio_file_map = {}

    for root, _, files in os.walk(directory_path):
        for filename in files:
            if filename.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
                full_path = os.path.join(root, filename)
                current_playlist.append(full_path)

                audio_file_map[filename] = full_path
                name_without_ext = os.path.splitext(filename)[0]
                audio_file_map[name_without_ext] = full_path

    current_playlist.sort()
    files_with_transcription_info = []

    for f_path in current_playlist:
        f_name = os.path.basename(f_path)
        files_with_transcription_info.append({
            "name": f_name,
            "url": f'/audio_files/{f_name}',
            "error_segments": parsed_transcription_data.get(f_name, [])
        })

    return jsonify({
        "success": True,
        "message": f"Directory selected: {directory_path}",
        "files_with_info": files_with_transcription_info
    })


@app.route('/upload_log', methods=['POST'])
def upload_log():
    """Upload and parse a transcription log."""
    global parsed_transcription_data

    log_content = None

    if 'log_file' in request.files:
        uploaded_file = request.files['log_file']
        if uploaded_file.filename:
            log_content = uploaded_file.read().decode('utf-8')

    if not log_content:
        return jsonify({
            "success": False,
            "message": "No log file provided."
        })

    try:
        parsed_transcription_data = parse_log_content(log_content)

        return jsonify({
            "success": True,
            "message": (
                "Log file uploaded and parsed successfully. "
                f"{len(parsed_transcription_data)} entries found."
            )
        })

    except Exception as e:
        app.logger.error(f"Error parsing uploaded log file: {e}")
        return jsonify({
            "success": False,
            "message": f"Error parsing uploaded log file: {str(e)}"
        })


@app.route('/load_log_from_path', methods=['POST'])
def load_log_from_path():
    """Load and parse a transcription log from a server-side path."""
    global parsed_transcription_data

    log_file_path = request.form.get('log_path')

    if not log_file_path:
        return jsonify({
            "success": False,
            "message": "No log file path provided."
        })

    if not os.path.exists(log_file_path):
        return jsonify({
            "success": False,
            "message": f"Log file not found at path: {log_file_path}"
        })

    if not os.path.isfile(log_file_path):
        return jsonify({
            "success": False,
            "message": f"Provided path is not a file: {log_file_path}"
        })

    try:
        with open(log_file_path, 'r', encoding='utf-8') as f:
            log_content = f.read()

        parsed_transcription_data = parse_log_content(log_content)

        return jsonify({
            "success": True,
            "message": (
                "Log file loaded from path and parsed successfully. "
                f"{len(parsed_transcription_data)} entries found."
            )
        })

    except Exception as e:
        app.logger.error(
            f"Error loading or parsing log file from path "
            f"'{log_file_path}': {e}"
        )
        return jsonify({
            "success": False,
            "message": f"Error loading or parsing log file: {str(e)}"
        })


@app.route('/status', methods=['GET'])
def get_status():
    """Return playlist, transcription, and CSV status information."""
    files_with_transcription_info = []

    if current_directory:
        for f_path in current_playlist:
            f_name = os.path.basename(f_path)
            files_with_transcription_info.append({
                "name": f_name,
                "url": f'/audio_files/{f_name}',
                "error_segments": parsed_transcription_data.get(f_name, [])
            })

    return jsonify({
        "currentDirectory": current_directory,
        "files_with_info": files_with_transcription_info,
        "logLoaded": bool(parsed_transcription_data),
        "csvLoaded": csv_file_loaded,
        "csvRecordCount": len(csv_error_data)
    })

@app.route('/labeling')
def labeling():
    """Serve the labeling interface."""
    return render_template('labeling.html')


@app.route('/load_csv', methods=['POST'])
def load_csv():
    """Load CSV error data from an uploaded file or server-side path."""
    global csv_error_data, csv_file_loaded

    try:
        csv_content = None

        if 'csv_file' in request.files:
            file = request.files['csv_file']
            if file and file.filename:
                csv_content = file.read().decode('utf-8')

        elif 'csv_path' in request.form:
            csv_path = request.form['csv_path'].strip()

            if csv_path and os.path.exists(csv_path):
                with open(csv_path, 'r', encoding='utf-8') as f:
                    csv_content = f.read()
            else:
                return jsonify({
                    "success": False,
                    "message": f"CSV file not found at path: {csv_path}"
                })

        if not csv_content:
            return jsonify({
                "success": False,
                "message": "No CSV file provided"
            })

        csv_error_data = []
        reader = csv.DictReader(StringIO(csv_content))

        for row in reader:
            required_columns = [
                'recordErrorID',
                'recordFile',
                'exampleExample',
                'recordTime'
            ]

            if not all(col in row for col in required_columns):
                continue

            try:
                record_time_text = row['recordTime'] or ''
                record_time = (
                    time_to_seconds(record_time_text)
                    if record_time_text.strip()
                    else 0.0
                )
                record_file = (row['recordFile'] or '').strip()

                app.logger.info(
                    f"Processing CSV row: ID={row['recordErrorID']}, "
                    f"File='{record_file}', Time={record_time}"
                )

                if not record_file:
                    app.logger.warning(
                        f"Skipping row {row['recordErrorID']} - empty filename"
                    )
                    continue

                csv_error_data.append({
                    'record_id': row['recordErrorID'],
                    'record_file': record_file,
                    'example_phrase': row['exampleExample'],
                    'record_time': record_time
                })

            except (ValueError, AttributeError) as e:
                app.logger.warning(f"Skipping invalid row: {e}")

        csv_file_loaded = True

        return jsonify({
            "success": True,
            "message": (
                f"CSV loaded successfully. "
                f"{len(csv_error_data)} error records found."
            ),
            "record_count": len(csv_error_data)
        })

    except Exception as e:
        app.logger.exception("Error loading CSV")
        return jsonify({
            "success": False,
            "message": f"Error loading CSV: {str(e)}"
        })


@app.route('/get_csv_data', methods=['GET'])
def get_csv_data():
    """Return the loaded CSV error data."""
    if not csv_file_loaded:
        return jsonify({
            "success": False,
            "message": "No CSV data loaded"
        })

    return jsonify({
        "success": True,
        "data": csv_error_data,
        "count": len(csv_error_data)
    })


@app.route('/save_label', methods=['POST'])
def save_label():
    """Save a labeled error region to a CSV file."""
    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "success": False,
                "message": "A valid JSON request body is required"
            }), 400

        required_fields = [
            'record_id',
            'start_time',
            'end_time',
            'audio_file'
        ]

        if not all(key in data for key in required_fields):
            return jsonify({
                "success": False,
                "message": "Missing required fields"
            }), 400

        try:
            start_time = float(data['start_time'])
            end_time = float(data['end_time'])
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "message": "Start and end times must be numbers"
            }), 400

        if start_time < 0 or end_time < start_time:
            return jsonify({
                "success": False,
                "message": "Invalid start or end time"
            }), 400

        error_phrase = ""

        for error_record in csv_error_data:
            if str(error_record['record_id']) == str(data['record_id']):
                error_phrase = error_record['example_phrase']
                break

        label_data = {
            'record_id': data['record_id'],
            'audio_file': data['audio_file'],
            'error_phrase': error_phrase,
            'start_time': round(start_time, 3),
            'end_time': round(end_time, 3),
            'duration': round(end_time - start_time, 3),
            'labeled_at': datetime.now().isoformat()
        }

        output_file = "/opt/data/labeled-segments.csv"
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        file_exists = (
            os.path.exists(output_file)
            and os.path.getsize(output_file) > 0
        )

        fieldnames = [
            'record_id',
            'audio_file',
            'error_phrase',
            'start_time',
            'end_time',
            'duration',
            'labeled_at'
        ]

        with open(
            output_file, 'a', newline='', encoding='utf-8'
        ) as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            if not file_exists:
                writer.writeheader()

            writer.writerow(label_data)

        app.logger.info(
            f"Label saved to {output_file}: "
            f"Record {data['record_id']}, "
            f"Time: {start_time:.3f}-{end_time:.3f}s, "
            f"File: {data['audio_file']}"
        )

        return jsonify({
            "success": True,
            "message": (
                f"Label saved for record {data['record_id']} "
                f"to {output_file}"
            )
        })

    except Exception as e:
        app.logger.exception("Error saving label")
        return jsonify({
            "success": False,
            "message": f"Error saving label: {str(e)}"
        })

@app.route('/download_labels')
def download_labels():
    """Download the labeled segments CSV file."""
    output_file = "/opt/data/labeled-segments.csv"

    if os.path.exists(output_file):
        return send_file(
            output_file,
            as_attachment=True,
            download_name="labeled-segments.csv"
        )

    return "No labeled segments file found", 404


@app.route('/view_labels')
def view_labels():
    """Return labeled segments as JSON."""
    output_file = "/opt/data/labeled-segments.csv"

    if not os.path.exists(output_file):
        return jsonify({
            "success": False,
            "message": "No labeled segments file found",
            "data": []
        })

    try:
        labeled_segments = []

        with open(output_file, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)

            for row in reader:
                segment = {
                    'record_id': row.get('record_id', ''),
                    'audio_file': row.get('audio_file', ''),
                    'error_phrase': row.get('error_phrase', ''),
                    'start_time': row.get('start_time', '0'),
                    'end_time': row.get('end_time', '0'),
                    'duration': row.get('duration', '0'),
                    'labeled_at': row.get('labeled_at', '')
                }

                labeled_segments.append(segment)

        return jsonify({
            "success": True,
            "message": (
                f"Found {len(labeled_segments)} labeled segments"
            ),
            "data": labeled_segments
        })

    except Exception as e:
        app.logger.exception("Error reading labels file")
        return jsonify({
            "success": False,
            "message": f"Error reading labels file: {str(e)}",
            "data": []
        })


@app.route('/delete_labels', methods=['POST'])
def delete_labels():
    """Delete the labeled segments CSV file."""
    output_file = "/opt/data/labeled-segments.csv"

    try:
        if os.path.exists(output_file):
            os.remove(output_file)

            return jsonify({
                "success": True,
                "message": "All labeled segments deleted successfully"
            })

        return jsonify({
            "success": False,
            "message": "No labeled segments file found to delete"
        })

    except Exception as e:
        app.logger.exception("Error deleting labels file")
        return jsonify({
            "success": False,
            "message": f"Error deleting labels file: {str(e)}"
        })


def auto_load_data():
    """Load audio files and CSV data from default container locations."""
    global csv_error_data, csv_file_loaded
    global current_directory, current_playlist, audio_file_map

    # Auto-load audio files
    audio_path = "/opt/audio"

    if os.path.isdir(audio_path):
        try:
            current_directory = audio_path
            current_playlist = []
            audio_file_map = {}

            for root, _, files in os.walk(audio_path):
                for filename in files:
                    if filename.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
                        full_path = os.path.join(root, filename)
                        current_playlist.append(full_path)

                        audio_file_map[filename] = full_path
                        name_without_ext = os.path.splitext(filename)[0]
                        audio_file_map[name_without_ext] = full_path

            current_playlist.sort()

            app.logger.info(
                f"Auto-loaded {len(current_playlist)} audio files "
                f"from {audio_path}"
            )

        except Exception as e:
            app.logger.exception("Failed to auto-load audio files")

    # Auto-load CSV data
    csv_path = "/opt/data/err-dataset-orig.csv"

    if not os.path.exists(csv_path):
        csv_path = "/opt/data/err-dataset.csv"

    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                csv_content = f.read()

            csv_error_data = []
            reader = csv.DictReader(StringIO(csv_content))

            required_columns = [
                'recordErrorID',
                'recordFile',
                'exampleExample',
                'recordTime'
            ]

            for row in reader:
                if not all(col in row for col in required_columns):
                    continue

                try:
                    record_time_text = row['recordTime'] or ''
                    record_time = (
                        time_to_seconds(record_time_text)
                        if record_time_text.strip()
                        else 0.0
                    )
                    record_file = (row['recordFile'] or '').strip()

                    if record_file:
                        csv_error_data.append({
                            'record_id': row['recordErrorID'],
                            'record_file': record_file,
                            'example_phrase': row['exampleExample'],
                            'record_time': record_time
                        })

                except (ValueError, AttributeError):
                    continue

            csv_file_loaded = True

            app.logger.info(
                f"Auto-loaded CSV with {len(csv_error_data)} error records"
            )

        except Exception:
            app.logger.exception("Failed to auto-load CSV")


if __name__ == '__main__':
    auto_load_data()
    app.run(debug=False, host='0.0.0.0', port=5000)
