const { createSocket } = require('dgram');

const server = createSocket('udp4');
const HEADER = 'ITS';
const PACKET_SIZE = 11;
let previousPacketTime;

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

const PORT = 41234;
const HOST = '0.0.0.0';
server.bind(PORT, HOST);
