
import os
import sys
import subprocess
import json
import re
import time
import uuid
import threading
import tempfile
import base64
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
import urllib.request
from flask import Flask, render_template, jsonify, request, send_file, session, Response
from flask_sock import Sock
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32))  # Use env variable or fallback to random
sock = Sock(app)

# Rate limiting - prevents abuse
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per hour", "10 per minute"],
    storage_uri="memory://"
)

# Use system's Downloads folder instead of project directory
# Windows: C:\Users\<username>\Downloads
# macOS/Linux: ~/Downloads
DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "MediaPull")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Execute yt-dlp as a python module to avoid path issues
YT_DLP_CMD = [sys.executable, "-m", "yt_dlp"]

# Store for connected clients and current state
clients = set()
current_state = {
    "playing": False,
    "paused": False,
    "title": None,
    "url": None,
    "audio_url": None,
    "video_url": None,
    "duration": 0,
    "current_time": 0,
    "format": "video",
    "quality": "best"
}

# Download jobs storage
download_jobs = {}
jobs_lock = threading.Lock()

# Secure cookie storage (encrypted in-memory only)
# Cookies are NEVER written to disk, only stored in encrypted memory
_cookie_encryption_key = Fernet.generate_key()
_cookie_cipher = Fernet(_cookie_encryption_key)
_user_cookies = {}
_cookies_lock = threading.Lock()
COOKIE_EXPIRY_HOURS = 2  # Auto-expire cookies after 2 hours

def _encrypt_cookie_data(data: str) -> bytes:
    """Encrypt cookie data in memory"""
    return _cookie_cipher.encrypt(data.encode())

def _decrypt_cookie_data(encrypted_data: bytes) -> str:
    """Decrypt cookie data from memory"""
    return _cookie_cipher.decrypt(encrypted_data).decode()

def save_user_cookies(session_id: str, cookie_content: str, platforms: list) -> dict:
    """Save cookies securely in encrypted memory storage"""
    with _cookies_lock:
        encrypted = _encrypt_cookie_data(cookie_content)
        _user_cookies[session_id] = {
            'cookies': encrypted,
            'platforms': platforms,
            'created_at': time.time(),
            'expires_at': time.time() + (COOKIE_EXPIRY_HOURS * 3600),
            'last_used': time.time()
        }
    return {'success': True, 'expires_in': f'{COOKIE_EXPIRY_HOURS} hours'}

def get_user_cookies(session_id: str) -> str:
    """Retrieve and decrypt cookies for a session"""
    with _cookies_lock:
        if session_id not in _user_cookies:
            return None

        cookie_data = _user_cookies[session_id]

        # Check if expired
        if time.time() > cookie_data['expires_at']:
            del _user_cookies[session_id]
            return None

        # Update last used
        cookie_data['last_used'] = time.time()

        try:
            return _decrypt_cookie_data(cookie_data['cookies'])
        except Exception:
            return None

def clear_user_cookies(session_id: str):
    """Clear cookies for a session"""
    with _cookies_lock:
        if session_id in _user_cookies:
            del _user_cookies[session_id]

def cleanup_expired_cookies():
    """Remove expired cookies from memory"""
    with _cookies_lock:
        now = time.time()
        expired = [sid for sid, data in _user_cookies.items() if now > data['expires_at']]
        for sid in expired:
            del _user_cookies[sid]

def get_cookie_temp_path(session_id: str) -> str:
    """Get a temporary file path for cookies (for yt-dlp use)"""
    cookie_content = get_user_cookies(session_id)
    if not cookie_content:
        return None

    # Create temp file with proper permissions
    fd, temp_path = tempfile.mkstemp(suffix='.txt', prefix='mediapull_cookies_')
    try:
        os.write(fd, cookie_content.encode('utf-8'))
        os.close(fd)
        # Set restrictive permissions (readable only by owner)
        os.chmod(temp_path, 0o600)
        return temp_path
    except Exception:
        os.close(fd)
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        return None

# Supported platforms with their specific handling
SUPPORTED_PLATFORMS = {
    'youtube.com': {'name': 'YouTube', 'extractor': 'youtube'},
    'youtu.be': {'name': 'YouTube', 'extractor': 'youtube'},
    'instagram.com': {'name': 'Instagram', 'extractor': 'instagram'},
    'tiktok.com': {'name': 'TikTok', 'extractor': 'tiktok'},
    'twitter.com': {'name': 'Twitter/X', 'extractor': 'twitter'},
    'x.com': {'name': 'Twitter/X', 'extractor': 'twitter'},
    'facebook.com': {'name': 'Facebook', 'extractor': 'facebook'},
    'fb.watch': {'name': 'Facebook', 'extractor': 'facebook'}
}

