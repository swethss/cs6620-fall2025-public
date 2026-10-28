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
