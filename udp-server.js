// Development UDP peer: logs velocity commands and streams the current Linux
// system battery percentage to the latest valid sender at 50 Hz.

const { createSocket } = require('node:dgram');
const fs = require('node:fs/promises');

const server = createSocket('udp4');
const HEADER = 'ITS';
const PACKET_SIZE = 11;
const BATTERY_PACKET_SIZE = 7;
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
        const packet = Buffer.allocUnsafe(BATTERY_PACKET_SIZE);
        packet.write(HEADER, 0, 3, 'ascii');
        packet.writeInt32LE(batteryPercentage, 3);
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
    if (msg.length !== PACKET_SIZE) {
        console.warn(
            `Ignored ${msg.length}-byte packet from ${rinfo.address}:${rinfo.port}; expected ${PACKET_SIZE} bytes.`,
        );
        return;
    }

    const header = msg.toString('ascii', 0, 3);
    if (header !== HEADER) {
        console.warn(
            `Ignored packet from ${rinfo.address}:${rinfo.port}; expected header "${HEADER}", received "${header}".`,
        );
        return;
    }

    const currentPacketTime = process.hrtime.bigint();
    const elapsedMilliseconds = previousPacketTime === undefined
        ? null
        : Number(currentPacketTime - previousPacketTime) / 1_000_000;
    previousPacketTime = currentPacketTime;

    const yVelocity = msg.readFloatLE(3);
    const thetaVelocity = msg.readFloatLE(7);
    latestClient = { address: rinfo.address, port: rinfo.port };

    console.log(
        `Received ${header} from ${rinfo.address}:${rinfo.port}: `
        + `Y=${yVelocity}, theta=${thetaVelocity}, `
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