# Error message mapping for user-friendly errors
ERROR_MESSAGES = {
    'private': 'This video is private or requires authentication',
    'unavailable': 'This video is unavailable or has been removed',
    'region': 'This video is region-restricted in your area',
    'age': 'This video is age-restricted',
    'copyright': 'This video has been removed due to copyright claims',
    'login': 'This content requires login credentials',
    'member_only': 'This is member-only content. Please upload cookies for access',
    'network': 'Network error while fetching video. Please try again',
    'parse': 'Could not parse video information. Platform may have changed',
    'timeout': 'Request timed out. The video may be too large or the platform slow',
}

def is_valid_url(url: str) -> bool:
    """Check if URL is from a supported platform"""
    url_lower = url.lower()
    return any(platform in url_lower for platform in SUPPORTED_PLATFORMS.keys())

def is_playlist_url(url: str) -> bool:
    """Check if URL is a playlist URL"""
    # YouTube playlist URLs
    if 'list=' in url:
        return True
    # Generic playlist paths
    if '/playlist' in url:
        return True
    # YouTube playlist short URL
    if 'youtube.com/playlist' in url:
        return True
    return False

def get_platform_info(url: str) -> dict:
    """Get platform name and extractor from URL"""
    url_lower = url.lower()
    for platform, info in SUPPORTED_PLATFORMS.items():
        if platform in url_lower:
            return info
    return {'name': 'Unknown', 'extractor': 'generic'}

def sanitize_filename(filename: str) -> str:
    """Remove invalid characters from filename"""
    return re.sub(r'[<>"/\\|?*]', '', filename)[:150]

def parse_error_message(error_text: str, has_cookies: bool = False) -> str:
    """Parse yt-dlp error and return user-friendly message"""
    error_lower = error_text.lower()

    # Member-only content detection
    member_indicators = ['member', 'premium', 'subscription', 'paid', 'patreon', 'channel membership']
    if any(x in error_lower for x in member_indicators):
        if not has_cookies:
            return ERROR_MESSAGES['member_only']
        else:
            return 'This content requires valid membership cookies. Your cookies may have expired or lack access.'

    if any(x in error_lower for x in ['private', 'sign in', 'login', 'auth']):
        return ERROR_MESSAGES['private']
    elif any(x in error_lower for x in ['unavailable', 'not exist', 'removed']):
        return ERROR_MESSAGES['unavailable']
    elif any(x in error_lower for x in ['region', 'country', 'blocked', 'geoblock']):
        return ERROR_MESSAGES['region']
    elif any(x in error_lower for x in ['age', 'age-restricted', 'adults']):
        return ERROR_MESSAGES['age']
    elif any(x in error_lower for x in ['copyright', 'dmca', 'violation']):
        return ERROR_MESSAGES['copyright']
    elif any(x in error_lower for x in ['timeout', 'time out']):
        return ERROR_MESSAGES['timeout']
    elif any(x in error_lower for x in ['network', 'connection', 'unreachable']):
        return ERROR_MESSAGES['network']
    elif any(x in error_lower for x in ['unable to extract', 'parse']):
        return ERROR_MESSAGES['parse']
    else:
        # Return the actual error message but sanitized if it's too long
        clean_error = error_msg.split('\n')[0]
        if len(clean_error) > 100:
            clean_error = clean_error[:97] + "..."
        return f"Error: {clean_error}"

def get_playlist_info(playlist_url: str, session_id: str = None) -> dict:
    """Extract playlist info and list of videos"""
    cookie_path = None
    temp_files = []

    try:
        # Get playlist info
        info_cmd = [
            *YT_DLP_CMD,
            "--dump-json",
            "--no-download",
            "--flat-playlist",  # Don't extract individual video info, just metadata
            "--playlist-items", "0-49",  # Limit to first 50 videos for preview
            "--no-warnings",
            "--js-runtimes", "node"
        ]

        # Add cookies if available
        if session_id:
            cookie_path = get_cookie_temp_path(session_id)
            if cookie_path:
                info_cmd.extend(["--cookies", cookie_path])
                temp_files.append(cookie_path)

        info_cmd.append(playlist_url)

        result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            error_msg = parse_error_message(result.stderr)
            return {'error': error_msg, 'details': result.stderr}

        # Parse each line as a separate video entry
        videos = []
        for line in result.stdout.strip().split('\n'):
            if line:
                try:
                    video_info = json.loads(line)
                    videos.append({
                        'id': video_info.get('id', ''),
                        'title': video_info.get('title', 'Unknown'),
                        'duration': video_info.get('duration', 0),
                        'thumbnail': video_info.get('thumbnail', ''),
                        'uploader': video_info.get('uploader', 'Unknown'),
                        'url': video_info.get('url', '') or f"https://www.youtube.com/watch?v={video_info.get('id', '')}"
                    })
                except json.JSONDecodeError:
                    continue

        if not videos:
            return {'error': 'No videos found in playlist'}

        # Get playlist metadata from first video's context
        playlist_title = videos[0].get('title', 'Playlist')

        return {
            'is_playlist': True,
            'title': playlist_title,
            'video_count': len(videos),
            'videos': videos,
            'platform': get_platform_info(playlist_url)['name'],
            'original_url': playlist_url
        }

    except subprocess.TimeoutExpired:
        return {'error': ERROR_MESSAGES['timeout']}
    except Exception as e:
        print(f"[playlist] Error: {e}")
        return {'error': ERROR_MESSAGES['parse']}
    finally:
        # Clean up temp cookie files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception:
                pass

