# MediaPull

**Universal Media Extraction Platform**

A production-grade media downloader and streaming platform supporting YouTube, Instagram, TikTok, Twitter/X, and Facebook.

---

## The Problem

Content is fragmented across platforms. Every major platform (YouTube, Instagram, TikTok) has its own ecosystem with different:

- **Access patterns**: Some require auth, others block scraping
- **Format structures**: Video/audio separation, adaptive bitrate, DRM
- **Rate limiting and anti-bot measures**: Each platform implements different protections
- **Tooling**: Users need separate tools for each platform

MediaPull solves this with a **unified extraction layer** that handles platform differences transparently.

---

## What Makes This Interesting

This isn't a simple "wrap yt-dlp in a UI" project. The complexity lies in:

### Platform Abstraction Layer

Each platform has unique extraction challenges:

- **YouTube**: Requires format selection between DASH streams (separate video/audio) vs progressive download
- **Instagram**: Often requires session cookies, handles stories vs posts vs reels differently
- **TikTok**: Heavy bot protection, frequently changing API signatures
- **Twitter/X**: M3U8 stream handling, rate limiting aggressive
- **Facebook**: Login walls for many videos, different URL patterns for watch vs share links

The system abstracts these differences behind a consistent API.

### Async Download Architecture

Downloads run in background worker threads to prevent blocking the main thread:

- **Job Queue**: Each download spawns an isolated job with unique ID
- **Progress Tracking**: Real-time progress polling via REST API
- **State Management**: Thread-safe job storage with mutex locks
- **Cleanup**: Jobs auto-expire after 24 hours to prevent memory bloat

### Format Negotiation

The system extracts *available* formats per URL and presents options:

- **Video**: Dynamic quality selection based on what's actually available (some videos cap at 720p)
- **Audio**: Bitrate selection with fallbacks if exact match unavailable
- **Stream URLs**: Direct media URLs extracted for instant browser playback without full download

### Error Handling & Resilience

Not just try/catch—meaningful error categorization:

| Error Type | Detection Pattern | User Message |
|------------|------------------|--------------|
| Private/Auth | "sign in", "private", "login" | "This video is private or requires authentication" |
| Member-Only | "member", "premium", "subscription" | "This is member-only content. Please upload cookies for access" |
| Region Block | "region", "country", "blocked" | "This video is region-restricted in your area" |
| Copyright | "copyright", "DMGA", "violation" | "This video has been removed due to copyright claims" |
| Age Restricted | "age", "adults" | "This video is age-restricted" |
| Parse Fail | "unable to extract", "parse" | "Could not parse video information. Platform may have changed" |
| Timeout | "timeout", "time out" | "Request timed out. The video may be too large or the platform slow" |

This matters because platforms change their anti-scraping measures frequently. The system degrades gracefully.

### Rate Limiting & Abuse Prevention

Built-in Flask-Limiter integration:

- **General**: 100 requests/hour, 10/minute per IP
- **Play endpoint**: 20/minute (stream operations)
- **Download endpoint**: 5/minute (expensive operations)

Prevents abuse while allowing legitimate use.

---

## Architecture

```
┌─────────────────────────────────────────┐
│           Client (Browser)            │
│  ┌─────────────┐    ┌───────────────┐ │
│  │  Player UI  │    │ Download Jobs │ │
│  └──────┬──────┘    └───────┬───────┘ │
└─────────┼───────────────────┼─────────┘
          │                   │
          ▼                   ▼
┌─────────────────────────────────────────┐
│           Flask Backend                 │
│  ┌─────────────┐    ┌───────────────┐ │
│  │   Routes    │◄──►│  Rate Limiter │ │
│  └──────┬──────┘    └───────────────┘ │
│         │                               │
│  ┌──────┴──────┐    ┌───────────────┐ │
│  │   WS Server │    │ Download Jobs │ │
│  │  (State Sync)│    │  (Threading)  │ │
│  └─────────────┘    └───────┬───────┘ │
└─────────────────────────────┼───────────┘
                              │
                    ┌─────────┴──────────┐
                    │      yt-dlp        │
                    │ (Media Extraction) │
                    └────────────────────┘
```

### Tech Stack

- **Backend**: Flask (Python)
- **Real-time**: Flask-Sock (WebSocket)
- **Rate Limiting**: Flask-Limiter
- **Media Extraction**: yt-dlp
- **Frontend**: Vanilla JavaScript + CSS Grid/Flexbox
- **Concurrency**: Python threading with job queue

---

## Features

### Current

