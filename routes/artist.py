#!/usr/bin/env python3
"""
Artist endpoint blueprint.
"""

from flask import Blueprint, Response, request
import json
import os
import random
import string
import sys
import traceback
from multiprocessing import Process

artist_bp = Blueprint('artist', __name__)

# Global dictionary to keep track of running download processes.
download_processes = {}

def generate_random_filename(length=6):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length)) + '.artist.prg'

class FlushingFileWrapper:
    def __init__(self, file):
        self.file = file

    def write(self, text):
        # Only write lines that start with '{'
        for line in text.split('\n'):
            line = line.strip()
            if line and line.startswith('{'):
                try:
                    obj = json.loads(line)
                    # Skip writing if the JSON object has type "track"
                    if obj.get("type") == "track":
                        continue
                except ValueError:
                    # If not valid JSON, write the line as is.
                    pass
                self.file.write(line + '\n')
        self.file.flush()

    def flush(self):
        self.file.flush()

def download_artist_task(service, artist_url, main, fallback, quality, fall_quality, real_time,
                         album_type, prg_path, orig_request, custom_dir_format, custom_track_format):
    """
    This function wraps the call to download_artist_albums, writes the original
    request data to the progress file, and then writes JSON status updates.
    """
    try:
        from routes.utils.artist import download_artist_albums
        with open(prg_path, 'w') as f:
            flushing_file = FlushingFileWrapper(f)
            original_stdout = sys.stdout
            sys.stdout = flushing_file  # Redirect stdout to our flushing file wrapper

            # Write the original request data to the progress file.
            try:
                flushing_file.write(json.dumps({"original_request": orig_request}) + "\n")
            except Exception as e:
                flushing_file.write(json.dumps({
                    "status": "error",
                    "message": f"Failed to write original request data: {str(e)}"
                }) + "\n")

            try:
                download_artist_albums(
                    service=service,
                    artist_url=artist_url,
                    main=main,
                    fallback=fallback,
                    quality=quality,
                    fall_quality=fall_quality,
                    real_time=real_time,
                    album_type=album_type,
                    custom_dir_format=custom_dir_format,
                    custom_track_format=custom_track_format
                )
                flushing_file.write(json.dumps({"status": "complete"}) + "\n")
            except Exception as e:
                error_data = json.dumps({
                    "status": "error",
                    "message": str(e),
                    "traceback": traceback.format_exc()
                })
                flushing_file.write(error_data + "\n")
            finally:
                sys.stdout = original_stdout  # Restore stdout
    except Exception as e:
        with open(prg_path, 'w') as f:
            error_data = json.dumps({
                "status": "error",
                "message": str(e),
                "traceback": traceback.format_exc()
            })
            f.write(error_data + "\n")