def get_video_info(video_url: str, session_id: str = None) -> dict:
    """Extract video info and available formats from video URL using yt-dlp"""
    cookie_path = None
    temp_files = []

    try:
        # Get video info with format listing
        info_cmd = [
            *YT_DLP_CMD,
            "--dump-json",
            "--no-download",
            "--no-warnings",
            "--js-runtimes", "node"
        ]

        # Add cookies if available
        has_cookies = False
        if session_id:
            cookie_path = get_cookie_temp_path(session_id)
            if cookie_path:
                info_cmd.extend(["--cookies", cookie_path])
                temp_files.append(cookie_path)
                has_cookies = True

        info_cmd.append(video_url)

        result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            error_msg = parse_error_message(result.stderr, has_cookies)
            return {'error': error_msg, 'details': result.stderr}

        info = json.loads(result.stdout)

        # Extract available formats
        formats = []
        seen_qualities = set()

        for fmt in info.get('formats', []):
            # Skip audio-only for video selection
            if fmt.get('vcodec') != 'none' and fmt.get('height'):
                height = fmt.get('height')
                if height not in seen_qualities:
                    seen_qualities.add(height)
                    formats.append({
                        'format_id': fmt.get('format_id'),
                        'height': height,
                        'ext': fmt.get('ext', 'mp4'),
                        'quality_label': f"{height}p",
                        'filesize_approx': fmt.get('filesize_approx', 0)
                    })

        # Sort by height descending
        formats.sort(key=lambda x: x['height'], reverse=True)

        # Audio formats
        audio_formats = []
        for fmt in info.get('formats', []):
            if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                audio_formats.append({
                    'format_id': fmt.get('format_id'),
                    'ext': fmt.get('ext', 'm4a'),
                    'abr': fmt.get('abr', 0),
                    'filesize_approx': fmt.get('filesize_approx', 0)
                })

        # Sort by bitrate
        audio_formats.sort(key=lambda x: x['abr'] if x['abr'] else 0, reverse=True)

        return {
            "title": info.get('title', 'Unknown'),
            "duration": info.get('duration', 0),
            "thumbnail": info.get('thumbnail', ''),
            "uploader": info.get('uploader', 'Unknown'),
            "upload_date": info.get('upload_date', ''),
            "view_count": info.get('view_count', 0),
            "formats": formats[:10],  # Limit to top 10
            "audio_formats": audio_formats[:5],  # Top 5 audio formats
            "original_url": video_url,
            "platform": get_platform_info(video_url)['name'],
            "extractor": get_platform_info(video_url)['extractor']
        }
    except subprocess.TimeoutExpired:
        return {'error': ERROR_MESSAGES['timeout']}
    except Exception as e:
        print(f"[video] Error: {e}")
        return {'error': ERROR_MESSAGES['parse']}
    finally:
        # Clean up temp cookie files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception:
                pass

def get_stream_urls(video_url: str, video_format: str = None, audio_format: str = None, session_id: str = None) -> dict:
    """Get streaming URLs for video and audio"""
    temp_files = []
    cookie_path = None

    try:
        # Get cookie path if available
        if session_id:
            cookie_path = get_cookie_temp_path(session_id)
            if cookie_path:
                temp_files.append(cookie_path)

        # Get best audio stream
        audio_cmd = [
            *YT_DLP_CMD,
            "-f", audio_format if audio_format else "bestaudio/best",
            "-g",
            "--no-warnings",
            "--js-runtimes", "node"
        ]
        if cookie_path:
            audio_cmd.extend(["--cookies", cookie_path])
        audio_cmd.append(video_url)

        audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=30)
        audio_url = audio_result.stdout.strip().split('\n')[0] if audio_result.returncode == 0 else None

        # Get video stream URL
        if video_format:
            video_cmd = [
                *YT_DLP_CMD,
                "-f", f"{video_format}+bestaudio/best[height<={video_format}p]/best",
                "-g",
                "--no-warnings",
                "--js-runtimes", "node"
            ]
        else:
            video_cmd = [
                *YT_DLP_CMD,
                "-f", "best[height<=1080]/best",
                "-g",
                "--no-warnings",
                "--js-runtimes", "node"
            ]

        if cookie_path:
            video_cmd.extend(["--cookies", cookie_path])
        video_cmd.append(video_url)

        video_result = subprocess.run(video_cmd, capture_output=True, text=True, timeout=30)
        video_stream_url = video_result.stdout.strip().split('\n')[0] if video_result.returncode == 0 else None

        return {
            "audio_url": audio_url,
            "video_url": video_stream_url
        }
    except Exception as e:
        print(f"[stream] Error: {e}")
        return {"audio_url": None, "video_url": None}
    finally:
        # Clean up temp cookie files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception:
                pass