- ✅ **Stream Preview**: Play video/audio directly in browser before downloading
- ✅ **Quality Selection**: Dynamic format options per URL (720p, 1080p, audio bitrate)
- ✅ **Background Downloads**: Async job queue with progress tracking
- ✅ **Multi-Platform**: YouTube, Instagram, TikTok, Twitter/X, Facebook
- ✅ **Error Handling**: Meaningful error messages for each failure mode
- ✅ **Rate Limiting**: Abuse prevention without blocking legitimate users
- ✅ **Real-time State**: WebSocket sync between multiple clients
- ✅ **Cookie Authentication**: Access member-only, private, and age-restricted content
- ✅ **Playlist Downloads**: Download entire playlists with automatic folder organization

### Planned

- 🔄 **Playlist/Batch Downloads**: Queue multiple items with aggregate progress
- 🔄 **Metadata Export**: Save video info as JSON alongside downloads
- 🔄 **Thumbnail Caching**: Local cache to reduce redundant requests

---

## Running Locally

### Prerequisites

- Python 3.8+
- yt-dlp (`pip install yt-dlp` or `brew install yt-dlp`)
- FFmpeg (for audio extraction and format conversion)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd mediapull

# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py
```

The server will start on `http://localhost:5000`.

---

## Cookie Authentication

MediaPull now supports accessing member-only, private, and age-restricted content through secure cookie authentication.

### Supported Protected Content

| Platform | Content Type | Requirements |
|----------|-------------|--------------|
| YouTube | Premium videos, Channel memberships, Age-restricted | YouTube Premium or channel membership |
| Instagram | Private posts, Stories from followed accounts | Must follow the private account |
| Patreon | Creator posts with video/audio | Active pledge/subscription |
| Facebook | Videos behind login wall | Valid Facebook session |
| Twitter/X | Age-restricted/sensitive content | Logged-in session |

### Security Features

- **Memory-only storage**: Cookies are encrypted in memory and never written to disk
- **Auto-expiration**: Cookies expire after 2 hours automatically
- **No logging**: Cookie data is never logged or exposed in error messages
- **Session isolation**: Each browser session has isolated cookie storage

### How to Use

1. **Export cookies** from your browser using an extension like "Get cookies.txt LOCALLY"
2. **Click "Add Cookies"** in the MediaPull interface
3. **Upload the cookies.txt file** or paste the content
4. **Access member-only content** by pasting the restricted URL

### Cookie Extraction Steps

1. Install the [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahboknnkkbmhdgfgjokddgplp) extension
2. Sign in to the platform (YouTube, Instagram, Patreon, etc.)
3. Navigate to the platform's main page
4. Click the extension icon and select "Export" → "Export as JSON"
5. Upload the downloaded file to MediaPull

**Note**: Cookies are sensitive session data. Only use trusted extensions and avoid sharing cookie files.

### Environment Variables

Create a `.env` file (optional):

```env
# Optional: Redis for distributed rate limiting (defaults to memory)
REDIS_URL=redis://localhost:6379/0
```

**Download Location**: Files are saved to your system's Downloads folder: `~/Downloads/MediaPull/`

---

## API Reference

### Endpoints

| Method | Endpoint | Description | Rate Limit |
|--------|----------|-------------|------------|
| POST | `/api/analyze` | Analyze URL (detects playlist vs single video) | 10/min |
| POST | `/api/formats` | Get available formats for single video URL | 10/min |
| POST | `/api/play` | Start streaming playback | 20/min |
| POST | `/api/download` | Start background download (video or playlist) | 5/min |
| GET | `/api/download/{id}/status` | Check job status | 60/min |
| GET | `/api/download/{id}/file` | Download completed file | - |
| GET | `/api/jobs` | List recent download jobs | 30/min |
| POST | `/api/stop` | Stop current playback | - |
| **POST** | **`/api/cookies`** | **Upload cookies for auth** | 10/min |
| **GET** | **`/api/cookies/status`** | **Check auth status** | - |
| **DELETE** | **`/api/cookies`** | **Clear cookies** | - |
| **GET** | **`/api/cookies/instructions`** | **Get cookie extraction help** | - |
| WS | `/ws` | Real-time state sync | - |

### Example: Fetch Formats

```bash
curl -X POST http://localhost:5000/api/formats \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=..."}'
```

Response:

```json
{
  "success": true,
  "title": "Video Title",
  "duration": 360,
  "platform": "YouTube",
  "video_formats": [
    {"quality_label": "1080p", "height": 1080, "ext": "mp4"},
    {"quality_label": "720p", "height": 720, "ext": "mp4"}
  ],
  "audio_formats": [
    {"abr": 128, "ext": "m4a"}
  ]
}
```

