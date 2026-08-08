// Development UDP peer: logs velocity commands and streams the current Linux
// system battery percentage to the latest valid sender at 50 Hz.

const { createSocket } = require('node:dgram');
const fs = require('node:fs/promises');
const path = require('node:path');
const { loadPacketCodec } = require('./electron-components/packet-schema');

const server = createSocket('udp4');
// Shared JSON-driven packet layout, identical to the one the app sends/receives.
const codec = loadPacketCodec(path.join(__dirname, 'packet-schema.json'));
const batteryFieldName = codec.fieldNameForRole('reply', 'batteryPercentage');
const yVelocityFieldName = codec.fieldNameForRole('command', 'yVelocity');
const thetaVelocityFieldName = codec.fieldNameForRole('command', 'thetaVelocity');
const BATTERY_SEND_INTERVAL_MS = 20;
const BATTERY_CACHE_MS = 1000;
const POWER_SUPPLY_PATH = '/sys/class/power_supply';
let previousPacketTime;
let latestClient;
let cachedBatteryPercentage;
let batteryCacheTime = 0;
let batteryReadPromise;
let batteryReadWarningShown = false;
let batterySendInFlight = false;

async function readBatteryPercentage() {
    const now = Date.now();
    if (cachedBatteryPercentage !== undefined && now - batteryCacheTime < BATTERY_CACHE_MS) {
        return cachedBatteryPercentage;
    }
    if (batteryReadPromise) return batteryReadPromise;

    batteryReadPromise = (async () => {
        const entries = await fs.readdir(POWER_SUPPLY_PATH, { withFileTypes: true });
        const batteryEntries = entries.filter((entry) => entry.name.startsWith('BAT'));

        for (const entry of batteryEntries) {
            try {
                const capacity = await fs.readFile(`${POWER_SUPPLY_PATH}/${entry.name}/capacity`, 'utf8');
                const percentage = Number.parseInt(capacity.trim(), 10);
                if (Number.isInteger(percentage) && percentage >= 0 && percentage <= 100) {
                    cachedBatteryPercentage = percentage;
                    batteryCacheTime = Date.now();
                    return percentage;
                }
            } catch {
                // Try the next battery exposed by the system.
            }
        }

        throw new Error('No readable system battery capacity was found.');
    })();

    try {
        return await batteryReadPromise;
    } finally {
        batteryReadPromise = undefined;
    }
}

async function sendBatteryPercentage() {
    if (!latestClient || batterySendInFlight) return;

    const destination = latestClient;
    batterySendInFlight = true;
    try {
        const batteryPercentage = await readBatteryPercentage();
        const packet = codec.encodeReply(
            batteryFieldName ? { [batteryFieldName]: batteryPercentage } : {},
        );
        await new Promise((resolve, reject) => {
            server.send(packet, destination.port, destination.address, (error) => {
                if (error) reject(error);
                else resolve();
            });
        });
        batteryReadWarningShown = false;
    } catch (error) {
        if (!batteryReadWarningShown) {
            console.warn(`Unable to send battery percentage: ${error.message}`);
            batteryReadWarningShown = true;
        }
    } finally {
        batterySendInFlight = false;
    }
}

server.on('message', (msg, rinfo) => {
    const command = codec.decodeCommand(msg);
    if (!command) {
        console.warn(
            `Ignored ${msg.length}-byte packet from ${rinfo.address}:${rinfo.port}; `
            + `expected ${codec.command.size}-byte "${codec.command.header}" command.`,
        );
        return;
    }

    const currentPacketTime = process.hrtime.bigint();
    const elapsedMilliseconds = previousPacketTime === undefined
        ? null
        : Number(currentPacketTime - previousPacketTime) / 1_000_000;
    previousPacketTime = currentPacketTime;

    latestClient = { address: rinfo.address, port: rinfo.port };

    // Report every decoded field so custom schema entries are visible too, while
    // still highlighting the well-known Y/theta velocities when present.
    const yVelocity = yVelocityFieldName ? command[yVelocityFieldName] : undefined;
    const thetaVelocity = thetaVelocityFieldName ? command[thetaVelocityFieldName] : undefined;
    const fieldSummary = Object.entries(command)
        .map(([name, value]) => `${name}=${value}`)
        .join(', ');

    console.log(
        `Received ${codec.command.header} from ${rinfo.address}:${rinfo.port}: `
        + `${fieldSummary || `Y=${yVelocity}, theta=${thetaVelocity}`}, `
        + `interval=${elapsedMilliseconds === null ? 'first packet' : `${elapsedMilliseconds.toFixed(3)} ms`}`,
    );

});

server.on('error', (err) => {
    console.error(`Server error:\n${err.stack}`);
    server.close();
});

server.on('listening', () => {
    const address = server.address();
    console.log(`UDP Server listening on ${address.address}:${address.port}`);
});

const batterySendTimer = setInterval(() => {
    void sendBatteryPercentage();
}, BATTERY_SEND_INTERVAL_MS);
batterySendTimer.unref();

server.on('close', () => {
    clearInterval(batterySendTimer);
});

const PORT = 41234;
const HOST = '0.0.0.0';
server.bind(PORT, HOST);