def run_playlist_download_job(job_id: str, playlist_url: str, format_type: str, quality: str = None, session_id: str = None):
    """Background job for downloading entire playlists"""
    temp_files = []
    cookie_path = None

    try:
        with jobs_lock:
            download_jobs[job_id]['status'] = 'processing'
            download_jobs[job_id]['progress'] = 5

        # Get cookies if available
        if session_id:
            cookie_path = get_cookie_temp_path(session_id)
            if cookie_path:
                temp_files.append(cookie_path)

        # First, get playlist info to extract title
        playlist_cmd = [
            *YT_DLP_CMD,
            "--dump-json",
            "--no-download",
            "--flat-playlist",
            "--playlist-items", "0",  # Just get first item for playlist title
            "--no-warnings",
            "--js-runtimes", "node"
        ]
        if cookie_path:
            playlist_cmd.extend(["--cookies", cookie_path])
        playlist_cmd.append(playlist_url)

        result = subprocess.run(playlist_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            try:
                pl_info = json.loads(result.stdout)
                playlist_title = sanitize_filename(pl_info.get('title', 'Playlist'))
            except:
                playlist_title = sanitize_filename("Playlist")
        else:
            playlist_title = sanitize_filename("Playlist")

        # Create playlist folder
        timestamp = int(time.time())
        playlist_folder = os.path.join(DOWNLOADS_DIR, f"{playlist_title}_{timestamp}")
        os.makedirs(playlist_folder, exist_ok=True)

        with jobs_lock:
            download_jobs[job_id]['playlist_folder'] = playlist_folder
            download_jobs[job_id]['progress'] = 10

        # Build download command for entire playlist
        if format_type == 'audio':
            format_spec = "bestaudio/best"
            if quality == 'medium':
                format_spec = "bestaudio[abr<=128]/bestaudio"
            elif quality == 'low':
                format_spec = "worstaudio"

            cmd = [
                *YT_DLP_CMD,
                "-f", format_spec,
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "-o", os.path.join(playlist_folder, "%(title)s.%(ext)s"),
                "--newline",
                "--progress",
                "--no-warnings",
                "--js-runtimes", "node"
            ]
        else:  # video
            if quality and quality != 'best':
                height = quality.replace('p', '')
                format_spec = f"best[height<={height}][ext=mp4]/best[height<={height}]/best"
            else:
                format_spec = "best[height<=1080][ext=mp4]/best[ext=mp4]/best"

            cmd = [
                *YT_DLP_CMD,
                "-f", format_spec,
                "--merge-output-format", "mp4",
                "-o", os.path.join(playlist_folder, "%(title)s.%(ext)s"),
                "--newline",
                "--progress",
                "--no-warnings",
                "--js-runtimes", "node"
            ]

        if cookie_path:
            cmd.extend(["--cookies", cookie_path])

        cmd.append(playlist_url)

        with jobs_lock:
            download_jobs[job_id]['status'] = 'downloading'
            download_jobs[job_id]['progress'] = 15

        # Run download with progress parsing
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        total_videos = None
        downloaded_count = 0

        for line in iter(process.stdout.readline, ''):
            if line:
                # Detect video count
                if 'Downloading playlist' in line:
                    import re
                    match = re.search(r'(\d+)\s+videos', line)
                    if match:
                        total_videos = int(match.group(1))

                # Detect completed downloads
                if 'has already been downloaded' in line or 'Destination' in line:
                    downloaded_count += 1
                    if total_videos:
                        progress = 15 + int((downloaded_count / total_videos) * 80)
                        with jobs_lock:
                            download_jobs[job_id]['progress'] = progress
                            download_jobs[job_id]['videos_downloaded'] = downloaded_count

        process.stdout.close()
        returncode = process.wait()

        if returncode != 0:
            error_text = process.stderr.read()
            if 'error' not in error_text.lower() or downloaded_count > 0:
                # Partial success - some videos downloaded
                pass
            else:
                with jobs_lock:
                    download_jobs[job_id]['status'] = 'failed'
                    download_jobs[job_id]['error'] = parse_error_message(error_text)
                return

        with jobs_lock:
            download_jobs[job_id]['status'] = 'completed'
            download_jobs[job_id]['progress'] = 100
            download_jobs[job_id]['videos_downloaded'] = downloaded_count
            download_jobs[job_id]['completed_at'] = datetime.now().isoformat()

    except subprocess.TimeoutExpired:
        with jobs_lock:
            download_jobs[job_id]['status'] = 'failed'
            download_jobs[job_id]['error'] = ERROR_MESSAGES['timeout']
    except Exception as e:
        print(f"[playlist_download] Error: {e}")
        with jobs_lock:
            download_jobs[job_id]['status'] = 'failed'
            download_jobs[job_id]['error'] = str(e)
    finally:
        # Clean up temp cookie files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception:
                pass

def run_download_job(job_id: str, url: str, format_type: str, quality: str = None, session_id: str = None):
    """Background job for downloading media"""
    temp_files = []
    try:
        with jobs_lock:
            download_jobs[job_id]['status'] = 'downloading'
            download_jobs[job_id]['progress'] = 10

        # Get video info first
        info = get_video_info(url, session_id)
        if 'error' in info:
            with jobs_lock:
                download_jobs[job_id]['status'] = 'failed'
                download_jobs[job_id]['error'] = info['error']
            return

        with jobs_lock:
            download_jobs[job_id]['progress'] = 30
            download_jobs[job_id]['title'] = info['title']

        safe_title = sanitize_filename(info['title'])
        timestamp = int(time.time())

        if format_type == 'audio':
            filename = f"{safe_title}_{timestamp}.mp3"
            filepath = os.path.join(DOWNLOADS_DIR, filename)

            # Format selection for audio
            format_spec = "bestaudio/best"
            if quality == 'best':
                format_spec = "bestaudio/best"
            elif quality == 'medium':
                format_spec = "bestaudio[abr<=128]/bestaudio"
            elif quality == 'low':
                format_spec = "worstaudio"

            cmd = [
                *YT_DLP_CMD,
                "-f", format_spec,
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "-o", filepath,
                "--newline",
                "--progress",
                "--no-warnings",
                "--js-runtimes", "node"
            ]

            # Add cookies if available
            if session_id:
                cookie_path = get_cookie_temp_path(session_id)
                if cookie_path:
                    cmd.extend(["--cookies", cookie_path])
                    temp_files.append(cookie_path)

            cmd.append(url)
        else:  # video
            filename = f"{safe_title}_{timestamp}.mp4"
            filepath = os.path.join(DOWNLOADS_DIR, filename)

            # Format selection for video
            if quality and quality != 'best':
                height = quality.replace('p', '')
                format_spec = f"best[height<={height}][ext=mp4]/best[height<={height}]/best"
            else:
                format_spec = "best[height<=1080][ext=mp4]/best[ext=mp4]/best"

            cmd = [
                *YT_DLP_CMD,
                "-f", format_spec,
                "--merge-output-format", "mp4",
                "-o", filepath,
                "--newline",
                "--progress",
                "--no-warnings",
                "--js-runtimes", "node"
            ]

            # Add cookies if available
            if session_id:
                cookie_path = get_cookie_temp_path(session_id)
                if cookie_path:
                    cmd.extend(["--cookies", cookie_path])
                    temp_files.append(cookie_path)

            cmd.append(url)

        with jobs_lock:
            download_jobs[job_id]['progress'] = 50

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        # Check for actual file
        actual_file = filepath
        if not os.path.exists(actual_file):
            ext = '.mp3' if format_type == 'audio' else '.mp4'
            if os.path.exists(filepath + ext):
                actual_file = filepath + ext

        if result.returncode != 0 or not os.path.exists(actual_file):
            error_msg = parse_error_message(result.stderr)
            with jobs_lock:
                download_jobs[job_id]['status'] = 'failed'
                download_jobs[job_id]['error'] = error_msg
            return

        with jobs_lock:
            download_jobs[job_id]['status'] = 'completed'
            download_jobs[job_id]['progress'] = 100
            download_jobs[job_id]['filepath'] = actual_file
            download_jobs[job_id]['filename'] = os.path.basename(actual_file)
            download_jobs[job_id]['completed_at'] = datetime.now().isoformat()

    except subprocess.TimeoutExpired:
        with jobs_lock:
            download_jobs[job_id]['status'] = 'failed'
            download_jobs[job_id]['error'] = ERROR_MESSAGES['timeout']
    except Exception as e:
        print(f"[download_job] Error: {e}")
        with jobs_lock:
            download_jobs[job_id]['status'] = 'failed'
            download_jobs[job_id]['error'] = str(e)
    finally:
        # Clean up temp cookie files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception:
                pass

def broadcast_state():
    """Broadcast current state to all connected clients"""
    global clients
    message = json.dumps(current_state)
    disconnected = set()
    for client in clients:
        try:
            client.send(message)
        except:
            disconnected.add(client)
    clients -= disconnected

def cleanup_old_jobs():
    """Remove jobs older than 24 hours"""
    cutoff = time.time() - (24 * 3600)
    with jobs_lock:
        expired = [k for k, v in download_jobs.items() if v.get('created_at', 0) < cutoff]
        for k in expired:
            del download_jobs[k]

@app.route('/')
def index():
    return render_template('player.html')

@app.route('/api/analyze', methods=['POST'])
@limiter.limit("10 per minute")
def analyze_url():
    """Analyze URL to determine if it's a playlist or single video"""
    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not is_valid_url(url):
        return jsonify({"error": "Unsupported platform. Try YouTube, Instagram, TikTok, Twitter/X, or Facebook"}), 400

    session_id = request.cookies.get('session_id') or request.headers.get('X-Session-ID')

    # Check if it's a playlist
    if is_playlist_url(url):
        info = get_playlist_info(url, session_id)
        if 'error' in info:
            return jsonify({"error": info['error']}), 400

        return jsonify({
            "success": True,
            "is_playlist": True,
            "title": info['title'],
            "video_count": info['video_count'],
            "videos": info['videos'],
            "platform": info['platform']
        })
    else:
        info = get_video_info(url, session_id)
        if 'error' in info:
            return jsonify({"error": info['error']}), 400

        return jsonify({
            "success": True,
            "is_playlist": False,
            "title": info['title'],
            "duration": info['duration'],
            "thumbnail": info['thumbnail'],
            "platform": info['platform'],
            "video_formats": info['formats'],
            "audio_formats": info['audio_formats']
        })

@app.route('/api/formats', methods=['POST'])
@limiter.limit("10 per minute")
def get_formats():
    """Get available formats for a URL (single video only)"""
    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not is_valid_url(url):
        return jsonify({"error": "Unsupported platform. Try YouTube, Instagram, TikTok, Twitter/X, or Facebook"}), 400

    if is_playlist_url(url):
        return jsonify({"error": "Use /api/analyze for playlist URLs"}), 400

    # Get session ID for cookies
    session_id = request.cookies.get('session_id') or request.headers.get('X-Session-ID')

    info = get_video_info(url, session_id)
    if 'error' in info:
        return jsonify({"error": info['error']}), 400

    return jsonify({
        "success": True,
        "title": info['title'],
        "duration": info['duration'],
        "thumbnail": info['thumbnail'],
        "platform": info['platform'],
        "video_formats": info['formats'],
        "audio_formats": info['audio_formats']
    })

@app.route('/api/proxy-thumbnail')
def proxy_thumbnail():
    """Proxy thumbnails to bypass CORS/CORP restrictions"""
    url = request.args.get('url')
    if not url:
        return "No URL provided", 400
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return Response(
                response.read(),
                mimetype=response.headers.get_content_type() or 'image/jpeg'
            )
    except Exception as e:
        print(f"[proxy] Error fetching thumbnail: {e}")
        return "Failed to fetch thumbnail", 500

@app.route('/api/play', methods=['POST'])
@limiter.limit("20 per minute")
def play():
    data = request.get_json()
    url = data.get('url')
    format_type = data.get('format', 'video')
    quality = data.get('quality', 'best')

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not is_valid_url(url):
        return jsonify({"error": "Unsupported platform. Try YouTube, Instagram, TikTok, or Twitter/X"}), 400

    # Get session ID for cookies
    session_id = request.cookies.get('session_id') or request.headers.get('X-Session-ID')

    # Get video info
    info = get_video_info(url, session_id)
    if 'error' in info:
        return jsonify({"error": info['error']}), 400

    # Get stream URLs
    video_format_id = None
    audio_format_id = None

    if format_type == 'video' and quality != 'best':
        for fmt in info.get('formats', []):
            if fmt.get('quality_label') == quality:
                video_format_id = fmt.get('format_id')
                break

    streams = get_stream_urls(url, video_format_id, audio_format_id, session_id)

    if not streams.get('video_url') and format_type == 'video':
        return jsonify({"error": "Could not get stream URL. Video may be restricted."}), 400

    if not streams.get('audio_url') and format_type == 'audio':
        return jsonify({"error": "Could not get audio stream URL."}), 400

    # Update state
    current_state.update({
        "playing": True,
        "paused": False,
        "title": info["title"],
        "url": info["original_url"],
        "audio_url": streams.get("audio_url"),
        "video_url": streams.get("video_url"),
        "duration": info["duration"],
        "thumbnail": info.get("thumbnail", ""),
        "uploader": info.get("uploader", "Unknown"),
        "platform": info.get("platform", "Unknown"),
        "current_time": 0,
        "format": format_type,
        "quality": quality
    })

    broadcast_state()
    return jsonify({"success": True, **current_state})

@app.route('/api/download', methods=['POST'])
@limiter.limit("5 per minute")
def download():
    """Start a download job (single video or playlist)"""
    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not is_valid_url(url):
        return jsonify({"error": "Unsupported platform. Try YouTube, Instagram, TikTok, or Twitter/X"}), 400

    format_type = data.get('format', 'audio')
    quality = data.get('quality', 'best')
    is_playlist = data.get('is_playlist', False)

    # Get session ID for cookies
    session_id = request.cookies.get('session_id') or request.headers.get('X-Session-ID')

    # Create job
    job_id = str(uuid.uuid4())[:8]

    with jobs_lock:
        download_jobs[job_id] = {
            'id': job_id,
            'url': url,
            'format': format_type,
            'quality': quality,
            'is_playlist': is_playlist,
            'status': 'pending',
            'progress': 0,
            'created_at': time.time(),
            'session_id': session_id
        }

    # Start background thread with appropriate job function
    if is_playlist:
        thread = threading.Thread(
            target=run_playlist_download_job,
            args=(job_id, url, format_type, quality, session_id)
        )
    else:
        thread = threading.Thread(
            target=run_download_job,
            args=(job_id, url, format_type, quality, session_id)
        )

    thread.daemon = True
    thread.start()

    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "started"
    })

