const express = require('express');
const { spawn } = require('child_process');
const ffmpegStatic = require('ffmpeg-static');

const app = express();

// Development-only tool that mimics a robot IP camera by serving an MJPEG
// stream over HTTP at /video. This matches what the app's <img> element
// expects, so you can point the camera URL at http://<this-host>:8080/video.

// Prefer the ffmpeg binary bundled with the project (ffmpeg-static). Allow an
// override via FFMPEG_PATH for a system ffmpeg if you need one.
const FFMPEG_PATH = process.env.FFMPEG_PATH || ffmpegStatic || '/usr/lib/jellyfin-ffmpeg/ffmpeg ' || 'ffmpeg';

const PORT = Number(process.env.PORT) || 8080;

// By default we generate a synthetic test pattern so no webcam hardware is
// required. Set CAMERA_DEVICE=/dev/video0 (or any v4l2 device) to stream a
// real camera instead.
const CAMERA_DEVICE = process.env.CAMERA_DEVICE || '';

// Build the ffmpeg input arguments for either a real device or a test source.
function buildInputArgs() {
    if (CAMERA_DEVICE) {
        return ['-f', 'v4l2', '-i', CAMERA_DEVICE];
    }

    // lavfi testsrc2 produces a moving test pattern with a running timer,
    // which is ideal for mimicking a live camera feed without hardware.
    return [
        '-re',
        '-f', 'lavfi',
        '-i', 'testsrc2=size=1280x720:rate=15',
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

    // Spawn FFmpeg to encode the selected input as an MJPEG multipart stream.
    const ffmpeg = spawn(FFMPEG_PATH, [
        ...buildInputArgs(),
        '-c:v', 'mjpeg',
        '-q:v', '5',
        '-f', 'mpjpeg',
        'pipe:1'
    ]);

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
