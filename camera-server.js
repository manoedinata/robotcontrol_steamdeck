const express = require('express');
const { spawn } = require('child_process');
const ffmpegStatic = require('ffmpeg-static');

const app = express();

// Development-only tool that mimics a robot IP camera by serving an MJPEG
// stream over HTTP at /video. This matches what the app's <img> element
// expects, so you can point the camera URL at http://<this-host>:8080/video.

// Prefer the ffmpeg binary bundled with the project (ffmpeg-static). Allow an
// override via FFMPEG_PATH for a system ffmpeg if you need one.
const FFMPEG_PATH = '/usr/lib/jellyfin-ffmpeg/ffmpeg' || 'ffmpeg' || process.env.FFMPEG_PATH || ffmpegStatic;

const PORT = Number(process.env.PORT) || 8080;

// By default we generate a synthetic test pattern so no webcam hardware is
// required. Set CAMERA_DEVICE=/dev/video0 (or any v4l2 device) to stream a
// real camera instead.
const CAMERA_DEVICE = process.env.CAMERA_DEVICE || '';

// Capture geometry for a real device. Most UVC webcams support MJPEG natively
// at these values; adjust with CAMERA_SIZE / CAMERA_FRAMERATE if yours differs.
const CAMERA_SIZE = process.env.CAMERA_SIZE || '640x360';
const CAMERA_FRAMERATE = process.env.CAMERA_FRAMERATE || '30';

// Build the full ffmpeg argument list for either a real device or a test source.
function buildFfmpegArgs() {
    if (CAMERA_DEVICE) {
        // Ask the webcam for its native MJPEG stream and copy it straight
        // through. Re-encoding raw YUYV to MJPEG on the CPU is the usual cause
        // of lag, so avoiding the transcode keeps the feed smooth.
        return [
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
            '-f', 'v4l2',
            '-input_format', 'mjpeg',
            '-video_size', CAMERA_SIZE,
            '-framerate', CAMERA_FRAMERATE,
            '-i', CAMERA_DEVICE,
            '-c:v', 'copy',
            '-f', 'mpjpeg',
            'pipe:1',
        ];
    }

    // lavfi testsrc2 produces a moving test pattern with a running timer,
    // which is ideal for mimicking a live camera feed without hardware.
    return [
        '-re',
        '-f', 'lavfi',
        '-i', 'testsrc2=size=1280x720:rate=15',
        '-c:v', 'mjpeg',
        '-q:v', '5',
        '-f', 'mpjpeg',
        'pipe:1',
    ];
}

// Enable CORS so the stream can be consumed from any origin during development.
app.use((req, res, next) => {
    res.header('Access-Control-Allow-Origin', '*');
    next();
});

app.get('/video', (req, res) => {
    console.log('Client connected to stream.');

    // MUST MATCH FFMPEG'S DEFAULT MPJPEG BOUNDARY ('ffmpeg').
    res.writeHead(200, {
        'Cache-Control': 'no-store, no-cache, must-revalidate, pre-check=0',
        'Pragma': 'no-cache',
        'Connection': 'close',
        'Content-Type': 'multipart/x-mixed-replace; boundary=ffmpeg'
    });

    // Spawn FFmpeg to serve the selected input as an MJPEG multipart stream.
    const ffmpeg = spawn(FFMPEG_PATH, buildFfmpegArgs());

    // Pipe stdout directly to the HTTP response.
    ffmpeg.stdout.pipe(res);

    // Log errors to the terminal for debugging.
    ffmpeg.stderr.on('data', (data) => {
        console.error(`[FFmpeg]: ${data.toString()}`);
    });

    ffmpeg.on('error', (err) => {
        console.error('Failed to start FFmpeg:', err);
        res.end();
    });

    ffmpeg.on('close', (code) => {
        console.log(`FFmpeg process exited with code ${code}`);
        res.end();
    });

    // Handle client disconnect (e.g. page refresh / hot reload).
    req.on('close', () => {
        console.log('Client disconnected, terminating FFmpeg instance.');
        ffmpeg.kill('SIGKILL');
    });
});

app.listen(PORT, '0.0.0.0', () => {
    const source = CAMERA_DEVICE ? `device ${CAMERA_DEVICE}` : 'synthetic test pattern';
    console.log(`Mock camera server (${source}) running on http://0.0.0.0:${PORT}/video`);
    console.log(`Using ffmpeg at: ${FFMPEG_PATH}`);
});