@app.route('/api/download/<job_id>/status')
@limiter.limit("60 per minute")
def download_status(job_id):
    """Get download job status"""
    with jobs_lock:
        job = download_jobs.get(job_id)

    if not job:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({
        "success": True,
        "job": job
    })

@app.route('/api/download/<job_id>/file')
def download_file(job_id):
    """Download the completed file"""
    with jobs_lock:
        job = download_jobs.get(job_id)

    if not job or job.get('status') != 'completed':
        return jsonify({"error": "File not ready"}), 400

    filepath = job.get('filepath')
    filename = job.get('filename')

    if not filepath or not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    return send_file(
        filepath,
        as_attachment=True,
        download_name=filename
    )

@app.route('/api/jobs')
@limiter.limit("30 per minute")
def list_jobs():
    """List recent download jobs"""
    with jobs_lock:
        jobs = list(download_jobs.values())
    return jsonify({"jobs": jobs})

# Cookie authentication endpoints
@app.route('/api/cookies', methods=['POST'])
@limiter.limit("10 per minute")
def upload_cookies():
    """Upload browser cookies for accessing member-only/private content"""
    try:
        # Get cookie content from request
        if request.content_type and 'multipart/form-data' in request.content_type:
            # File upload
            if 'cookies' not in request.files:
                return jsonify({"error": "No cookie file provided"}), 400

            file = request.files['cookies']
            if file.filename == '':
                return jsonify({"error": "No file selected"}), 400

            # Read file content
            cookie_content = file.read().decode('utf-8')
        else:
            # Text upload
            data = request.get_json() or {}
            cookie_content = data.get('cookies', '')

        if not cookie_content or len(cookie_content) < 100:
            return jsonify({"error": "Invalid cookie file. Please upload a valid Netscape format cookies.txt file."}), 400

        # Validate cookie format (Netscape format)
        if '# Netscape HTTP Cookie File' not in cookie_content and '# HTTP Cookie File' not in cookie_content:
            # Try to detect platform from content
            platforms = detect_platforms_from_cookies(cookie_content)
            if not platforms:
                return jsonify({
                    "error": "Invalid cookie format. Expected Netscape format cookies.txt file.",
                    "instructions": "Export cookies using a browser extension like 'Get cookies.txt' for Chrome/Firefox"
                }), 400
        else:
            platforms = detect_platforms_from_cookies(cookie_content)

        # Generate or get session ID
        session_id = request.cookies.get('session_id')
        if not session_id:
            session_id = str(uuid.uuid4())[:16]

        # Save cookies securely
        result = save_user_cookies(session_id, cookie_content, platforms)

        response = jsonify({
            "success": True,
            "message": f"Cookies uploaded successfully for: {', '.join(platforms)}",
            "platforms": platforms,
            "expires_in": result['expires_in']
        })

        # Set session cookie
        response.set_cookie(
            'session_id',
            session_id,
            httponly=True,
            secure=False,  # Set to True if using HTTPS
            samesite='Lax',
            max_age=7200  # 2 hours
        )

        return response

    except Exception as e:
        print(f"[cookies] Error: {e}")
        return jsonify({"error": "Failed to process cookies. Please try again."}), 500