### Example: Start Download

```bash
curl -X POST http://localhost:5000/api/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://...", "format": "video", "quality": "720p"}'
```

Response:

```json
{
  "success": true,
  "job_id": "abc123",
  "status": "started"
}
```

### Example: Download Playlist

```bash
curl -X POST http://localhost:5000/api/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://youtube.com/playlist?list=PL...", "format": "video", "quality": "1080p", "is_playlist": true}'
```

Response:

```json
{
  "success": true,
  "job_id": "xyz789",
  "status": "started"
}
```

All videos will be saved to `~/Downloads/MediaPull/{PlaylistName}_{timestamp}/`.

### Example: Upload Cookies

```bash
curl -X POST http://localhost:5000/api/cookies \
  -F "cookies=@/path/to/cookies.txt"
```

Response:

```json
{
  "success": true,
  "message": "Cookies uploaded successfully for: youtube, patreon",
  "platforms": ["youtube", "patreon"],
  "expires_in": "2 hours"
}
```

---

## Challenges & Solutions

### Challenge: Platform Anti-Scraping

**Problem**: YouTube and Instagram actively detect and block automated access.

**Solution**: yt-dlp handles most of this, but we add:
- Request timeouts (prevent hanging on blocked requests)
- User-agent rotation (via yt-dlp defaults)
- Meaningful error categorization (so users know why something failed)

### Challenge: Format Complexity

**Problem**: Modern video platforms use adaptive bitrate streaming (DASH/HLS) where video and audio are separate streams.

**Solution**: 
- Extract both streams and present unified options
- For browser playback, let the browser handle the merging
- For downloads, use yt-dlp's merge capability (requires FFmpeg)

### Challenge: Concurrent Downloads

**Problem**: Multiple simultaneous downloads can exhaust system resources or trigger platform rate limits.

**Solution**:
- Thread-per-download model (simple, works for low-medium concurrency)
- Rate limiting at API level
- Future: Implement job queue with worker pool

---

## Known Limitations

1. **Legal/ToS Constraints**: This tool cannot be hosted publicly due to platform Terms of Service and potential DMCA issues. It is intended for personal use only.

2. **Platform Changes**: When platforms update their APIs or anti-scraping measures, extraction may break until yt-dlp updates.

3. **Private Content**: Videos requiring authentication (private Instagram accounts, age-restricted YouTube) cannot be accessed without user-provided cookies (not yet implemented).

4. **Live Streams**: Currently unsupported (different extraction path).

---

## Future Roadmap

### Playlist Downloads (Priority: High) - ✅ IMPLEMENTED

Support for downloading entire playlists from YouTube and other platforms:

- **Automatic folder creation**: Each playlist creates a dedicated folder in `~/Downloads/MediaPull/`
- **Folder naming**: `{PlaylistTitle}_{timestamp}/` to avoid conflicts
- **Progress tracking**: Real-time count of videos downloaded
- **Partial failure handling**: Continues downloading if individual videos fail
- **Format consistency**: All videos downloaded in selected quality (best, 1080p, 720p, etc.)

Usage: Paste a playlist URL (e.g., `https://youtube.com/playlist?list=PL...`) and click "Download All Videos".

### Authentication Layer (Priority: Medium) - ✅ IMPLEMENTED

Support for user-provided cookies to access:

- Private Instagram content
- Age-restricted YouTube videos
- Facebook videos behind login wall
- YouTube Channel Memberships/Premium
- Patreon content

Features secure in-memory encrypted storage with automatic expiration.

### Distributed Rate Limiting (Priority: Low)

Current rate limiting is in-memory (per instance). For multi-instance deployments:

- Redis-backed rate limiting
- Shared job queue across instances

---

## Project Structure

```
.
├── app.py                 # Main Flask application (backend + API)
├── main.py               # Entry point with server startup
├── requirements.txt     # Python dependencies
├── README.md           # This file
└── templates/
    └── player.html     # Frontend UI
```

**Note**: Downloaded files are saved to `~/Downloads/MediaPull/` (your system's Downloads folder), not the project directory.

**Playlist Downloads**: When downloading a playlist, a folder named `{PlaylistTitle}_{timestamp}` is automatically created inside `~/Downloads/MediaPull/`, and all videos from that playlist are saved there.

---

## License

This project is for educational purposes. Users are responsible for complying with platform Terms of Service and applicable copyright laws.

---

## Acknowledgments

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - The core extraction library
- [Flask](https://flask.palletsprojects.com/) - Web framework

---

*Not publicly hosted due to platform ToS constraints. Demo available on request.*
