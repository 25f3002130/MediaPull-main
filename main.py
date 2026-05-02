"""
MediaPull - Universal Media Extraction Platform

A production-grade media downloader supporting YouTube, Instagram, TikTok, Twitter/X, and Facebook.

Features:
- Stream video/audio directly in browser with quality selection
- Background download jobs with progress tracking
- Format selection (720p, 1080p, audio bitrate)
- Rate limiting and abuse prevention
- Real-time WebSocket updates
- Handles platform restrictions with meaningful error messages

Architecture:
- Flask backend with async download workers
- In-memory job queue with threading
- WebSocket for real-time player state sync
- yt-dlp for reliable media extraction
"""

from app import app

if __name__ == "__main__":
    import socket

    # Get local IP for sharing
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = "localhost"

    print("\n" + "="*60)
    print("🎬 MediaPull - Universal Media Extraction Platform")
    print("="*60)
    print(f"Local:   http://localhost:5000")
    print(f"Network: http://{local_ip}:5000")
    print("="*60)
    print("Supported platforms:")
    print("  • YouTube")
    print("  • Instagram")
    print("  • TikTok")
    print("  • Twitter/X")
    print("  • Facebook")
    print("="*60)
    print("Paste a video URL to analyze and extract!")
    print("="*60 + "\n")

    app.run(host='0.0.0.0', port=5000, debug=True)