def detect_platforms_from_cookies(cookie_content: str) -> list:
    """Detect which platforms are in the cookie file"""
    content_lower = cookie_content.lower()
    platforms = []

    platform_indicators = {
        'youtube': ['youtube', 'google', 'youtu.be'],
        'instagram': ['instagram', 'instagram.com'],
        'tiktok': ['tiktok', 'tiktok.com'],
        'twitter': ['twitter', 'x.com', 'tweetdeck'],
        'facebook': ['facebook', 'fb.com', 'fb.watch'],
        'patreon': ['patreon'],
        'vimeo': ['vimeo']
    }

    for platform, indicators in platform_indicators.items():
        if any(ind in content_lower for ind in indicators):
            platforms.append(platform)

    return platforms if platforms else ['unknown']

@app.route('/api/cookies/status', methods=['GET'])
def cookie_status():
    """Check authentication status"""
    session_id = request.cookies.get('session_id') or request.headers.get('X-Session-ID')

    if not session_id:
        return jsonify({
            "authenticated": False,
            "platforms": [],
            "expires_in": None
        })

    with _cookies_lock:
        if session_id not in _user_cookies:
            return jsonify({
                "authenticated": False,
                "platforms": [],
                "expires_in": None
            })

        cookie_data = _user_cookies[session_id]
        remaining = int(cookie_data['expires_at'] - time.time())
        hours_remaining = remaining // 3600
        minutes_remaining = (remaining % 3600) // 60

        return jsonify({
            "authenticated": True,
            "platforms": cookie_data['platforms'],
            "expires_in": f"{hours_remaining}h {minutes_remaining}m",
            "expires_seconds": remaining
        })