@artist_bp.route('/download', methods=['GET'])
def handle_artist_download():
    """
    Starts the artist album download process.
    Expected query parameters:
      - artist_url: string (e.g., a Spotify artist URL)
      - service: string (e.g., "deezer" or "spotify")
      - main: string (e.g., "MX")
      - fallback: string (optional, e.g., "JP")
      - quality: string (e.g., "MP3_128")
      - fall_quality: string (optional, e.g., "HIGH")
      - real_time: bool (e.g., "true" or "false")
      - album_type: string(s); one or more of "album", "single", "appears_on", "compilation" (if multiple, comma-separated)
      - custom_dir_format: string (optional, default: "%ar_album%/%album%/%copyright%")
      - custom_track_format: string (optional, default: "%tracknum%. %music% - %artist%")
    """
    service = request.args.get('service')
    artist_url = request.args.get('artist_url')
    main = request.args.get('main')
    fallback = request.args.get('fallback')
    quality = request.args.get('quality')
    fall_quality = request.args.get('fall_quality')
    album_type = request.args.get('album_type')
    real_time_arg = request.args.get('real_time', 'false')
    real_time = real_time_arg.lower() in ['true', '1', 'yes']

    # New query parameters for custom formatting.
    custom_dir_format = request.args.get('custom_dir_format', "%ar_album%/%album%")
    custom_track_format = request.args.get('custom_track_format', "%tracknum%. %music% - %artist%")

    # Sanitize main and fallback to prevent directory traversal
    if main:
        main = os.path.basename(main)
    if fallback:
        fallback = os.path.basename(fallback)

    # Check for required parameters.
    if not all([service, artist_url, main, quality, album_type]):
        return Response(
            json.dumps({"error": "Missing parameters"}),
            status=400,
            mimetype='application/json'
        )

    # Validate credentials based on the selected service.
    try:
        if service == 'spotify':
            if fallback:
                # When using Spotify as the main service with a fallback, assume main credentials for Deezer and fallback for Spotify.
                deezer_creds_path = os.path.abspath(os.path.join('./creds/deezer', main, 'credentials.json'))
                if not os.path.isfile(deezer_creds_path):
                    return Response(
                        json.dumps({"error": "Invalid Deezer credentials directory"}),
                        status=400,
                        mimetype='application/json'
                    )
                spotify_fallback_path = os.path.abspath(os.path.join('./creds/spotify', fallback, 'credentials.json'))
                if not os.path.isfile(spotify_fallback_path):
                    return Response(
                        json.dumps({"error": "Invalid Spotify fallback credentials directory"}),
                        status=400,
                        mimetype='application/json'
                    )
            else:
                # Validate Spotify main credentials.
                spotify_creds_path = os.path.abspath(os.path.join('./creds/spotify', main, 'credentials.json'))
                if not os.path.isfile(spotify_creds_path):
                    return Response(
                        json.dumps({"error": "Invalid Spotify credentials directory"}),
                        status=400,
                        mimetype='application/json'
                    )
        elif service == 'deezer':
            # Validate Deezer main credentials.
            deezer_creds_path = os.path.abspath(os.path.join('./creds/deezer', main, 'credentials.json'))
            if not os.path.isfile(deezer_creds_path):
                return Response(
                    json.dumps({"error": "Invalid Deezer credentials directory"}),
                    status=400,
                    mimetype='application/json'
                )
        else:
            return Response(
                json.dumps({"error": "Unsupported service"}),
                status=400,
                mimetype='application/json'
            )
    except Exception as e:
        return Response(
            json.dumps({"error": f"Credential validation failed: {str(e)}"}),
            status=500,
            mimetype='application/json'
        )

    # Create a random filename for the progress file.
    filename = generate_random_filename()
    prg_dir = './prgs'
    os.makedirs(prg_dir, exist_ok=True)
    prg_path = os.path.join(prg_dir, filename)

    # Capture the original request parameters as a dictionary.
    orig_request = request.args.to_dict()

    # Create and start the download process.
    process = Process(
        target=download_artist_task,
        args=(
            service,
            artist_url,
            main,
            fallback,
            quality,
            fall_quality,
            real_time,
            album_type,
            prg_path,
            orig_request,
            custom_dir_format,
            custom_track_format
        )
    )
    process.start()
    download_processes[filename] = process

    return Response(
        json.dumps({"prg_file": filename}),
        status=202,
        mimetype='application/json'
    )

@artist_bp.route('/download/cancel', methods=['GET'])
def cancel_artist_download():
    """
    Cancel a running artist download process by its prg file name.
    """
    prg_file = request.args.get('prg_file')
    if not prg_file:
        return Response(
            json.dumps({"error": "Missing process id (prg_file) parameter"}),
            status=400,
            mimetype='application/json'
        )

    process = download_processes.get(prg_file)
    prg_dir = './prgs'
    prg_path = os.path.join(prg_dir, prg_file)

    if process and process.is_alive():
        process.terminate()
        process.join()  # Wait for termination
        del download_processes[prg_file]

        try:
            with open(prg_path, 'a') as f:
                f.write(json.dumps({"status": "cancel"}) + "\n")
        except Exception as e:
            return Response(
                json.dumps({"error": f"Failed to write cancel status to file: {str(e)}"}),
                status=500,
                mimetype='application/json'
            )

        return Response(
            json.dumps({"status": "cancel"}),
            status=200,
            mimetype='application/json'
        )
    else:
        return Response(
            json.dumps({"error": "Process not found or already terminated"}),
            status=404,
            mimetype='application/json'
        )

# NEW ENDPOINT: Get Artist Information
@artist_bp.route('/info', methods=['GET'])
def get_artist_info():
    """
    Retrieve Spotify artist metadata given a Spotify ID.
    Expects a query parameter 'id' that contains the Spotify artist ID.
    """
    spotify_id = request.args.get('id')
    if not spotify_id:
        return Response(
            json.dumps({"error": "Missing parameter: id"}),
            status=400,
            mimetype='application/json'
        )
    
    try:
        # Import the get_spotify_info function from the utility module.
        from routes.utils.get_info import get_spotify_info
        # Call the function with the artist type.
        artist_info = get_spotify_info(spotify_id, "artist")
        return Response(
            json.dumps(artist_info),
            status=200,
            mimetype='application/json'
        )
    except Exception as e:
        error_data = {
            "error": str(e),
            "traceback": traceback.format_exc()
        }
        return Response(
            json.dumps(error_data),
            status=500,
            mimetype='application/json'
        )