@app.route('/api/cookies', methods=['DELETE'])
def clear_cookies_endpoint():
    """Clear stored cookies"""
    session_id = request.cookies.get('session_id') or request.headers.get('X-Session-ID')

    if session_id:
        clear_user_cookies(session_id)

    response = jsonify({"success": True, "message": "Cookies cleared"})
    response.set_cookie('session_id', '', expires=0)
    return response

@app.route('/api/cookies/instructions', methods=['GET'])
def cookie_instructions():
    """Get instructions for extracting cookies from each platform"""
    return jsonify({
        "general": {
            "description": "Export cookies in Netscape format using browser extensions",
            "recommended_extensions": [
                "Chrome: Get cookies.txt LOCALLY",
                "Firefox: cookies.txt",
                "Edge: Export cookies"
            ],
            "warning": "Cookies contain sensitive session data. Only use trusted extensions."
        },
        "platforms": {
            "youtube": {
                "steps": [
                    "Sign in to YouTube with a Premium or channel member account",
                    "Install 'Get cookies.txt LOCALLY' extension",
                    "Open YouTube.com",
                    "Click the extension and export cookies",
                    "Upload the file here"
                ],
                "note": "YouTube Premium membership is required for member-only content",
                "supported_content": ["Premium videos", "Channel memberships", "Age-restricted content"]
            },
            "instagram": {
                "steps": [
                    "Sign in to Instagram",
                    "Install 'Get cookies.txt LOCALLY' extension",
                    "Open Instagram.com",
                    "Click the extension and export cookies",
                    "Upload the file here"
                ],
                "note": "Private account following is required for private content",
                "supported_content": ["Private posts", "Stories from followed accounts"]
            },
            "patreon": {
                "steps": [
                    "Sign in to Patreon",
                    "Install 'Get cookies.txt LOCALLY' extension",
                    "Open Patreon.com",
                    "Click the extension and export cookies",
                    "Upload the file here"
                ],
                "note": "Active pledge/subscription required for creator content",
                "supported_content": ["Patreon-only posts", "Video attachments", "Audio files"]
            }
        }
    })

@app.route('/api/pause', methods=['POST'])
def pause():
    current_state["paused"] = True
    broadcast_state()
    return jsonify({"success": True, **current_state})

@app.route('/api/resume', methods=['POST'])
def resume():
    current_state["paused"] = False
    broadcast_state()
    return jsonify({"success": True, **current_state})

@app.route('/api/stop', methods=['POST'])
def stop():
    current_state.update({
        "playing": False,
        "paused": False,
        "title": None,
        "url": None,
        "audio_url": None,
        "video_url": None,
        "duration": 0,
        "current_time": 0,
        "thumbnail": "",
        "uploader": "",
        "platform": "",
        "format": "video",
        "quality": "best"
    })
    broadcast_state()
    return jsonify({"success": True, **current_state})

@app.route('/api/seek', methods=['POST'])
def seek():
    data = request.get_json()
    time_pos = data.get('time', 0)
    current_state["current_time"] = time_pos
    broadcast_state()
    return jsonify({"success": True, **current_state})

@app.route('/api/state')
def get_state():
    return jsonify(current_state)

@sock.route('/ws')
def websocket(ws):
    """WebSocket for real-time updates"""
    clients.add(ws)
    try:
        ws.send(json.dumps(current_state))

        while True:
            data = ws.receive()
            if data:
                try:
                    msg = json.loads(data)
                    if msg.get('type') == 'time_update':
                        current_state["current_time"] = msg.get('time', 0)
                except json.JSONDecodeError:
                    pass
    except:
        pass
    finally:
        clients.discard(ws)

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🎵 MediaPull - Universal Media Extraction Platform")
    print("="*60)
    print("Open: http://localhost:5000")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=5000, debug=True)
